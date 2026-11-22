"""Durable Freenet download queue. The node owns partial data, not GUI sockets."""

from __future__ import annotations

import copy
from collections import deque
import json
import os
import queue
from pathlib import Path
import threading
import time
import uuid

from .key_parser import filename_for
from .node_protocol import FCPConnection, restart_node_request
from .paths import DOWNLOAD_DIR, STATE_PATH
from .queue_store import QueueStore


class DownloadManager:
    DEFAULT_WINDOW_PREFERENCES = {"width": 1280, "height": 650, "maximized": False}
    DEFAULT_SORT_PREFERENCES = {"column": "id", "descending": False, "running_first": False}
    SORT_PREFERENCE_COLUMNS = {"id", "file", "size", "speed", "status", "progress", "elapsed", "eta"}

    def __init__(self, state_path=STATE_PATH, directory=DOWNLOAD_DIR, host="127.0.0.1", port=9481,
                 notify=lambda tasks: None, autostart=True, reconnect_delay=1, web_base="http://127.0.0.1:8888"):
        self.store = QueueStore(state_path)
        self.directory = Path(directory).resolve()
        self.parts = self.directory / ".fld-parts"
        self.parts.mkdir(parents=True, exist_ok=True)
        self.tasks = self.store.load()
        self.column_widths = self._load_column_widths(
            self.store.get_metadata("column_widths_v2"), self.store.get_metadata("column_widths"))
        self.sort_preferences = self._valid_sort_preferences(self.store.get_metadata("sort_preferences"))
        self.window_preferences = self._valid_window_preferences(self.store.get_metadata("window_preferences"))
        self._migrate_tasks()
        for task in self.tasks.values():
            task.setdefault("elapsed", 0.0)
            task.setdefault("timer_known", task["desired"] != "done")
            task.setdefault("active_since", time.time() if task["desired"] == "running" else None)
            task["eta_speed"] = 0
        self.host, self.port, self.notify = host, port, notify
        self.reconnect_delay = reconnect_delay
        self.web_base = web_base
        self.retrying = set()
        self.retry_results = queue.Queue()
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.closing = threading.Event()
        self.close_deadline = float("inf")
        self.wake = threading.Event()
        self.connection = None
        self.known = {}
        self.sent = {}
        self.verify_removals = set()
        self.failed = set()
        self.completed = {}
        self.samples = {}
        self.eta_samples = {}
        self.outbox = deque()
        self.dirty = False
        self.last_save = self.last_notify = 0
        self.thread = None
        if autostart:
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()

    @staticmethod
    def _load_column_widths(raw_v2, raw_legacy):
        widths = DownloadManager._valid_width_array(raw_v2, 11)
        if widths is not None:
            return widths
        old = DownloadManager._valid_width_array(raw_legacy, (11, 12))
        if old is None:
            return None
        if len(old) == 12:
            return [old[0], *old[2:]]
        return [50, *old[1:]]

    @staticmethod
    def _valid_width_array(raw, lengths):
        if raw is None:
            return None
        try:
            widths = json.loads(raw)
            if (not isinstance(widths, list) or len(widths) not in
                    ({lengths} if isinstance(lengths, int) else set(lengths))):
                return None
            if any(isinstance(value, bool) or not isinstance(value, int) for value in widths):
                return None
            return [max(40, min(2000, value)) for value in widths]
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    @classmethod
    def _valid_sort_preferences(cls, raw):
        defaults = dict(cls.DEFAULT_SORT_PREFERENCES)
        if raw is None:
            return defaults
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(value, dict):
                return defaults
            column = value.get("column")
            descending = value.get("descending")
            running_first = value.get("running_first")
            if (column not in cls.SORT_PREFERENCE_COLUMNS or
                    not isinstance(descending, bool) or not isinstance(running_first, bool)):
                return defaults
            return {"column": column, "descending": descending, "running_first": running_first}
        except (TypeError, ValueError, json.JSONDecodeError):
            return defaults

    @classmethod
    def _valid_window_preferences(cls, raw):
        defaults = dict(cls.DEFAULT_WINDOW_PREFERENCES)
        if raw is None:
            return defaults
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(value, dict):
                return defaults
            width, height, maximized = value.get("width"), value.get("height"), value.get("maximized")
            if (isinstance(width, bool) or not isinstance(width, int) or width <= 0 or
                    isinstance(height, bool) or not isinstance(height, int) or height <= 0 or
                    not isinstance(maximized, bool)):
                return defaults
            return {"width": width, "height": height, "maximized": maximized}
        except (TypeError, ValueError, json.JSONDecodeError):
            return defaults

    def _migrate_tasks(self):
        """Add stable display IDs and conservative start history before FCP starts."""
        raw_next = self.store.get_metadata("next_display_id")
        try:
            next_saved = int(json.loads(raw_next)) if raw_next else 1
            if next_saved < 1:
                next_saved = 1
        except (ValueError, TypeError, json.JSONDecodeError):
            next_saved = 1
        valid_ids, changed = [], False
        for task in self.tasks.values():
            display_id = task.get("display_id")
            if isinstance(display_id, int) and not isinstance(display_id, bool) and display_id > 0 and display_id not in valid_ids:
                valid_ids.append(display_id)
            else:
                changed = True
        next_id = max(next_saved, max(valid_ids, default=0) + 1)
        claimed = set()
        for task in self.tasks.values():
            display_id = task.get("display_id")
            if not isinstance(display_id, int) or isinstance(display_id, bool) or display_id <= 0 or display_id in claimed:
                task["display_id"] = next_id
                next_id += 1
                changed = True
            claimed.add(task["display_id"])
            evidence = (task.get("desired", "new") not in {"new", "removed"} or
                        bool(task.get("elapsed", 0)) or task.get("active_since") is not None or
                        bool(task.get("fetched", 0)) or bool(task.get("fraction", 0)) or
                        bool(task.get("retries", 0)) or bool(task.get("path")) or
                        bool(task.get("size_final", False)) or task.get("completed_at") is not None or
                        task.get("status") == "Complete")
            if "ever_started" not in task:
                task["ever_started"] = bool(evidence)
                changed = True
            elif not task["ever_started"] and evidence:
                task["ever_started"] = True
                changed = True
            elif not isinstance(task["ever_started"], bool):
                task["ever_started"] = bool(task["ever_started"] or evidence)
                changed = True
        next_id = max(next_id, max(claimed, default=0) + 1, next_saved)
        if raw_next is None or next_id != next_saved:
            changed = True
        self.store.save(self.tasks if changed else None,
            metadata={"next_display_id": json.dumps(next_id)},
            defaults={"window_preferences": json.dumps(self.window_preferences)})
        self.next_display_id = next_id

    def set_window_preferences(self, preferences):
        value = self._valid_window_preferences(preferences)
        with self.lock:
            self.store.save(metadata={"window_preferences": json.dumps(value)})
            self.window_preferences = value

    def set_sort_preferences(self, preferences):
        value = self._valid_sort_preferences(preferences)
        with self.lock:
            self.store.save(metadata={"sort_preferences": json.dumps(value)})
            self.sort_preferences = value

    def snapshot(self):
        with self.lock:
            return copy.deepcopy([task for task in self.tasks.values() if task["desired"] != "removed"])

    def checkpoint(self, force=False):
        now = time.monotonic()
        if self.dirty and (force or now - self.last_save >= 2):
            self.store.save(self.tasks)
            self.last_save, self.dirty = now, False
        if force or now - self.last_notify >= 1:
            self.notify(self.snapshot())
            self.last_notify = now

    def add(self, keys):
        with self.lock:
            existing = {task["key"] for task in self.tasks.values() if task["desired"] != "removed"}
            for key in keys:
                if key in existing:
                    continue
                if any(ord(char) < 32 for char in key):
                    raise ValueError("Invalid Freenet key.")
                identifier = "fld-" + uuid.uuid4().hex
                self.tasks[identifier] = dict(id=identifier, display_id=self.next_display_id, ever_started=False,
                    key=key, filename=filename_for(key), desired="new",
                    status="Ready", size=None, size_final=False, fetched=0, fraction=0, speed=0,
                    selected=True, path=None, error="", retry_at=0, retries=0,
                    elapsed=0.0, active_since=None, timer_known=True)
                self.tasks[identifier]["eta_speed"] = 0
                self.next_display_id += 1
                existing.add(key)
            self.dirty = True
            self.store.save(self.tasks, metadata={"next_display_id": json.dumps(self.next_display_id)})
            self.dirty = False
            self.last_save = time.monotonic()
            self.notify(self.snapshot())
            self.last_notify = self.last_save
        self.wake.set()

    def set_column_widths(self, widths):
        if (not isinstance(widths, (list, tuple)) or len(widths) != 11 or
                any(isinstance(value, bool) or not isinstance(value, int) for value in widths)):
            raise ValueError("Unexpected number of list columns.")
        widths = [max(40, min(2000, value)) for value in widths]
        with self.lock:
            self.store.save(metadata={"column_widths_v2": json.dumps(widths)})
            self.column_widths = widths

    def action(self, identifiers, desired):
        with self.lock:
            changed = False
            for identifier in identifiers:
                task = self.tasks.get(identifier)
                if task is None or task.get("desired") == "removed":
                    continue
                if task["desired"] == "done" and desired != "removed":
                    continue
                if desired == "running":
                    task["ever_started"] = True
                self.change_state(task, desired)
                task["status"] = {"running": "Connecting...", "paused": "Pause pending...",
                                  "cancelled": "Cancel pending...", "removed": "Removing..."}[desired]
                task["speed"] = 0
                if desired == "running":
                    task["retry_at"] = 0
                    task["error"] = ""
                self.sent.pop(identifier, None)
                changed = True
            if not changed:
                return
            self.dirty = True
            self.checkpoint(True)
        self.wake.set()

    def start(self, identifiers):
        self.action(identifiers, "running")

    def pause(self, identifiers):
        self.action(identifiers, "paused")

    def cancel(self, identifiers):
        self.action(identifiers, "cancelled")

    def remove_unstarted(self):
        with self.lock:
            removed = [identifier for identifier, task in self.tasks.items()
                       if not task.get("ever_started", False) and task.get("desired") not in {"removed"}]
            for identifier in removed:
                self.tasks.pop(identifier, None)
                self.sent.pop(identifier, None)
                self.known.pop(identifier, None)
            if removed:
                self.dirty = True
                self.checkpoint(True)
        return removed

    def remove_completed(self):
        with self.lock:
            identifiers = tuple(identifier for identifier, task in self.tasks.items()
                                if task.get("desired") == "done")
            return self.delete_tasks(identifiers)

    def delete_tasks(self, identifiers):
        """Durably mark exact task UUIDs removed; node cleanup stays asynchronous."""
        with self.lock:
            old_dirty, old_save, old_notify = self.dirty, self.last_save, self.last_notify
            accepted = []
            backups = {}
            sent_backups = {}
            sample_backups = {}
            eta_backups = {}
            for identifier in dict.fromkeys(identifiers):
                task = self.tasks.get(identifier)
                if task is None or task.get("desired") == "removed":
                    continue
                backups[identifier] = copy.deepcopy(task)
                sent_backups[identifier] = self.sent.get(identifier)
                sample_backups[identifier] = self.samples.get(identifier)
                eta_backups[identifier] = copy.deepcopy(self.eta_samples.get(identifier))
                self.change_state(task, "removed")
                task.update(status="Removing...", speed=0)
                task["retry_at"] = 0
                self.sent.pop(identifier, None)
                self.samples.pop(identifier, None)
                self.eta_samples.pop(identifier, None)
                accepted.append(identifier)
            if accepted:
                try:
                    self.store.save(self.tasks)
                except Exception:
                    for identifier, task in backups.items():
                        self.tasks[identifier] = task
                        if sent_backups[identifier] is None:
                            self.sent.pop(identifier, None)
                        else:
                            self.sent[identifier] = sent_backups[identifier]
                        if sample_backups[identifier] is not None:
                            self.samples[identifier] = sample_backups[identifier]
                        if eta_backups[identifier] is not None:
                            self.eta_samples[identifier] = eta_backups[identifier]
                        else:
                            self.eta_samples.pop(identifier, None)
                    self.dirty, self.last_save, self.last_notify = old_dirty, old_save, old_notify
                    raise
                self.dirty = False
                self.last_save = time.monotonic()
                try:
                    self.notify(self.snapshot())
                except Exception:
                    # Commit is authoritative. A later periodic checkpoint can notify.
                    self.dirty = True
                else:
                    self.last_notify = self.last_save
        if accepted:
            self.wake.set()
        return accepted

    def stage_path(self, identifier):
        if not identifier.startswith("fld-") or not all(char in "0123456789abcdef" for char in identifier[4:]):
            raise ValueError("Invalid saved request identifier.")
        return self.parts / (identifier + ".ready")

    def change_state(self, task, desired, now=None):
        now = time.time() if now is None else now
        if desired != "running" or task["desired"] != "running":
            self.eta_samples.pop(task["id"], None)
            task["eta_speed"] = 0
        if desired != "running" and task.get("active_since") is not None:
            task["elapsed"] = task.get("elapsed", 0) + max(0, now - task["active_since"])
            task["active_since"] = None
        elif desired == "running" and task.get("active_since") is None:
            task["active_since"] = now
            task["timer_known"] = True
        task["desired"] = desired

    def update_eta(self, task, now):
        """Average output progress over 60 seconds, including transfer stalls."""
        if task["size"] is None:
            task["eta_speed"] = 0
            return
        history = self.eta_samples.setdefault(task["id"], deque())
        fetched = task["fetched"]
        if history and fetched < history[-1][1]:
            history.clear()
        if not history or now - history[-1][0] >= 1:
            history.append((now, fetched))
        while len(history) > 2 and history[1][0] <= now - 60:
            history.popleft()
        start, amount = history[0]
        if start < now - 60 and len(history) > 1:
            next_time, next_amount = history[1]
            amount += (next_amount - amount) * ((now - 60 - start) / (next_time - start))
            start = now - 60
        duration = now - start
        task["eta_speed"] = max(0, (fetched - amount) / duration) if duration >= 5 else 0

    def send_later(self, name, **fields):
        self.outbox.append((name, fields))

    def flush_messages(self):
        # Socket writes must not hold the state lock or block GUI queue actions.
        for _ in range(min(32, len(self.outbox))):
            name, fields = self.outbox.popleft()
            self.connection.send(name, **fields)

    def wait_message(self, expected, pending):
        deadline = time.monotonic() + 15
        while not self.stop_event.is_set() and time.monotonic() < min(deadline, self.close_deadline):
            message = self.connection.read()
            if message is None:
                continue
            if message[0] == expected:
                return message[1]
            if message[0] == "ProtocolError":
                fields = message[1]
                raise OSError(fields.get("CodeDescription", "FCP setup rejected") + ": " + fields.get("ExtraDescription", ""))
            pending.append(message)
        raise ConnectionError("Freenet setup timed out")

    def connect(self):
        self.known, self.sent, self.failed, self.completed, self.samples = {}, {}, set(), {}, {}
        self.outbox.clear()
        self.connection = FCPConnection(self.host, self.port)
        pending = []
        self.connection.send("ClientHello", Name=self.store.client_name, ExpectedVersion="2.0")
        self.wait_message("NodeHello", pending)
        self.connection.send("TestDDARequest", Directory=str(self.parts), WantReadDirectory="false", WantWriteDirectory="true")
        fields = self.wait_message("TestDDAReply", pending)
        challenge = None
        try:
            if "WriteFilename" in fields:
                challenge = Path(fields["WriteFilename"]).resolve()
                if challenge.parent != self.parts or not challenge.name.startswith("DDACheck-"):
                    raise OSError("Freenet returned an invalid disk access challenge.")
                with challenge.open("w", encoding="utf-8") as output:
                    output.write(fields["ContentToWrite"])
            self.connection.send("TestDDAResponse", Directory=str(self.parts))
            fields = self.wait_message("TestDDAComplete", pending)
            if fields.get("WriteDirectoryAllowed") != "true":
                raise OSError("Freenet cannot write the download staging directory.")
        finally:
            if challenge:
                challenge.unlink(missing_ok=True)
        self.listing = True
        self.list_started = time.monotonic()
        self.connection.send("WatchGlobal", Enabled="true", VerbosityMask="73")
        self.connection.send("ListPersistentRequests")
        for message in pending:
            with self.lock:
                self.handle(*message)

    def submit(self, task):
        identifier = task["id"]
        stage = self.stage_path(identifier)
        if stage.exists():
            # A completed file can outlive its node queue entry after a crash.
            if task["size_final"] and stage.stat().st_size == task["size"]:
                self.completed[identifier] = task["size"]
                return
            raise OSError("A staging file needs recovery; it was preserved: " + str(stage))
        self.send_later("ClientGet", URI=task["key"], Identifier=identifier, Verbosity="73",
            ReturnType="disk", Filename=str(stage), Persistence="forever", Global="true",
            FilterData="false", MaxRetries="-1", PriorityClass="2", RealTimeFlag="false")
        self.known[identifier] = {"PriorityClass": "2"}
        self.sent[identifier] = "running"
        task["status"] = "Downloading"
        self.dirty = True

    def reconcile(self):
        if self.listing:
            if time.monotonic() - self.list_started > 15:
                raise ConnectionError("Freenet did not return its persistent queue. Check that persistence is enabled.")
            return
        while True:
            try:
                identifier, error = self.retry_results.get_nowait()
            except queue.Empty:
                break
            self.retrying.discard(identifier)
            task = self.tasks.get(identifier)
            if task is not None and task.get("desired") == "removed":
                # The retry endpoint may have accepted this UUID before deletion.
                # Reconcile it again instead of allowing its result to revive it.
                self.sent.pop(identifier, None)
                self.verify_removals.add(identifier)
                self.dirty = True
                continue
            if task is None or task["desired"] not in {"running", "paused"}:
                continue
            if error:
                task["error"] = "Retry endpoint unavailable: " + error
                task["retry_at"] = time.time() + 30
            else:
                self.failed.discard(identifier)
                task["status"] = "Resuming" if task["desired"] == "running" else "Paused"
                self.send_later("GetRequestStatus", Identifier=identifier, Global="true", OnlyData="false")
            self.dirty = True
        now = time.time()
        for identifier, task in list(self.tasks.items()):
            desired = task["desired"]
            if desired == "running":
                self.update_eta(task, time.monotonic())
            sample = self.samples.get(identifier)
            if sample and time.monotonic() - sample[0] > 5:
                task["speed"] = 0
            if identifier in self.completed and desired not in {"cancelled", "removed"}:
                try:
                    self.finalize(task, self.completed[identifier])
                except OSError as exc:
                    task["status"], task["error"] = "Saving failed; retrying", str(exc)
                continue
            if desired in {"done", "cancelled", "removed"}:
                if identifier in self.known:
                    if self.sent.get(identifier) != "remove":
                        self.send_later("RemovePersistentRequest", Identifier=identifier, Global="true")
                        self.sent[identifier] = "remove"
                elif desired == "removed" and identifier not in self.retrying:
                    if identifier in self.verify_removals:
                        if self.sent.get(identifier) != "verify-remove":
                            self.send_later("GetRequestStatus", Identifier=identifier, Global="true", OnlyData="false")
                            self.sent[identifier] = "verify-remove"
                    else:
                        self.stage_path(identifier).unlink(missing_ok=True)
                        self.tasks.pop(identifier)
                        self.dirty = True
                elif desired == "cancelled":
                    self.stage_path(identifier).unlink(missing_ok=True)
                    task["status"] = "Cancelled"
                continue
            if desired in {"new", "failed"}:
                continue
            if identifier not in self.known:
                if desired == "paused":
                    task["status"] = "Paused"
                else:
                    try:
                        self.submit(task)
                    except OSError as exc:
                        task["status"], task["error"] = "Cannot start", str(exc)
                        self.change_state(task, "failed")
                        self.dirty = True
                continue
            if self.sent.get(identifier) != desired:
                self.send_later("ModifyPersistentRequest", Identifier=identifier, Global="true",
                                     PriorityClass="6" if desired == "paused" else "2")
                self.sent[identifier] = desired
            if desired == "running" and identifier in self.failed and now >= task["retry_at"] and identifier not in self.retrying:
                self.retrying.add(identifier)
                task["status"] = "Retrying automatically"
                self.samples.pop(identifier, None)
                def retry(identifier=identifier):
                    try:
                        restart_node_request(self.web_base, identifier)
                    except Exception as exc:
                        self.retry_results.put((identifier, str(exc)))
                    else:
                        self.retry_results.put((identifier, None))
                threading.Thread(target=retry, daemon=True).start()

    def finalize(self, task, length):
        stage = self.stage_path(task["id"])
        target = Path(task["path"]) if task["path"] else None
        if stage.exists():
            if stage.stat().st_size != length:
                raise OSError("Waiting for Freenet to finish writing the file.")
            if target is None:
                stem, suffix = os.path.splitext(task["filename"])
                for number in range(100000):
                    candidate = self.directory / (task["filename"] if not number else f"{stem} ({number}){suffix}")
                    try:
                        with candidate.open("xb"):
                            pass
                    except FileExistsError:
                        continue
                    target = candidate
                    task["path"] = str(target)
                    self.dirty = True
                    self.checkpoint(True)
                    break
                if target is None:
                    raise OSError("Too many files with the same name.")
            if target.exists() and target.stat().st_size != 0:
                raise OSError("Reserved destination was changed; file preserved in staging.")
            os.replace(stage, target)
        elif target is None or not target.exists() or target.stat().st_size != length:
            raise OSError("Completed file is not available yet.")
        self.change_state(task, "done", task.get("completed_at"))
        task.update(status="Downloaded", filename=target.name, size=length, size_final=True,
                    fetched=length, fraction=1, speed=0, error="")
        self.completed.pop(task["id"], None)
        self.dirty = True
        self.checkpoint(True)

    def handle(self, name, fields):
        identifier = fields.get("Identifier")
        task = self.tasks.get(identifier)
        if name == "EndListPersistentRequests":
            self.listing = False
            absent = self.verify_removals.difference(self.known)
            self.verify_removals.difference_update(absent)
            for pending_id in absent:
                self.sent.pop(pending_id, None)
            return
        if task is None:
            if name == "ProtocolError" and identifier is None:
                raise OSError(fields.get("CodeDescription", "FCP error") + ": " + fields.get("ExtraDescription", ""))
            return
        desired = task["desired"]
        if desired == "removed":
            if name == "PersistentGet":
                # Listing proves the UUID still exists; reconcile will remove it.
                self.known[identifier] = fields
                self.verify_removals.discard(identifier)
            elif name == "PersistentRequestRemoved":
                self.known.pop(identifier, None)
                self.sent.pop(identifier, None)
                self.verify_removals.discard(identifier)
                self.completed.pop(identifier, None)
                self.failed.discard(identifier)
                self.stage_path(identifier).unlink(missing_ok=True)
            elif name == "ProtocolError" and fields.get("Code") == "15":
                self.known.pop(identifier, None)
                self.sent.pop(identifier, None)
                self.verify_removals.discard(identifier)
            else:
                # Late progress/completion/failure cannot alter a tombstone.
                return
            self.dirty = True
            return
        if name == "PersistentGet":
            if fields.get("ReturnType") != "disk":
                raise OSError("Saved request has an unexpected return type.")
            self.known[identifier] = fields
            priority = "6" if desired == "paused" else "2"
            if fields.get("PriorityClass") == priority:
                self.sent[identifier] = desired
            if desired == "paused" and fields.get("PriorityClass") == "6":
                task["status"] = "Paused"
            elif desired == "running" and identifier not in self.failed:
                task["status"] = "Downloading"
                task["error"] = ""
        elif name == "PersistentRequestModified":
            if identifier in self.known:
                self.known[identifier]["PriorityClass"] = fields.get("PriorityClass")
            if desired == "paused" and fields.get("PriorityClass") == "6":
                task["status"] = "Paused"
        elif name == "ExpectedDataLength":
            if task["size"] != int(fields["DataLength"]):
                self.eta_samples.pop(identifier, None)
            task["size"] = int(fields["DataLength"])
            task["size_final"] = fields.get("Finalized", fields.get("FinalizedExpected", "false")) == "true"
            task["fetched"] = int(task["fraction"] * task["size"])
        elif name == "SimpleProgress":
            required = int(fields.get("Required", 0))
            succeeded = int(fields.get("Succeeded", 0))
            task["fraction"] = min(0.999, succeeded / required) if required > 0 else 0
            task["fetched"] = int(task["fraction"] * task["size"]) if task["size"] is not None else 0
            now = time.monotonic()
            previous = self.samples.get(identifier)
            if previous and now - previous[0] >= 0.5:
                task["speed"] = max(0, (succeeded - previous[1]) * 32768 / (now - previous[0]))
                self.samples[identifier] = now, succeeded
            elif not previous:
                self.samples[identifier] = now, succeeded
            if desired == "running" and identifier not in self.failed:
                task["status"] = "Downloading"
            elif desired == "paused":
                task["speed"] = 0
        elif name == "DataFound":
            self.completed[identifier] = int(fields["DataLength"])
            if fields.get("CompletionTime"):
                task["completed_at"] = min(time.time(), int(fields["CompletionTime"]) / 1000)
        elif name == "GetFailed":
            if desired == "failed":
                self.failed.add(identifier)
                return
            if desired not in {"running", "paused"}:
                return
            code = int(fields.get("Code", -1))
            description = fields.get("CodeDescription", "Freenet request failed")
            task["error"] = f"{description} (code {code})" + (": " + fields["ExtraDescription"] if fields.get("ExtraDescription") else "")
            self.failed.add(identifier)
            if code == 27 or fields.get("Fatal") != "true" or code in {12, 13, 14, 15, 17, 18, 19, 25, 28, 30}:
                if task["retry_at"] <= time.time():
                    task["retries"] += 1
                    task["retry_at"] = time.time() + (0 if code == 27 else min(300, 2 ** min(task["retries"], 8)))
                task["status"] = "Retrying automatically" if desired == "running" else "Paused"
            else:
                self.change_state(task, "failed")
                task["status"] = "Failed; see details"
            task["speed"] = 0
        elif name == "ProtocolError":
            code = fields.get("Code")
            if code == "15":
                self.known.pop(identifier, None)
                self.sent.pop(identifier, None)
            else:
                self.change_state(task, "failed")
                task["status"] = "Node rejected request"
                task["error"] = fields.get("CodeDescription", "FCP error") + ": " + fields.get("ExtraDescription", "")
        elif name == "IdentifierCollision":
            self.send_later("GetRequestStatus", Identifier=identifier, Global="true", OnlyData="false")
        elif name == "PersistentRequestRemoved":
            self.known.pop(identifier, None)
            self.sent.pop(identifier, None)
            self.completed.pop(identifier, None)
            self.failed.discard(identifier)
            if desired in {"cancelled", "removed"}:
                self.stage_path(identifier).unlink(missing_ok=True)
        self.dirty = True

    def controls_applied(self):
        if self.listing:
            return False
        for identifier, task in self.tasks.items():
            node = self.known.get(identifier)
            if task["desired"] == "paused" and node and node.get("PriorityClass") != "6":
                return False
            if task["desired"] in {"cancelled", "removed", "done"} and node:
                return False
        return True

    def run(self):
        delay = self.reconnect_delay
        while not self.stop_event.is_set() and not self.closing.is_set():
            try:
                self.connect()
                delay = self.reconnect_delay
                last_poll = last_received = time.monotonic()
                while not self.stop_event.is_set():
                    with self.lock:
                        self.reconcile()
                        self.checkpoint()
                        if self.closing.is_set() and ((self.controls_applied() and not self.outbox) or time.monotonic() >= self.close_deadline):
                            break
                        if time.monotonic() - last_poll >= 10:
                            for identifier in list(self.known):
                                self.send_later("GetRequestStatus", Identifier=identifier, Global="true", OnlyData="false")
                            last_poll = time.monotonic()
                    self.flush_messages()
                    message = self.connection.read()
                    if message:
                        last_received = time.monotonic()
                        with self.lock:
                            self.handle(*message)
                    elif time.monotonic() - last_received > 60 and self.known:
                        raise ConnectionError("Freenet stopped responding")
            except (OSError, ValueError, KeyError) as exc:
                with self.lock:
                    for task in self.tasks.values():
                        if task["desired"] in {"running", "paused"}:
                            task["status"] = "Reconnecting automatically" if task["desired"] == "running" else "Pause pending; node offline"
                            task["speed"] = 0
                            task["error"] = str(exc)
                    self.dirty = True
                    self.checkpoint(True)
                self.wake.wait(delay)
                self.wake.clear()
                delay = min(30, delay * 2)
            finally:
                if self.connection:
                    self.connection.close()
                    self.connection = None
        with self.lock:
            self.checkpoint(True)

    def close(self):
        self.close_deadline = time.monotonic() + 4
        self.closing.set()
        self.wake.set()
        if self.thread:
            self.thread.join(6)
            if self.thread.is_alive():
                raise OSError("Freenet connection is still shutting down. Queue state is saved.")
        elif self.connection:
            self.connection.close()
            self.connection = None
        with self.lock:
            self.checkpoint(True)
            self.store.close()

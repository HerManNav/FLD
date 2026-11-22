from pathlib import Path
from collections import deque
import json
import queue
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fake_node import PAYLOAD, PersistentNode
from fld.app import action_worker
from fld.queue_manager import DownloadManager
from fld.node_protocol import FCPConnection
from fld.queue_store import QueueStore
from test_freenet import KEY


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.node = PersistentNode()
        threading.Thread(target=self.node.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.addCleanup(self.node.server_close)
        self.addCleanup(self.node.shutdown)
        self.addCleanup(self.node.drop_connections)
        self.addCleanup(lambda: self.assertEqual(self.node.errors, []))
        self.managers = []
        self.addCleanup(self.close_managers)

    def close_managers(self):
        for manager in self.managers:
            manager.close()

    def manager(self):
        manager = DownloadManager(self.root / "queue.sqlite3", self.root / "downloads",
                                 port=self.node.server_address[1], reconnect_delay=0.02,
                                 web_base=f"http://127.0.0.1:{self.node.web.server_port}")
        self.managers.append(manager)
        return manager

    def wait(self, condition, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.02)
        self.fail("Persistent queue condition did not become true")

    def new_download(self, manager, name="file with spaces.mp4"):
        manager.add([KEY + "/" + name])
        identifier = manager.snapshot()[-1]["id"]
        manager.start([identifier])
        self.wait(lambda: identifier in self.node.jobs)
        return identifier

    def test_resume_after_pause_app_restart_and_node_reconnect(self):
        manager = self.manager()
        identifier = self.new_download(manager)
        self.node.progress(identifier, 4)
        self.wait(lambda: manager.snapshot()[0]["fraction"] == 0.4)
        manager.pause([identifier])
        self.wait(lambda: self.node.jobs[identifier]["PriorityClass"] == "6")
        manager.close()
        self.managers.remove(manager)
        # The mock node retains its persistent job while its FCP connections restart.
        self.node.drop_connections()
        restored = self.manager()
        self.wait(lambda: restored.snapshot()[0]["status"] == "Paused")
        self.assertEqual(restored.snapshot()[0]["fraction"], 0.4)
        restored.start([identifier])
        self.wait(lambda: self.node.jobs[identifier]["PriorityClass"] == "2")
        self.assertEqual(self.node.submissions, 1)
        self.assertEqual(self.node.jobs[identifier]["MaxRetries"], "-1")
        self.node.complete(identifier)
        self.wait(lambda: restored.snapshot()[0]["desired"] == "done")
        task = restored.snapshot()[0]
        self.assertEqual(Path(task["path"]).read_bytes(), PAYLOAD)
        self.assertEqual(task["size"], len(PAYLOAD))
        self.assertTrue(task["size_final"])
        self.wait(lambda: identifier not in self.node.jobs)

    def test_connection_loss_resumes_same_request_automatically(self):
        manager = self.manager()
        identifier = self.new_download(manager)
        self.node.progress(identifier, 7)
        self.wait(lambda: manager.snapshot()[0]["fraction"] == 0.7)
        previous_connection = manager.connection
        self.node.drop_connections()
        self.wait(lambda: manager.connection is not None and manager.connection is not previous_connection and identifier in manager.known)
        self.assertEqual(self.node.submissions, 1)
        self.assertEqual(manager.snapshot()[0]["fraction"], 0.7)
        self.node.complete(identifier)
        self.wait(lambda: manager.snapshot()[0]["desired"] == "done")

    def test_network_failure_restarts_preserved_request(self):
        manager = self.manager()
        identifier = self.new_download(manager)
        self.node.progress(identifier, 5)
        self.node.fail(identifier, 19, fatal=True)
        self.wait(lambda: manager.snapshot()[0]["status"] == "Retrying automatically")
        with manager.lock:
            manager.tasks[identifier]["retry_at"] = 0
        self.wait(lambda: self.node.restarts == 1)
        self.assertEqual(self.node.submissions, 1)
        self.assertEqual(self.node.jobs[identifier]["succeeded"], 5)
        self.node.complete(identifier)
        self.wait(lambda: manager.snapshot()[0]["desired"] == "done")

    def test_fatal_invalid_key_stops_retrying_and_keeps_error(self):
        manager = self.manager()
        identifier = self.new_download(manager)
        self.node.fail(identifier, 20, fatal=True)
        self.wait(lambda: manager.snapshot()[0]["desired"] == "failed")
        self.assertIn("code 20", manager.snapshot()[0]["error"])
        self.assertEqual(self.node.restarts, 0)

    def test_size_and_progress_are_saved_and_recovered(self):
        manager = self.manager()
        identifier = self.new_download(manager)
        self.node.progress(identifier, 3)
        self.wait(lambda: manager.snapshot()[0]["fetched"] == int(len(PAYLOAD) * 0.3))
        self.assertEqual(manager.snapshot()[0]["size"], len(PAYLOAD))
        manager.close()
        self.managers.remove(manager)
        store = QueueStore(self.root / "queue.sqlite3")
        try:
            task = store.load()[identifier]
            self.assertEqual(task["fraction"], 0.3)
            self.assertEqual(task["desired"], "running")
        finally:
            store.close()

    def test_all_requests_start_and_pause_independently(self):
        manager = self.manager()
        manager.add([KEY + "/one.mp4", KEY + "/two.zip"])
        identifiers = [task["id"] for task in manager.snapshot()]
        manager.start(identifiers)
        self.wait(lambda: len(self.node.jobs) == 2)
        manager.pause([identifiers[0]])
        self.wait(lambda: self.node.jobs[identifiers[0]]["PriorityClass"] == "6")
        self.assertEqual(self.node.jobs[identifiers[1]]["PriorityClass"], "2")
        manager.pause(identifiers)
        self.wait(lambda: all(job["PriorityClass"] == "6" for job in self.node.jobs.values()))

    def test_completed_file_after_offline_gui_is_recovered_without_refetch(self):
        manager = self.manager()
        identifier = self.new_download(manager)
        manager.close()
        self.managers.remove(manager)
        self.node.complete(identifier)
        restored = self.manager()
        self.wait(lambda: restored.snapshot()[0]["desired"] == "done")
        self.assertEqual(self.node.submissions, 1)
        self.assertEqual(Path(restored.snapshot()[0]["path"]).read_bytes(), PAYLOAD)

    def test_duplicate_destination_files_are_preserved(self):
        manager = self.manager()
        directory = self.root / "downloads"
        (directory / "file with spaces.mp4").write_bytes(b"existing file")
        identifier = self.new_download(manager)
        self.node.complete(identifier)
        self.wait(lambda: manager.snapshot()[0]["desired"] == "done")
        self.assertEqual((directory / "file with spaces.mp4").read_bytes(), b"existing file")
        self.assertEqual(Path(manager.snapshot()[0]["path"]).name, "file with spaces (1).mp4")

    def test_reserved_destination_conflict_preserves_staging_and_reports_failure(self):
        manager = DownloadManager(self.root / "conflict.sqlite3", self.root / "conflict-downloads", autostart=False)
        self.managers.append(manager)
        manager.add([KEY + "/conflict.zip"])
        task = manager.snapshot()[0]
        destination = manager.directory / task["filename"]
        destination.write_bytes(b"existing destination")
        stage = manager.stage_path(task["id"])
        stage.write_bytes(PAYLOAD)
        task["path"] = str(destination)
        manager.tasks[task["id"]]["path"] = str(destination)
        manager.tasks[task["id"]]["desired"] = "running"
        manager.completed[task["id"]] = len(PAYLOAD)
        manager.listing = False
        with manager.lock:
            manager.reconcile()
        saved = manager.tasks[task["id"]]
        self.assertEqual(saved["status"], "Saving failed; retrying")
        self.assertIn("Reserved destination was changed", saved["error"])
        self.assertEqual(destination.read_bytes(), b"existing destination")
        self.assertEqual(stage.read_bytes(), PAYLOAD)

    def test_durable_duplicate_names_preserve_existing_file_and_all_payloads(self):
        manager = self.manager()
        directory = self.root / "downloads"
        directory.mkdir(exist_ok=True)
        existing = directory / "same.mp4"
        existing.write_bytes(b"keep existing")
        manager.add([KEY + "/same.mp4", "SSK@a,b,c/same.mp4", "USK@a,b,c/site/1/same.mp4"])
        identifiers = [task["id"] for task in manager.snapshot()]
        manager.start(identifiers)
        self.wait(lambda: all(identifier in self.node.jobs for identifier in identifiers))
        for identifier in identifiers:
            self.node.complete(identifier)
        self.wait(lambda: all(manager.tasks[identifier]["desired"] == "done" for identifier in identifiers))
        paths = [Path(manager.tasks[identifier]["path"]) for identifier in identifiers]
        self.assertEqual(len(set(paths)), 3)
        self.assertEqual({path.name for path in paths}, {"same (1).mp4", "same (2).mp4", "same (3).mp4"})
        self.assertEqual(existing.read_bytes(), b"keep existing")
        self.assertTrue(all(path.read_bytes() == PAYLOAD for path in paths))

    def test_cancel_removes_active_stage_and_keeps_completed_file_and_foreign_request(self):
        manager = self.manager()
        manager.add([KEY + "/active.zip", KEY + "/done.zip"])
        active, completed = [task["id"] for task in manager.snapshot()]
        manager.start([active, completed])
        self.wait(lambda: active in self.node.jobs and completed in self.node.jobs)
        self.node.complete(completed)
        self.wait(lambda: manager.tasks[completed]["desired"] == "done")
        completed_path = Path(manager.tasks[completed]["path"])
        stage = manager.stage_path(active)
        stage.write_bytes(b"partial node output")
        foreign = "another-app-cancel-proof"
        self.node.jobs[foreign] = dict(URI=KEY + "/foreign.zip", Filename=str(self.root / "foreign.part"),
            PriorityClass="3", ReturnType="disk", Global="true", client="other-app", succeeded=7,
            completed=False, failure=None)
        manager.cancel([active, completed])
        self.wait(lambda: active not in self.node.jobs and not stage.exists())
        self.assertEqual(manager.tasks[completed]["desired"], "done")
        self.assertEqual(completed_path.read_bytes(), PAYLOAD)
        self.assertIn(foreign, self.node.jobs)
        self.assertIn(active, manager.tasks)
        self.assertIn(completed, manager.tasks)

    def test_saved_removed_state_reconciles_without_clear_and_preserves_foreign_data(self):
        manager = self.manager()
        identifier = self.new_download(manager, "legacy removed.zip")
        manager.close()
        self.managers.remove(manager)
        store = QueueStore(self.root / "queue.sqlite3")
        tasks = store.load()
        tasks[identifier]["desired"] = "removed"
        tasks[identifier]["ever_started"] = True
        store.save(tasks)
        store.close()
        foreign = "another-app-legacy-removal"
        self.node.jobs[foreign] = dict(URI=KEY + "/foreign.zip", Filename=str(self.root / "foreign.part"),
            PriorityClass="3", ReturnType="disk", Global="true", client="other-app", succeeded=7,
            completed=False, failure=None)
        completed_file = self.root / "downloads" / "already-complete.zip"
        completed_file.parent.mkdir(parents=True, exist_ok=True)
        completed_file.write_bytes(b"completed data")
        restored = self.manager()
        self.wait(lambda: identifier not in restored.tasks and identifier not in self.node.jobs)
        self.assertIn(foreign, self.node.jobs)
        self.assertEqual(completed_file.read_bytes(), b"completed data")

    def test_forced_process_exit_keeps_resume_identity(self):
        code = """
from pathlib import Path
import os, sys, time
from fld.queue_manager import DownloadManager
root=Path(sys.argv[1]); port=int(sys.argv[2]); key=sys.argv[3]
manager=DownloadManager(root/'queue.sqlite3', root/'downloads', port=port)
manager.add([key]); identifier=manager.snapshot()[0]['id']; manager.start([identifier])
deadline=time.monotonic()+5
while identifier not in manager.known and time.monotonic()<deadline: time.sleep(.02)
os._exit(0)
"""
        result = subprocess.run([sys.executable, "-c", code, str(self.root), str(self.node.server_address[1]), KEY + "/crash.zip"],
                                cwd=Path(__file__).resolve().parents[1], timeout=10)
        self.assertEqual(result.returncode, 0)
        restored = self.manager()
        self.wait(lambda: len(restored.known) == 1)
        self.assertEqual(self.node.submissions, 1)
        identifier = restored.snapshot()[0]["id"]
        self.node.complete(identifier)
        self.wait(lambda: restored.snapshot()[0]["desired"] == "done")

    def test_second_app_instance_cannot_steal_queue_connection(self):
        manager = self.manager()
        with self.assertRaisesRegex(OSError, "already open"):
            QueueStore(self.root / "queue.sqlite3")

    def test_store_save_rolls_back_partial_checkpoint(self):
        store = QueueStore(self.root / "rollback.sqlite3")
        self.addCleanup(store.close)
        old = {"first": {"filename": "old one.zip"}, "second": {"filename": "old two.zip"}}
        store.save(old)
        store.db.execute("CREATE TRIGGER fail_second BEFORE INSERT ON tasks WHEN NEW.id='new-second' BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "injected failure"):
            store.save({"new-first": {"filename": "new one.zip"}, "new-second": {"filename": "new two.zip"}})
        self.assertEqual(store.load(), old)

    def test_store_metadata_only_empty_mapping_and_defaults(self):
        store = QueueStore(self.root / "store-modes.sqlite3")
        self.addCleanup(store.close)
        store.save({"one": {"filename": "kept.zip"}})
        store.save(metadata={"preference": '"saved"'},
                   defaults={"preference": '"default"', "first_run": 'true'})
        self.assertEqual(store.load(), {"one": {"filename": "kept.zip"}})
        self.assertEqual(store.get_metadata("preference"), '"saved"')
        self.assertEqual(store.get_metadata("first_run"), "true")
        store.save({})
        self.assertEqual(store.load(), {})

    def test_add_tasks_and_display_counter_share_transaction(self):
        manager = DownloadManager(self.root / "add-rollback.sqlite3", self.root / "add-rollback-downloads", autostart=False)
        self.managers.append(manager)
        manager.add([KEY + "/committed.zip"])
        previous_tasks = manager.store.load()
        previous_counter = manager.store.db.execute("SELECT value FROM metadata WHERE name='next_display_id'").fetchone()[0]
        manager.store.db.execute("CREATE TRIGGER fail_counter BEFORE INSERT ON metadata WHEN NEW.name='next_display_id' BEGIN SELECT RAISE(ABORT, 'counter failure'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "counter failure"):
            manager.add([KEY + "/rolled back.zip"])
        self.assertEqual(manager.store.load(), previous_tasks)
        self.assertEqual(manager.store.db.execute("SELECT value FROM metadata WHERE name='next_display_id'").fetchone()[0], previous_counter)

    def test_migration_task_and_counter_writes_share_transaction(self):
        manager = DownloadManager(self.root / "migration-rollback.sqlite3", self.root / "migration-rollback-downloads", autostart=False)
        self.managers.append(manager)
        manager.add([KEY + "/legacy.zip"])
        identifier = manager.snapshot()[0]["id"]
        legacy = manager.store.load()
        legacy[identifier].pop("display_id")
        legacy[identifier].pop("ever_started")
        manager.store.save(legacy)
        old_counter = manager.store.db.execute("SELECT value FROM metadata WHERE name='next_display_id'").fetchone()[0]
        manager.store.db.execute("DELETE FROM metadata WHERE name='next_display_id'")
        manager.store.db.execute("CREATE TRIGGER fail_counter BEFORE INSERT ON metadata WHEN NEW.name='next_display_id' BEGIN SELECT RAISE(ABORT, 'migration counter failure'); END")
        manager.tasks = manager.store.load()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "migration counter failure"):
            manager._migrate_tasks()
        self.assertEqual(manager.store.load(), legacy)
        self.assertEqual(manager.store.db.execute("SELECT value FROM metadata WHERE name='next_display_id'").fetchone()[0], old_counter)

    def test_future_schema_rejection_preserves_version_and_releases_lock(self):
        path = self.root / "future.sqlite3"
        db = sqlite3.connect(path)
        db.execute("PRAGMA user_version=99")
        db.close()
        with self.assertRaisesRegex(ValueError, "newer FLD version"):
            QueueStore(path)
        db = sqlite3.connect(path)
        self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 99)
        db.execute("PRAGMA user_version=1")
        db.close()
        store = QueueStore(path)
        self.addCleanup(store.close)
        self.assertEqual(store.db.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_persistent_fcp_framing_timeout_rejection_and_field_injection(self):
        class SocketDouble:
            def __init__(self, chunks):
                self.chunks, self.sent = iter(chunks), []
            def settimeout(self, value): pass
            def recv(self, size):
                value = next(self.chunks)
                if isinstance(value, BaseException):
                    raise value
                return value
            def sendall(self, data): self.sent.append(data)
            def close(self): pass

        socket_double = SocketDouble([b"Event\nA=", socket.timeout(),
                                      b"1\nEndMessage\nOther\nB=2\nEndMessage\n"])
        with patch("fld.node_protocol.socket.create_connection", return_value=socket_double):
            connection = FCPConnection("127.0.0.1", 1)
        self.assertIsNone(connection.read())
        self.assertEqual(connection.read(), ("Event", {"A": "1"}))
        self.assertEqual(connection.read(), ("Other", {"B": "2"}))
        with self.assertRaisesRegex(ValueError, "line break"):
            connection.send("ClientGet", Identifier="safe\nInjected=yes")
        self.assertEqual(socket_double.sent, [])

        for invalid, error in ((b"Event\ninvalid\n", "Invalid FCP message"),
                               (b"Event\nData\n", "Unexpected file stream")):
            bad = SocketDouble([invalid])
            with patch("fld.node_protocol.socket.create_connection", return_value=bad):
                candidate = FCPConnection("127.0.0.1", 1)
            with self.assertRaisesRegex(OSError, error):
                candidate.read()

    def test_snapshot_nested_mutation_cannot_change_manager_or_database(self):
        manager = DownloadManager(self.root / "snapshot.sqlite3", self.root / "snapshot-downloads", autostart=False)
        self.managers.append(manager)
        manager.add([KEY + "/snapshot.zip"])
        identifier = manager.snapshot()[0]["id"]
        manager.tasks[identifier]["extension_data"] = {"parts": ["original"]}
        manager.store.save(manager.tasks)
        snapshot = manager.snapshot()
        snapshot[0]["extension_data"]["parts"].append("changed")
        self.assertEqual(manager.tasks[identifier]["extension_data"]["parts"], ["original"])
        self.assertEqual(manager.store.load()[identifier]["extension_data"]["parts"], ["original"])

    def test_action_worker_recovers_and_skips_cancelled_scan_add(self):
        class Manager:
            def __init__(self): self.actions = []
            def fail(self): raise RuntimeError("expected action failure")
            def next(self): self.actions.append("next")
            def add(self, keys): self.actions.append(("add", keys))

        manager, actions, events = Manager(), queue.Queue(), queue.Queue()
        cancelled = threading.Event()
        cancelled.set()
        for action in (("fail", (), 1, None), ("next", (), 2, None),
                       ("scan_add", ([KEY],), 3, cancelled), ("stop", (), 4, None)):
            actions.put(action)
        worker = threading.Thread(target=action_worker, args=(manager, actions, events))
        worker.start()
        actions.join()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(manager.actions, ["next"])
        received = [events.get_nowait(), events.get_nowait()]
        self.assertEqual(received, [(1, "action_error", ("expected action failure",)),
                                    (3, "scan_cancelled", ())])

    def test_eta_uses_sixty_second_history_and_includes_stalls(self):
        manager = DownloadManager(self.root / "eta.sqlite3", self.root / "eta-downloads", autostart=False)
        self.managers.append(manager)
        manager.add([KEY + "/average.mp4"])
        task = manager.tasks[manager.snapshot()[0]["id"]]
        task.update(size=1000000, desired="running")
        for second in range(61):
            task["fetched"] = second * 1000
            manager.update_eta(task, second)
        self.assertEqual(task["eta_speed"], 1000)
        task["fetched"] += 10000
        manager.update_eta(task, 61)
        self.assertEqual(task["eta_speed"], 1150)
        task["speed"] = 0
        manager.update_eta(task, 70)
        self.assertEqual(task["eta_speed"], 1000)
        for second in range(71, 132):
            manager.update_eta(task, second)
        self.assertEqual(task["eta_speed"], 0)
        self.assertLessEqual(len(manager.eta_samples[task["id"]]), 62)
        manager.pause([task["id"]])
        self.assertNotIn(task["id"], manager.eta_samples)

    def test_eta_averages_irregular_samples_and_requires_warmup(self):
        manager = DownloadManager(self.root / "eta-irregular.sqlite3", self.root / "eta2-downloads", autostart=False)
        self.managers.append(manager)
        manager.add([KEY + "/average.mp4"])
        task = manager.tasks[manager.snapshot()[0]["id"]]
        task["size"] = 1000000
        for second in (0, 1, 3):
            task["fetched"] = second * 1000
            manager.update_eta(task, second)
        self.assertEqual(task["eta_speed"], 0)
        for second in (30, 61):
            task["fetched"] = second * 1000
            manager.update_eta(task, second)
        self.assertEqual(task["eta_speed"], 1000)

    def test_blocked_socket_write_does_not_hold_queue_state_lock(self):
        entered, release, read_finished = threading.Event(), threading.Event(), threading.Event()
        original = FCPConnection.send
        def delayed_send(connection, name, **fields):
            if name == "ClientGet":
                entered.set()
                release.wait(3)
            return original(connection, name, **fields)
        with patch.object(FCPConnection, "send", delayed_send):
            manager = self.manager()
            manager.add([KEY + "/slow-write.zip"])
            identifier = manager.snapshot()[0]["id"]
            manager.start([identifier])
            self.assertTrue(entered.wait(2))
            reader = threading.Thread(target=lambda: (manager.snapshot(), read_finished.set()))
            reader.start()
            try:
                self.assertTrue(read_finished.wait(.5), "Socket I/O blocked access to queue state")
            finally:
                release.set()
                reader.join(2)
            self.wait(lambda: identifier in self.node.jobs)

    def test_elapsed_excludes_pause_and_survives_restart(self):
        manager = DownloadManager(self.root / "timing.sqlite3", self.root / "timed-downloads", autostart=False)
        self.managers.append(manager)
        manager.add([KEY + "/timed.mp4"])
        identifier = manager.snapshot()[0]["id"]
        with patch("fld.queue_manager.time.time", return_value=100):
            manager.start([identifier])
        with patch("fld.queue_manager.time.time", return_value=135):
            manager.pause([identifier])
        self.assertEqual(manager.snapshot()[0]["elapsed"], 35)
        with patch("fld.queue_manager.time.time", return_value=200):
            manager.start([identifier])
        manager.stage_path(identifier).write_bytes(PAYLOAD)
        with patch("fld.queue_manager.time.time", return_value=225):
            manager.handle("DataFound", {"Identifier": identifier, "DataLength": str(len(PAYLOAD)), "CompletionTime": "222000"})
            manager.finalize(manager.tasks[identifier], len(PAYLOAD))
        self.assertEqual(manager.snapshot()[0]["elapsed"], 57)
        manager.close()
        self.managers.remove(manager)
        restored = DownloadManager(self.root / "timing.sqlite3", self.root / "timed-downloads", autostart=False)
        self.managers.append(restored)
        self.assertEqual(restored.snapshot()[0]["elapsed"], 57)
        self.assertIsNone(restored.snapshot()[0]["active_since"])

    def test_old_completed_records_do_not_claim_a_known_duration(self):
        manager = DownloadManager(self.root / "legacy.sqlite3", self.root / "legacy-downloads", autostart=False)
        self.managers.append(manager)
        manager.add([KEY + "/old.mp4"])
        task = manager.tasks[manager.snapshot()[0]["id"]]
        for field in ("elapsed", "active_since", "timer_known"):
            task.pop(field)
        task["desired"] = "done"
        manager.store.save(manager.tasks)
        manager.close()
        self.managers.remove(manager)
        restored = DownloadManager(self.root / "legacy.sqlite3", self.root / "legacy-downloads", autostart=False)
        self.managers.append(restored)
        self.assertFalse(restored.snapshot()[0]["timer_known"])

    def test_other_apps_global_queue_entries_are_ignored(self):
        foreign = "another-app-download"
        self.node.jobs[foreign] = dict(URI=KEY + "/other.zip", Filename=str(self.root / "foreign.part"),
            PriorityClass="3", ReturnType="disk", Global="true", client="other-app", succeeded=7,
            failure=None, completed=False)
        manager = self.manager()
        identifier = self.new_download(manager)
        manager.pause([identifier])
        self.wait(lambda: self.node.jobs[identifier]["PriorityClass"] == "6")
        manager.cancel([identifier])
        self.wait(lambda: identifier not in self.node.jobs)
        self.assertIn(identifier, manager.tasks)
        self.assertEqual(manager.tasks[identifier]["desired"], "cancelled")
        self.assertIn(foreign, self.node.jobs)
        self.assertEqual(self.node.jobs[foreign]["PriorityClass"], "3")

    def test_pause_immediately_before_close_reaches_node(self):
        manager = self.manager()
        identifier = self.new_download(manager)
        manager.pause([identifier])
        manager.close()
        self.managers.remove(manager)
        self.assertEqual(self.node.jobs[identifier]["PriorityClass"], "6")
        restored = self.manager()
        self.wait(lambda: restored.snapshot()[0]["status"] == "Paused")

    def test_failed_task_can_be_started_after_reopening_app(self):
        manager = self.manager()
        identifier = self.new_download(manager)
        self.node.fail(identifier, 20, fatal=True)
        self.wait(lambda: manager.snapshot()[0]["desired"] == "failed")
        manager.close()
        self.managers.remove(manager)
        restored = self.manager()
        self.wait(lambda: identifier in restored.failed)
        restored.start([identifier])
        self.wait(lambda: self.node.restarts == 1)
        self.assertEqual(self.node.submissions, 1)

    def test_recovers_rename_completed_before_state_commit(self):
        manager = self.manager()
        identifier = self.new_download(manager)
        target = self.root / "downloads" / "recovered.mp4"
        target.write_bytes(PAYLOAD)
        with manager.lock:
            manager.tasks[identifier]["path"] = str(target)
        # Simulate a crash after renaming the node's file but before marking it done.
        with self.node.lock:
            self.node.jobs[identifier]["completed"] = True
        self.node.broadcast(identifier, "DataFound", DataLength=len(PAYLOAD))
        self.wait(lambda: manager.snapshot()[0]["desired"] == "done")
        self.assertEqual(manager.snapshot()[0]["path"], str(target))
        self.assertEqual(target.read_bytes(), PAYLOAD)

    def test_display_ids_survive_removal_restart_and_never_reuse_numbers(self):
        manager = DownloadManager(self.root / "ids.sqlite3", self.root / "ids-downloads", autostart=False)
        self.managers.append(manager)
        manager.add([KEY + "/one.mp4", KEY + "/one.mp4", KEY + "/two.mp4"])
        tasks = manager.snapshot()
        self.assertEqual([task["display_id"] for task in tasks], [1, 2])
        first, removed = [task["id"] for task in tasks]
        self.assertEqual(manager.remove_unstarted(), [first, removed])
        manager.add([KEY + "/three.mp4"])
        third = manager.snapshot()[0]
        self.assertEqual(third["display_id"], 3)
        manager.close()
        self.managers.remove(manager)
        restored = DownloadManager(self.root / "ids.sqlite3", self.root / "ids-downloads", autostart=False)
        self.managers.append(restored)
        restored.add([KEY + "/four.mp4"])
        self.assertEqual(restored.snapshot()[-1]["display_id"], 4)

    def test_start_history_and_remove_action_order_are_authoritative(self):
        manager = DownloadManager(self.root / "history.sqlite3", self.root / "history-downloads", autostart=False)
        self.managers.append(manager)
        manager.add([KEY + "/started.mp4", KEY + "/new.zip"])
        started, never_started = [task["id"] for task in manager.snapshot()]
        manager.start([started])
        self.assertTrue(manager.snapshot()[0]["ever_started"])
        self.assertEqual(manager.remove_unstarted(), [never_started])
        self.assertEqual([task["id"] for task in manager.snapshot()], [started])
        manager.start([never_started])
        manager.pause(["missing-uuid"])
        self.assertEqual([task["id"] for task in manager.snapshot()], [started])

    def test_remove_completed_uses_authoritative_done_state_and_keeps_output(self):
        manager = DownloadManager(self.root / "completed.sqlite3", self.root / "completed-downloads", autostart=False)
        self.managers.append(manager)
        manager.add([KEY + "/done.mp4", KEY + "/running.zip", KEY + "/new.jpg"])
        done, running, new = [task["id"] for task in manager.snapshot()]
        output = self.root / "completed-downloads" / "done.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(PAYLOAD)
        with manager.lock:
            manager.tasks[done].update(desired="done", status="Downloaded", path=str(output), fraction=1)
            manager.tasks[running].update(desired="running", status="Downloaded", fraction=1)
        self.assertEqual(manager.remove_completed(), [done])
        self.assertEqual(manager.remove_completed(), [])
        remaining = {task["id"] for task in manager.snapshot()}
        self.assertEqual(remaining, {running, new})
        self.assertEqual(output.read_bytes(), PAYLOAD)

    def test_remove_completed_checkpoint_failure_restores_done_record(self):
        manager = DownloadManager(self.root / "completed-rollback.sqlite3",
                                  self.root / "completed-rollback-downloads", autostart=False)
        self.managers.append(manager)
        manager.add([KEY + "/done.mp4"])
        identifier = manager.snapshot()[0]["id"]
        with manager.lock:
            manager.tasks[identifier].update(desired="done", status="Downloaded")
        with patch.object(manager.store, "save", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                manager.remove_completed()
        self.assertEqual(manager.snapshot()[0]["desired"], "done")

    def test_delete_tasks_tombstones_exact_uuids_and_preserves_completed_file(self):
        manager = DownloadManager(self.root / "delete.sqlite3", self.root / "delete-downloads", autostart=False)
        self.managers.append(manager)
        manager.add([KEY + "/keep.mp4", KEY + "/remove.zip"])
        keep, remove = [task["id"] for task in manager.snapshot()]
        completed = self.root / "delete-downloads" / "keep.mp4"
        completed.write_bytes(PAYLOAD)
        with manager.lock:
            task = manager.tasks[keep]
            task.update(desired="done", status="Downloaded", path=str(completed), size=len(PAYLOAD), size_final=True)
            manager.sent[remove] = "running"
        accepted = manager.delete_tasks([keep, "missing", remove, keep])
        self.assertEqual(accepted, [keep, remove])
        self.assertEqual(manager.snapshot(), [])
        self.assertEqual(manager.tasks[keep]["desired"], "removed")
        self.assertEqual(manager.tasks[remove]["desired"], "removed")
        self.assertTrue(completed.exists())
        self.assertEqual(manager.delete_tasks([keep, remove]), [])
        manager.add([KEY + "/remove.zip"])
        self.assertNotEqual(manager.snapshot()[0]["id"], remove)

    def test_delete_save_failure_restores_memory_and_retries(self):
        manager = DownloadManager(self.root / "delete-rollback.sqlite3", self.root / "delete-rollback-downloads",
                                  autostart=False)
        self.managers.append(manager)
        manager.add([KEY + "/rollback.mp4"])
        identifier = manager.snapshot()[0]["id"]
        with manager.lock:
            manager.sent[identifier] = "running"
            manager.samples[identifier] = (12.0, 3)
            manager.eta_samples[identifier] = deque([(1.0, 2)])
            task_before = dict(manager.tasks[identifier])
            sent_before = dict(manager.sent)
            samples_before = dict(manager.samples)
            eta_before = dict(manager.eta_samples)
            dirty_before, save_before, notify_before = manager.dirty, manager.last_save, manager.last_notify
            manager.wake.clear()
        with patch.object(manager.store, "save", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                manager.delete_tasks([identifier])
        self.assertEqual(manager.tasks[identifier], task_before)
        self.assertEqual(manager.sent, sent_before)
        self.assertEqual(manager.samples, samples_before)
        self.assertEqual(manager.eta_samples, eta_before)
        self.assertEqual((manager.dirty, manager.last_save, manager.last_notify),
                         (dirty_before, save_before, notify_before))
        self.assertFalse(manager.wake.is_set())
        self.assertEqual(manager.delete_tasks([identifier]), [identifier])

    def test_removed_inflight_retry_waits_for_status_verification(self):
        manager = DownloadManager(self.root / "delete-retry.sqlite3", self.root / "delete-retry-downloads",
                                  autostart=False)
        self.managers.append(manager)
        manager.add([KEY + "/retry-delete.mp4"])
        identifier = manager.snapshot()[0]["id"]
        manager.delete_tasks([identifier])
        manager.retrying.add(identifier)
        manager.retry_results.put((identifier, None))
        manager.listing = False
        manager.reconcile()
        self.assertIn(identifier, manager.tasks)
        self.assertIn(identifier, manager.verify_removals)
        self.assertEqual(manager.sent[identifier], "verify-remove")
        self.assertEqual(manager.outbox[-1][0], "GetRequestStatus")
        manager.handle("ProtocolError", {"Identifier": identifier, "Code": "15"})
        manager.reconcile()
        self.assertNotIn(identifier, manager.tasks)

    def test_delete_active_paused_failed_requests_keeps_foreign_request(self):
        manager = self.manager()
        manager.add([KEY + "/delete-active.mp4", KEY + "/delete-paused.mp4", KEY + "/delete-failed.mp4"])
        identifiers = [task["id"] for task in manager.snapshot()]
        manager.start(identifiers)
        self.wait(lambda: all(identifier in self.node.jobs for identifier in identifiers))
        manager.pause([identifiers[1]])
        self.wait(lambda: self.node.jobs[identifiers[1]]["PriorityClass"] == "6")
        self.node.fail(identifiers[2], 20, fatal=True)
        self.wait(lambda: manager.tasks[identifiers[2]]["desired"] == "failed")
        foreign = "other-app-selected-delete"
        self.node.jobs[foreign] = dict(URI=KEY + "/foreign.zip", Filename=str(self.root / "foreign.part"),
            PriorityClass="3", ReturnType="disk", Global="true", client="other-app", succeeded=7,
            completed=False, failure=None)
        self.assertEqual(manager.delete_tasks(identifiers), identifiers)
        self.wait(lambda: all(identifier not in self.node.jobs for identifier in identifiers))
        self.assertIn(foreign, self.node.jobs)
        self.assertTrue(all(identifier not in {task["id"] for task in manager.snapshot()} for identifier in identifiers))

    def test_offline_delete_persists_then_reconnect_removes_owned_request(self):
        manager = self.manager()
        identifier = self.new_download(manager, "offline-delete.mp4")
        foreign = "other-app-offline-delete"
        self.node.jobs[foreign] = dict(URI=KEY + "/foreign.zip", Filename=str(self.root / "foreign.part"),
            PriorityClass="3", ReturnType="disk", Global="true", client="other-app", succeeded=7,
            completed=False, failure=None)
        manager.close()
        self.managers.remove(manager)
        offline = DownloadManager(self.root / "queue.sqlite3", self.root / "downloads", autostart=False)
        self.managers.append(offline)
        self.assertEqual(offline.delete_tasks([identifier]), [identifier])
        offline.close()
        self.managers.remove(offline)
        persisted = QueueStore(self.root / "queue.sqlite3")
        try:
            self.assertEqual(persisted.load()[identifier]["desired"], "removed")
        finally:
            persisted.close()
        restored = self.manager()
        self.wait(lambda: identifier not in restored.tasks and identifier not in self.node.jobs)
        self.assertIn(foreign, self.node.jobs)

    def test_legacy_payload_migration_preserves_started_tasks_and_rowid_order(self):
        path = self.root / "legacy.sqlite3"
        database = sqlite3.connect(path)
        database.execute("CREATE TABLE metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
        database.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        pristine = {"id": "fld-a", "key": KEY + "/new.zip", "filename": "new.zip", "desired": "new",
                    "status": "Ready", "size": None, "fetched": 0, "fraction": 0, "speed": 0,
                    "selected": True, "path": None, "error": "", "retries": 0}
        started = {**pristine, "id": "fld-b", "key": KEY + "/paused.zip", "filename": "paused.zip",
                   "desired": "paused", "status": "Paused", "fetched": 4, "fraction": .4}
        database.executemany("INSERT INTO tasks VALUES (?,?)", [("fld-a", json.dumps(pristine)), ("fld-b", json.dumps(started))])
        database.commit()
        database.close()
        manager = DownloadManager(path, self.root / "legacy-downloads", autostart=False)
        self.managers.append(manager)
        rows = manager.snapshot()
        self.assertEqual([task["display_id"] for task in rows], [1, 2])
        self.assertEqual([task["ever_started"] for task in rows], [False, True])
        self.assertEqual(manager.remove_unstarted(), ["fld-a"])
        manager.close()
        self.managers.remove(manager)
        store = QueueStore(path)
        try:
            self.assertEqual(store.load()["fld-b"]["display_id"], 2)
            self.assertTrue(store.load()["fld-b"]["ever_started"])
        finally:
            store.close()

    def test_width_v2_migration_and_window_preferences_are_validated(self):
        path = self.root / "preferences.sqlite3"
        store = QueueStore(path)
        with store.db:
            old_widths = list(range(100, 111))
            store.db.execute("INSERT OR REPLACE INTO metadata VALUES ('column_widths', ?)", (json.dumps(old_widths),))
            store.db.execute("INSERT OR REPLACE INTO metadata VALUES ('window_preferences', ?)", ('{"width":true,"height":-4,"maximized":"yes"}',))
        store.close()
        manager = DownloadManager(path, self.root / "preferences-downloads", autostart=False)
        self.managers.append(manager)
        self.assertEqual(manager.column_widths, [50, *old_widths[1:]])
        self.assertEqual(manager.window_preferences, {"width": 1280, "height": 650, "maximized": False})
        self.assertEqual(manager.store.get_metadata("window_preferences"), '{"width":true,"height":-4,"maximized":"yes"}')
        widths = [60 + index for index in range(11)]
        manager.set_column_widths(widths)
        self.assertEqual(manager.store.get_metadata("column_widths"), json.dumps(old_widths))
        self.assertEqual(manager.store.get_metadata("column_widths_v2"), json.dumps(widths))
        manager.set_window_preferences({"width": 1000, "height": 700, "maximized": True})
        self.assertEqual(manager.window_preferences, {"width": 1000, "height": 700, "maximized": True})
        manager.close()
        self.managers.remove(manager)
        restored = DownloadManager(path, self.root / "preferences-downloads", autostart=False)
        self.managers.append(restored)
        self.assertEqual(restored.column_widths, widths)
        self.assertEqual(restored.window_preferences, {"width": 1000, "height": 700, "maximized": True})

    def test_width_migration_preserves_named_columns_and_v2_precedence(self):
        legacy12 = [100 + index for index in range(12)]
        cases = (
            ("legacy12", None, legacy12, [legacy12[0], *legacy12[2:]]),
            ("legacy11", None, list(range(200, 211)), [50, *range(201, 211)]),
            ("v2", list(range(300, 311)), list(range(400, 412)), list(range(300, 311))),
            ("invalid-v2-fallback", [True] * 11, list(range(500, 512)), [500, *range(502, 512)]),
            ("clamped-v2", [-500, 5000, *range(40, 49)], None, [40, 2000, *range(40, 49)]),
            ("malformed-v2-fallback", "{", list(range(600, 612)), [600, *range(602, 612)]),
            ("bad-width-values", [None, *range(1, 11)], [True, *range(1, 12)], None),
            ("malformed-legacy", None, "{", None),
            ("wrong-lengths", list(range(10)), list(range(10)), None),
            ("missing", None, None, None),
        )
        for name, v2, legacy, expected in cases:
            with self.subTest(name=name):
                path = self.root / f"{name}.sqlite3"
                store = QueueStore(path)
                metadata = {}
                if v2 is not None:
                    metadata["column_widths_v2"] = v2 if isinstance(v2, str) else json.dumps(v2)
                if legacy is not None:
                    metadata["column_widths"] = legacy if isinstance(legacy, str) else json.dumps(legacy)
                store.save(metadata=metadata)
                store.close()
                manager = DownloadManager(path, self.root / f"{name}-downloads", autostart=False)
                self.managers.append(manager)
                self.assertEqual(manager.column_widths, expected)
                manager.close()
                self.managers.remove(manager)

    def test_sort_preferences_validate_persist_by_name_and_preserve_task_fields(self):
        path = self.root / "sort-preferences.sqlite3"
        manager = DownloadManager(path, self.root / "sort-downloads", autostart=False)
        self.managers.append(manager)
        self.assertEqual(manager.sort_preferences, manager.DEFAULT_SORT_PREFERENCES)
        manager.add(["CHK@sort/false.mp4", "CHK@sort/true.mp4", "CHK@sort/missing.mp4"])
        identifiers = list(manager.tasks)
        manager.tasks[identifiers[0]]["selected"] = False
        manager.tasks[identifiers[2]].pop("selected")
        manager.store.save(manager.tasks)
        columns = sorted(manager.SORT_PREFERENCE_COLUMNS)
        for column in columns:
            for descending in (False, True):
                for running_first in (False, True):
                    value = {"column": column, "descending": descending,
                             "running_first": running_first, "ignored": "extra"}
                    manager.set_sort_preferences(value)
                    self.assertEqual(manager.sort_preferences,
                        {"column": column, "descending": descending, "running_first": running_first})
                    manager.close()
                    self.managers.remove(manager)
                    manager = DownloadManager(path, self.root / "sort-downloads", autostart=False)
                    self.managers.append(manager)
                    self.assertEqual(manager.sort_preferences,
                        {"column": column, "descending": descending, "running_first": running_first})
        for raw in ('{', '[]', '{"column":"unknown","descending":false,"running_first":false}',
                    '{"column":"file","descending":0,"running_first":false}',
                    '{"column":"file","descending":true,"running_first":null}',
                    '{}', '{"column":[],"descending":false,"running_first":false}',
                    '{"column":true,"descending":false,"running_first":false}'):
            manager.store.save(metadata={"sort_preferences": raw})
            manager.close()
            self.managers.remove(manager)
            manager = DownloadManager(path, self.root / "sort-downloads", autostart=False)
            self.managers.append(manager)
            self.assertEqual(manager.sort_preferences, manager.DEFAULT_SORT_PREFERENCES)
        self.assertFalse(manager.tasks[identifiers[0]]["selected"])
        self.assertTrue(manager.tasks[identifiers[1]]["selected"])
        self.assertNotIn("selected", manager.tasks[identifiers[2]])
        manager.close()
        self.managers.remove(manager)
        manager = DownloadManager(path, self.root / "sort-downloads", autostart=False)
        self.managers.append(manager)
        self.assertFalse(manager.tasks[identifiers[0]]["selected"])
        self.assertTrue(manager.tasks[identifiers[1]]["selected"])
        self.assertNotIn("selected", manager.tasks[identifiers[2]])

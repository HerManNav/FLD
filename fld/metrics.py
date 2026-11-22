"""Plain elapsed, ETA, and clock formatting helpers."""

import time


def clock_text(seconds):
    if seconds is None:
        return "--:--"
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def elapsed_seconds(task, now=None):
    elapsed = task.get("elapsed", 0)
    since = task.get("active_since")
    if task["desired"] == "running" and since is not None:
        elapsed += max(0, (time.time() if now is None else now) - since)
    return elapsed


def remaining_seconds(task):
    if task["desired"] == "done":
        return 0
    speed = task.get("eta_speed", task["speed"])
    if task["desired"] != "running" or task["size"] is None or speed <= 0:
        return None
    return max(0, task["size"] - task["fetched"]) / speed

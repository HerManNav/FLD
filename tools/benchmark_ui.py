"""Measure actual mapped-window scrolling with 150 synthetic tasks."""

import cProfile
import gc
import io
from pathlib import Path
import pstats
import sys
import tempfile
import time
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fld.app import App
from fld.queue_manager import DownloadManager


def main():
    with tempfile.TemporaryDirectory(prefix="fld-benchmark-") as folder:
        folder = Path(folder)
        manager = DownloadManager(folder / "queue.sqlite3", folder / "downloads", autostart=False)
        key = "CHK@" + "a" * 43 + "," + "b" * 43 + ",AAMC--8"
        manager.add([key + f"/Benchmark file {index:03d} with spaces.mp4" for index in range(150)])
        tasks = manager.snapshot()
        for task in tasks:
            task.update(desired="running", status="Downloading", size=1000000000,
                        size_final=True, fetched=300000000, fraction=.3, speed=125000,
                        active_since=time.time())
        root = tk.Tk()
        app = App(root, manager)
        root.title("FLD performance check - synthetic data")
        app.sync_tasks(tasks)
        root.update()
        for step in (1, 3):
            app.canvas.yview_moveto(0)
            root.update()
            profiler = cProfile.Profile()
            times = []
            profiler.enable()
            for index in range(80):
                if index % 4 == 0:
                    for number, task in enumerate(tasks):
                        offset = number * 1000000 if step == 3 else 0
                        task.update(fetched=300000000 + index * 10000 + offset,
                                    fraction=.3 + index / 100000 + offset / 1e9,
                                    speed=100000 + index * 1000 + number * 100)
                    app.sync_tasks(tasks)
                started = time.perf_counter()
                app.table.scroll_units(step if index < 40 else -step)
                root.update()
                times.append((time.perf_counter() - started) * 1000)
            profiler.disable()
            ordered = sorted(times)
            print(f"Mapped 150-row scroll, {step} rows/step: mean={sum(times)/len(times):.1f} ms, p95={ordered[int(len(times)*.95)]:.1f} ms, max={max(times):.1f} ms", flush=True)
            stream = io.StringIO()
            pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(8)
            print(stream.getvalue(), flush=True)
        app.close()
        app = root = None
        gc.collect()


if __name__ == "__main__":
    main()

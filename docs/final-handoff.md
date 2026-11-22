# FLD maintainer handoff

Internal map. Follow linked design docs before changing behavior. Keep this page
short; update evidence after each accepted source or release change.

## Runtime map

- Source: run `Start FLD.bat` or `python app.py`. Python + Tk required; no pip runtime deps.
- Frozen: `dist/FLD.exe`; Python bundled. Writable `queue.sqlite3`, `queue.lock`, `downloads/` beside EXE. Bundled icon stays read-only.
- Source data stays beside root `app.py`, never current working directory. Use `fld.paths`.
- Queue: `fld/queue_store.py` owns SQLite/process lock; `fld/queue_manager.py` owns UUID tasks, migration, state, retries, timers, recovery.
- GUI: `fld/app.py` owns Tk, scan thread, event polling, serialized action worker, tray, action scope. Tk only on GUI thread; network and SQLite off it.
- Scan: HTTP/HTTPS ordinary connection; Freenet fetch through local FCP/node only. Find adds unique keys. Remove unstarted cancels scan but keeps started tasks.
- Start submits durable request. Pause changes priority, keeps partial. Cancel removes node request, keeps row history. Retry preserves UUID. Exit leaves submitted requests alive; reopen reconnects.
- Local ownership only: control FLD UUIDs in its saved queue. Leave other node requests alone. Existing output never overwritten; completed files survive history cleanup.

## List, state, and storage

- `fld/gui_widgets.py`: one virtual body canvas; native header frame; three shared Start/Pause/Cancel controls; slots scale with visible height.
- Pointer row actions use raw canvas coordinates. Scroll anchoring uses the first visible UUID + intra-row offset. Explicit sort/group/filter starts top; passive state/membership/metric changes retain anchor; removed anchors clamp old offset.
- Arrow keys reveal focus. Wheel and Shift+wheel scroll. These are intentional movement.
- Row highlight is transient UUID set. Click, Ctrl/Shift, arrows, Space, Ctrl+A, Select all/none control highlights; Download selected and Delete selected use visible highlighted UUIDs. Hidden highlights survive filtering.
- Sort persists by named `sort_preferences`. Metadata width key is `column_widths_v2`, current 11 columns. Legacy widths convert on read; leave legacy key untouched. Window size/maximize also persist.
- X hides to tray. Tray restore returns window; tray/File Exit closes. Failed tray setup minimizes to taskbar.
- Size estimate uses output fraction until done. Elapsed counts desired-running time, excluding pauses. ETA uses rolling output progress. Graph keeps 120s of summed running speed; it continues in tray and resets per process.
- More detail: [development map](development.md), [list contract](list-fixes-design.md), [selection contract](selection-design.md), [release contract](release-design.md).

## Work and proof commands

- Source gate: `python -m unittest discover -s tests -v`. GUI fixtures use temporary DB/downloads and local fake nodes. Do not test against user queue or live node.
- Mapped list probe: `python tools/benchmark_ui.py`, 150 synthetic rows. Must run in visible Windows desktop.
- Native opt-ins: set `FLD_TEST_TRAY=1`, `FLD_TEST_WINDOW=1`; rerun full suite on Windows desktop.
- Demo: `python tools/demo_app.py --record .demo-capture\recording`; encode with `python tools/make_demo_gif.py ...` using bundled Pillow runtime. Demo uses in-memory toy data, no real queue/node.
- Build: 64-bit Python 3.15.2; `.venv-build`; pinned PyInstaller from `requirements-build.txt`; run `\.\build_windows.ps1`. Output `dist/FLD.exe`; build tools add no runtime deps.
- Frozen smoke: copy EXE alone to writable temp folder; run from unrelated cwd with Windows-only PATH: `FLD.exe --smoke-test REPORT.json`. Uses temp queue, no local node. Record report and SHA-256.
- Published release v0.1.1 in private repo. Local rebuild has same 0.1.1 PE metadata and is unpublished: `dist/FLD.exe` SHA-256 `d5309c4c84c359ad0d851920691f18f6c9302cf3321d4473d544b07e9c057a02` (15,225,783 bytes).
- Final Windows gate: 108 tests passed with tray/window opt-ins, no skips, 31.895s. Mapped 150-row benchmark: one-row 9.0ms mean / 11.1ms p95 / 12.7ms max; three-row with progress 10.9 / 13.9 / 21.0ms.
- Isolated copied EXE smoke passed: unrelated cwd, Windows-only PATH, frozen Python 3.15.2 AMD64 + Tk 9.0.4; executable-rooted data, icon, three controls/no Select, worker stopped, completed output sentinel intact, temp data removed. No live queue or node used.
- Root `FLD.exe` is occupied by running version. `dist/FLD.exe` uses a separate adjacent queue. After File > Exit, place rebuilt EXE beside root `queue.sqlite3` to reuse that queue; never overwrite a running EXE.

## Scroll report and remaining uncertainty

- Reproduced Tk bug: preservation fed a raw canvas origin into row anchoring. Canvas highlight/border inset made raw top `-1` while logical viewport top was `0`; raw bottom `4447` while logical bottom was `4448` in mapped 150-row probe.
- Remote tail reorder shifted logical top `0 → 32` and bottom `4448 → 4416`; at top leading synthetic row ID changed `150 → 149`. Middle logical positions `960` and `2400` stayed stable. Raw `959`/`2399` measurements alone hid the boundary error.
- Fix: `viewport_y()` reads `canvasy(highlight inset + border inset)` only for `rebuild_projection` and `apply_resize`. Drawing, hit testing, wheel/pointer paths remain raw. `move_to_y` sends a region-height fraction; Tk clamps actual lower bound.
- Focused mapped regression checks top/middle/bottom, ten tail swaps per position, UUID/offset/yview, drag/autofit, and out-of-range targets. Full Windows gate, benchmark, local build, and isolated smoke results appear above.
- User's intermittent real-world trigger remains unknown. A synthetic tagged-canvas MouseWheel delivered with root coordinates outside the window moved the middle viewport `959 → 1055` (three rows); direct canvas bindtag runs before descendant-check fallback. This proves handling of that delivered event only, not real OS event delivery or user's trigger.
- Window growth can clamp near bottom normally; middle resize stayed stable. Anchor removal, arrow reveal, wheel input, or reaching viewport bounds can also move content by design. Do not change those paths without new reproduction.

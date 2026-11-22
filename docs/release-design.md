# Select all rows, completed cleanup, and standalone Windows release

This is the concrete implementation and acceptance contract for Ctrl+A row
selection, Remove completed, and the first standalone Windows release. It
extends the existing [selection design](selection-design.md). Runtime production
code remains standard-library-only. Build dependencies are isolated from runtime
and from the user's installed Python.

## Scope and owners

- `fld/gui_widgets.py`: table-local Ctrl+A bindings and transient highlights.
- `fld/app.py`: Remove completed button, serialized worker action, pending state,
  acknowledgement, and errors.
- `fld/queue_manager.py`: authoritative completed-state filtering and reuse of
  durable UUID deletion.
- `fld/paths.py`: separate portable writable data from bundled read-only assets.
- `app.py`, a small dedicated smoke helper if needed, build configuration, and
  build instructions: repeatable standalone executable and actual-artifact proof.
- Existing app, manager, and runtime-path tests: focused regression coverage.

Do not change download protocols, parser behavior, durable schema, task UUIDs,
checkbox scope, default window size, or existing node ownership rules. Do not
bundle user data, the README animation, demo code, or tests. Do not introduce an
installer, updater, service, or runtime pip dependency. GitHub publication and
source commit/push are release operations handled separately from product code.

## Ctrl+A row highlights

Bind Ctrl+A to the body canvas and each of the four shared native row controls,
using the same table-local keyboard ownership as Up, Down, and Delete. Cover
Windows Control-a and Control-A forms. Return `"break"` to suppress a control
class binding. Do not add a root or global binding; source and filter entries
must retain their normal text-selection behavior.

The operation unions all UUIDs in the current visible projection into
`selected_ids`. It preserves previously selected hidden UUIDs, `anchor_id`, and
`focus_id` exactly, including absent or hidden anchors/focus. An empty projection
is a harmless no-op. Ctrl+A does not move the viewport, change details or native
control targets, write download-checkbox state, or invoke a manager action.
It uses the existing selection background update and callback rather than
rebuilding the projection or creating widgets. Later arrow/Shift gestures keep
the existing focus and hidden-anchor fallback rules.

The Select all button continues selecting download checkboxes. Ctrl+A selects
row highlights for the existing confirmed Delete selected path.

## Remove completed

Add **Remove completed** immediately after **Remove unstarted** on the second
action row, followed by **Delete selected**. Keep **Open download folder** at
the right. Retain the first action row and the existing small button gaps.
Verify the production DPI-aware 800x460 minimum with actual requested and
allocated button widths. If the row cannot fit, report the measured mismatch
before choosing a different layout; do not silently clip or shorten labels.

This operation is global: filename filtering, sorting, download checkboxes, and
row highlights do not narrow its scope. The authoritative criterion is current
`task['desired'] == 'done'`, not status text, percentage, filename existence, or
`ever_started`. New, running, paused, cancelled, failed, and already removed
records remain unchanged.

Implement `DownloadManager.remove_completed() -> list[str]`. Hold the manager's
existing RLock while collecting done UUIDs and calling `delete_tasks` with that
immutable list. Reentrant locking preserves atomic selection and deletion, so
no intervening task state change broadens the action. Reuse `delete_tasks` for
durable checkpoint acceptance, rollback on save failure, hidden tombstones,
owned-node cleanup, and preservation of completed output files. Do not duplicate
that lifecycle or directly pop completed records from the manager.

Like Remove unstarted, this dedicated cleanup button acts without a modal
confirmation. Completed cleanup removes list history and keeps output files.
Keep the existing default-No confirmation for Delete selected. Remove completed
does not cancel a scan, increment scan generation, cancel an active request, or
read/write the local node on Tk's thread.

Enable the button only when App's complete acknowledged rows contain at least
one done record, the app is not exiting, and completed cleanup is not pending.
Recompute this state after task sync, action acknowledgement/error, and exit
state changes. The GUI state is advisory: the manager always evaluates the
latest authoritative state when the serialized action executes. A zero-result
acknowledgement is valid. Guard repeated clicks while the action is pending.

Queue `remove_completed` on the existing action worker. Emit a distinct
`remove_completed_done` acknowledgement only after durable acceptance; return
the actual accepted UUIDs. Emit a dedicated `remove_completed_error` on failure.
Handle both before scan-generation filtering. Clear the pending guard on every
success or failure. Never optimistically hide rows.

Record acknowledged UUIDs in `App.removed_ids` before applying any coalesced
snapshot from the same poll. Clear their pending checkbox intentions, row
highlights, anchor/focus, and detail identity using existing UUID cleanup.
Preserve all surviving records and late scan additions. Re-added keys have new
UUIDs and remain visible. After acknowledgement show:

```text
Removed N completed task(s). Completed files were kept on disk.
```

N is the actual accepted count. Preserve the result through the acknowledgement
poll. On failure retain rows and show the existing `Error: <message>` form.
Do not alter the separate Remove unstarted scan-cancellation contract.

## Portable executable and runtime paths

Build a 64-bit Windows **FLD.exe**, version **0.1.0**, with a windowed one-file
PyInstaller build. Use the installed 64-bit Python 3.15.2 and pin the build-only
dependency to **PyInstaller==6.22.3** inside `.venv-build`. PyInstaller 6.21
introduced Python 3.15 support; 6.22 corrected embedded Tk 9 data discovery.
See the primary [PyInstaller changelog](https://pyinstaller.org/en/stable/CHANGES.html)
and [package metadata](https://pypi.org/project/pyinstaller/).

Keep a small repeatable Windows build entry point and checked-in build
configuration. Set the output name, windowed mode, ICO executable resource,
bundled ICO data, and matching 0.1.0 version information explicitly. Include
Python, Tcl/Tk, SQLite, standard-library modules, and their needed native runtime
through PyInstaller's supported analysis/hooks. The only application asset
required at runtime is `assets/fld.ico`; include it at that relative location.
Do not include the whole assets or repository directory.

Separate resource and data roots in `fld.paths`:

- Source execution keeps all existing paths beside root `app.py`.
- Frozen execution places `queue.sqlite3`, its lock, and `downloads/` beside
  `sys.executable`, independent of working directory.
- The icon resolves from the bundled resource tree using `__file__` (or the
  equivalent frozen extraction root), never from the executable's data folder.

Preserve the existing exported project-root meaning for source consumers; make
the data/resource distinction explicit without changing unrelated imports.
See PyInstaller's primary [runtime information](https://pyinstaller.org/en/stable/runtime-information.html).
No download, queue, or lock belongs in the temporary one-file extraction tree.
No automatic migration, copy, reset, or deletion of an existing database occurs.

This is a portable release: users extract or move the executable into a writable
folder and run it. Moving it beside an existing source-installation queue allows
normal reuse of that queue. Running it from a new folder creates a separate
queue and downloads folder. End-user README instructions must explain executable
startup without Python, the local-node requirement, and data location concisely.
Do not promise installation in protected system directories.

Ignore `.venv-build/`, `build/`, `dist/`, and local executable artifacts. Commit
source/build instructions/configuration; upload the executable to GitHub release
**v0.1.0** as a release asset. Include a SHA-256 checksum with the release so
the uploaded byte sequence can be verified. Do not add a binary to Git history.

## Actual executable smoke proof

A narrow diagnostic entry point such as `FLD.exe --smoke-test REPORT.json` is
acceptable to verify the actual frozen build. Its argument must be handled
before normal App startup, and normal no-argument startup remains unchanged.
Keep the helper independent of tests, demo tools, Pillow, and external services.

Create a temporary database/download directory and a real DownloadManager with
`autostart=False`. Construct the production App with that manager, open/map its
real Tk window, process its GUI loop, and close through the app's normal owned
shutdown. Do not connect to the local node or use the user's default queue. A
failed check exits nonzero and records a useful error in the report.

The smoke report records the frozen flag, embedded Python/Tk versions, resolved
executable/data/resource/icon paths, successful native icon loading, expected
controls, worker lifecycle, and clean shutdown. Exercise Ctrl+A and completed
cleanup against synthetic rows through the actual app/action-worker path, using
a temporary completed output sentinel and verifying its bytes remain intact.
Use bounded polling rather than sleeps on the GUI thread. Exit zero only after
all checks pass and the worker, manager, Tk session, and temporary data close.
Verify the bundled ICO with `LoadImageW` and release only that newly loaded
handle with `DestroyIcon`. Verify the mapped production window has an icon via
`WM_GETICON`, falling back to its class icon handles as documented by
[Microsoft](https://learn.microsoft.com/en-us/windows/win32/winmsg/wm-geticon).
Never destroy those borrowed window/class handles. Tk's `wm iconbitmap` getter
can be empty after setting the default icon, so that getter is not evidence of
load failure or success. Native icon failures must make the smoke test fail.

Run the built executable from an unrelated working directory with Python
directories removed from PATH. Place/copy it in an isolated writable directory
without source files or asset sidecars. Verify defaults resolve beside that
copied executable and the icon resolves inside the bundle. Capture successful
JSON output and the actual binary SHA-256. This proves execution using its
embedded runtime in the present machine environment; do not claim a clean-VM
test unless one was actually run.

## Acceptance conditions

1. Ctrl+A highlights all currently visible rows from canvas and each native row
   control; filtering preserves selected hidden UUIDs and sort order does not
   affect scope. Checkboxes, anchor, focus, viewport, details, and controls stay
   unchanged. Zero visible rows is harmless. Source/filter Ctrl+A selects text.
2. Completed cleanup removes only authoritative done UUIDs across a mixed-state
   queue, including hidden rows. Completed and preexisting unrelated files are
   byte-for-byte preserved. Active/paused/failed/new/cancelled records survive.
3. Manager coverage proves zero-result/idempotent behavior, durable failure
   rollback, offline reopen/tombstone safety, and reuse of existing owned-node
   cleanup without foreign request removal or late-event revival.
4. App coverage proves pending/exit enablement, no modal, unchanged scan
   generation/cancellation, action-worker serialization, stale snapshot/generation
   acknowledgement handling, fresh UUID preservation, and failure recovery.
5. Source runtime-path tests remain unchanged in meaning. Simulated frozen-path
   tests prove executable-rooted writable paths and separately bundled icon
   paths from an unrelated working directory. Imports remain inert.
6. Run `python -m unittest discover -s tests -v`. Run the existing native window
   and tray opt-ins where supported. Run `python tools/benchmark_ui.py`; retain
   four shared row controls and the existing visible-height slot bound.
7. Measure all action labels at the production DPI-aware minimum. Normal startup,
   icon, mapped app, both new actions, action worker, temporary durable queue,
   output preservation, and shutdown pass in the actual one-file executable.
8. A user can download **FLD.exe** from release **v0.1.0** and launch it without
   installing Python or pip packages. The local Freenet node remains required.
   Verify release visibility, uploaded bytes/checksum, committed source, and
   pushed branch/tag after publication. Report the private repository's access
   requirement accurately.

Implementation must record measured results separately from this contract.
Stop after these conditions pass; no new layout system, packaging framework,
settings, or unrelated cleanup is part of this release.

## Reproduce the Windows build

Use 64-bit Python 3.15.2. Create the isolated build environment once, install the
fully pinned build dependencies, then use the checked-in build entry point:

```powershell
C:\Python315\python.exe -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install -r requirements-build.txt
.\build_windows.ps1
```

The build script rejects a different interpreter version, architecture, or
PyInstaller version. It writes `dist\FLD.exe`; source runtime dependencies remain
unchanged. Run `dist\FLD.exe --smoke-test REPORT.json` for an isolated check.
For release proof, copy that executable to an otherwise empty writable folder,
start it from an unrelated working directory with a Windows-only PATH, and
inspect the JSON report. The diagnostic always uses its own temporary queue,
never the executable folder's existing queue or the local node.

## Verification results

The source gate passed all **104 tests** on 2026-11-22 with `FLD_TEST_TRAY=1`
and `FLD_TEST_WINDOW=1`, no skips, in **30.523 seconds**. New checks cover
Ctrl+A visible/hidden selection identity, native control and entry scope, empty
projection behavior, completed-state authority, preserved output bytes, durable
save failure, global action scope, pending/error/exit handling, stale
acknowledgements, and newly re-added UUIDs. Existing durable-deletion coverage
continues proving offline/reconnect, foreign-request preservation, and late-event
safety through the shared `delete_tasks` seam. Tests use temporary storage and
mock nodes; the user's queue/downloads are not test fixtures.

The mapped 150-task benchmark measured one-row scrolling at **9.5ms mean**,
**11.4ms p95**, and **11.8ms maximum**. Three-row scrolling with progress measured
**11.2ms mean**, **13.3ms p95**, and **13.5ms maximum**. The four shared native
controls and existing visible-height slot bound remain intact.

At the production DPI-aware **800x460** minimum, all second-row controls were
mapped and fully allocated. Actual/requested widths were **130/130px** for
Remove unstarted, **140/140px** for Remove completed, **113/113px** for Delete
selected, and **161/161px** for Open download folder. No layout exception was
needed.

The actual owned-window toy demo was refreshed to show Remove completed while
preserving its narrative. All **24 encoded 1280x720 frames** decoded successfully;
the GIF loops indefinitely (`loop=0`), lasts **21,910ms**, and occupies
**2,996,419 bytes**. Visual verification of the final frame confirmed eight rows,
two completed green progress bars, and the new button. The toy manager follows
the production cleanup interface and uses synthetic data without a live node.
These current artifact statistics supersede the earlier selection-design GIF
statistics, which remain a historical record.

The final one-file **FLD.exe** is **15,223,193 bytes**, reports ProductVersion
**0.1.0**, and embeds **Python 3.15.2 AMD64** and **Tk 9.0.4**. PyInstaller
archive inspection confirmed `assets/fld.ico`, `python315`, Tcl/Tk 9.0, and
SQLite runtime components. It found no Pillow, tests, demo tools/GIF, or user
database in the archive.

The actual binary was copied alone into `build/release-smoke/portable` and
launched from an unrelated working directory with PATH restricted to Windows
system directories. The smoke exited **0**. Its report confirmed frozen mode,
executable-rooted data paths, bundled ICO loading through Win32 `LoadImageW`,
and an available real-window icon through the documented class-handle fallback.
The production GUI mapped; the action worker started and stopped; Ctrl+A
selected one synthetic row; Remove completed acknowledged removal of that row;
the output sentinel's bytes survived; App and manager closed; temporary smoke
data were removed. All corresponding report checks were true. The manager used
temporary paths with `autostart=False`, so no live queue or local node was used.
This proves embedded-runtime execution in the tested Windows environment; a
separate clean VM was not used.

Final executable SHA-256:

```text
4d6c945be1101b033919a51f974cf7ab53ccce5fffc223009cd5a9b1d8b498fc
```

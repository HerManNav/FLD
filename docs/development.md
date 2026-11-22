# Development reference

Start every feature or fix with [the feature map](README.md), then read the
task-relevant sections linked here and in `change-design.md`. Reading unrelated
sections again is unnecessary. This reference describes the implemented
architecture; `change-design.md` preserves its behavior and acceptance contract.
Product implementation, checks, and verified demo GIF delivery are complete.
Maintain the index and
affected references alongside future changes. Keep the root README focused on
end-user startup and use; preserve engineering details here.

## Agent workflow

Use gpt-6.1-sol with high reasoning for design. Use gpt-6-luna with high reasoning
for implementation. Design first: read relevant code, write exact decisions and
acceptance conditions, then hand the contract to the implementation worker.
The implementation worker consults the feature map and relevant reference/design
sections before modifying code. Share exact section links and task-specific
decisions in the handoff instead of pasting whole reference files. The requested
model workflow applies to future chats, not just this change.

Check actual tool/model availability at runtime. Do not claim a model ran unless
it was accepted and performed the work. If an exact requested model cannot run,
tell the user and obtain approval for a concrete fallback before that phase.
Do not silently relabel another model as Luna or Sol. User-approved later model
choices override these defaults. Use normal English in all saved artifacts and
the caveman skill for conversation unless the user explicitly changes modes.

## Files and ownership

| File | Responsibility |
| --- | --- |
| app.py | Thin guarded launcher for `fld.app.main()` |
| fld/app.py | Tk window/layout, scan thread, action worker, event polling, tray lifecycle, action scope, details, graph sampling |
| fld/gui_widgets.py | Virtual DownloadList, shared native controls, canvas text/progress, column sizing, wheel routing, SpeedGraph |
| fld/metrics.py | Plain elapsed, ETA, and clock formatting helpers |
| fld/queue_manager.py | Task state, durable requests, reconnect/retry/finalize, timers/ETA, worker lifecycle |
| fld/queue_store.py | SQLite schema and checkpoints, metadata, cross-process lock |
| fld/node_protocol.py | Persistent FCP framing and local-node restart request |
| fld/key_parser.py | Key extraction, source normalization, safe filenames |
| fld/freenet.py | HTTP/local FCP page retrieval, limits, cancellation |
| fld/paths.py | Project-rooted runtime paths for downloads, queue, and icon |
| fld/windows_tray.py | Native Windows notification icon, native tray menu, duplicate notification handling |
| tests/fake_node.py | Local mock persistent FCP node and restart web endpoint |
| tests/test_app.py | Tk integration, scan responsiveness, list controls/rendering, tray integration through doubles |
| tests/test_queue_manager.py | Persistence, reconnects, controls, recovery, timing, lock behavior |
| tests/test_freenet.py | Extraction, source normalization, HTTP/FCP page-fetch behavior with local mocks |
| tests/test_runtime_paths.py | Working-directory-independent runtime paths and inert launcher/package imports |
| tests/test_windows_tray.py | Windows tray behavior; real shell test is opt-in |
| tools/benchmark_ui.py | Mapped 150-row scrolling with synthetic concurrent progress |
| tools/generate_icon.py | Rebuild app icon from included vector geometry |
| tools/demo_app.py | In-memory toy-data demo and own-window visual export; never uses the user's queue or node |
| tools/make_demo_gif.py | Tool-only Pillow encoder for captured BMP/PNG frames and recording manifests |

`Start FLD.bat` starts the app. Production requires only Python with Tkinter and
the standard library. Assets live beside the code. Resolve persistent paths
through `fld.paths`, never the terminal's working directory. Moving source into
`fld/` does not move `queue.sqlite3`, `queue.lock`, `downloads/`, or `assets/`:
they remain beside the root `app.py` launcher. The package initializer has no
startup side effects; the launcher calls `fld.app.main()` only when executed.

## GUI and thread boundaries

All Tk calls, Tk variables, widget creation, and destruction belong to the GUI
thread. App polls an event queue every 50ms and coalesces task snapshots, with
a bounded 1000-event poll batch. A separate single action worker serializes
manager actions and SQLite preference writes. Source scanning uses a separate
thread and queues scan_add through that same worker. Do not perform network
operations, lock waits, disk commits, or node shutdown on Tk's event thread.

Scan events carry a generation and cancellation event. Ignore old generations.
The scan busy indicator spans fetch, parse, and durable add; downloads continue
while scanning. Cancel all cancels scanning plus active/paused requests. The
Remove unstarted also invalidates the current scan, while preserving
started downloads. Find links adds unique keys instead of replacing the queue.

App retains complete row data and pending checkbox intentions so delayed
snapshots do not undo a click. Details are keyed by UUID, not displayed position.
Maintain these identities when sorting/filtering. Removal acknowledgements and
UUID tombstones prevent late snapshots from restoring removed rows; no
empty-queue barrier is required while started rows remain.

Transient row highlights use a separate UUID set, anchor, and focus. They never
change the persisted download checkbox. Selected deletion freezes visible UUIDs
before a default-No confirmation, then waits for durable action-worker
acknowledgement. It leaves scans running. Hidden selected UUIDs survive filtering
but are outside deletion scope. Acknowledgements precede scan-generation checks
and coalesced snapshots; errors and cancellation release the modal/pending guard.
See the [selection contract](selection-design.md#button-confirmation-and-acknowledgement).

One-second tick samples global active speed and refreshes progress/timers.
Selection and state changes render immediately. In the tray, data and graph
history continue while canvas painting is suspended. Release Tcl variables and
collect obsolete Tk sessions on the GUI thread.

## Rendering and interaction invariants

DownloadList draws only visible row graphics in one canvas. Off-screen records
remain dictionaries. Recycle slots and keep overlapping UUIDs in the same slot
while scrolling. Four native row controls are shared across the whole list:
checkbox, Start, Pause, Cancel. Other apparent row buttons/checkmarks are canvas
graphics. Never add one native widget per task or one canvas per row.

Natural column widths measure font/content and cap filename growth; persisted
widths override them. Divider drag is throttled to about 33ms; double-click
restores the natural width. Clamp stored widths to 40..2000 pixels. Text fitting
uses bounded caching, so pointer/scroll paths must not remeasure every filename.

Wheel routing covers canvas, controls, header, gaps, and focused-widget Windows
delivery. Fractional wheel deltas accumulate; Shift+wheel moves horizontally.
Up/Down moves the focused UUID and actual native-control target. Ctrl moves only
focus, Shift extends the row range, and Ctrl+Shift adds a range. Canvas and native
checkbox Space toggle the focused UUID's download checkbox once, independently
of hover. Right-click preserves an already selected group or selects one new
UUID before showing the shared Delete selected menu. Delete acts only while
the table or its native row controls have keyboard focus.
Hide overlays during scrolling, then restore them after a 150ms quiet interval.
Keep canvas press/release fallback working before native overlays appear.

The fixed clipped header frame stays above the scrolling body canvas, whose
first row starts at y=0. Header labels track horizontal scroll offset only.
Consult change-design.md before changing any offsets.
At 360px body height, the row pool remains at most 15; taller viewports may use
more visible slots, never all task records. Benchmark the real mapped window.

Current benchmark history on this PC: older widget-heavy scrolling averaged
178ms per one-row scroll; canvas reuse brought that to about 13ms. Three-row
scrolls with changing progress averaged about 16ms. These are reference
observations, not portable timing guarantees or a substitute for a fresh run.
After the queue-usability changes, the mapped 150-row benchmark measured 10.3ms
mean and 12.2ms p95 per one-row scroll. Three-row scrolling with concurrent
progress measured 12.5ms mean, 15.3ms p95, and 20.3ms maximum on this PC.

## Queue and persistence

Default state is queue.sqlite3 beside the root app.py launcher; the matching .lock prevents a
second instance from taking the stable FCP client identity. SQLite uses
synchronous=FULL. The version-1 database has metadata(name,value) and
tasks(id,payload); tasks are JSON payloads. `fld/queue_store.py` loads by rowid
and checkpoints the complete ordered task mapping transactionally.

`fld.queue_store.QueueStore` owns production SQL and the process lock. Its raw
metadata getter preserves stored strings for manager validation. Its `save`
operation can replace tasks and metadata atomically, or write metadata alone;
default metadata uses insert-if-absent behavior. `DownloadManager` owns the
meaning of metadata, task migration, display ID assignment, and notification
timing. It does not access the SQLite connection directly. Task additions and
their next-display-ID counter share one transaction; migration preserves that
same boundary and existing preference values.

Metadata includes the persistent client name, column widths, next_display_id,
and window_preferences. Task payloads include display_id and ever_started.
Keep UUIDs and all existing payload fields.
See the design for atomic counter assignment, legacy inference, and backward
11-column width compatibility. No migration should touch the live user queue
while testing; use temporary files or copies.

Task desired states are new, running, paused, cancelled, failed, done, and the
old internal removed state. Status is display text, not the authoritative state.
The manager owns task mutation under an RLock. Snapshot deep-copies plain data.
Checkpoint saves dirty data at most every two seconds during ordinary transfer,
forces commits for user controls and completion, and notifies at most once per
second unless forced. Socket writes go through an outbox after releasing the
state lock; a slow node must not stall GUI actions behind a socket write.

The user-facing Remove unstarted operation deletes only tasks which never
accepted Start. Completed files, paused partials, cancellations with history,
and failed attempts survive. Cancel discards node requests while retaining row
history. The obsolete blanket `clear` writer is removed; legacy saved `removed`
tasks still reconcile correctly. The unused manager-wide `select_all` writer
is removed; App's Select all and Select none use the visible UUID scope through
`select_many`.

Selected deletion marks exact UUID tombstones durably. Keep each tombstone until
owned local-node request cleanup is proved; never unlink a completed output.
An offline deletion survives restart. Failed SQLite acceptance restores the
affected in-memory task/timer/send/sample state before reporting an error.
Late node events cannot revive a tombstone. A pending retry retains its UUID
until post-retry request verification/removal, or a completed reconnect listing
proves absence. Re-adding the same key creates a fresh UUID. See the
[selection design](selection-design.md#durable-deletion-and-local-node-safety).

## Local node protocol and recovery

Freenet page keys and local localhost:8888 page URLs use the local node only.
Default FCP host/port is 127.0.0.1:9481; restart form endpoint is
http://127.0.0.1:8888/downloads/. Ordinary HTTP/HTTPS pages use the normal web
connection. Do not fetch Freenet content through public gateways.

Downloads share one persistent control connection and submit ClientGet with
ReturnType=disk, Persistence=forever, Global=true, MaxRetries=-1,
FilterData=false, and PriorityClass=2. The local node writes output directly
into downloads/.fld-parts/<uuid>.ready after the DDA write-access handshake.
WatchGlobal and ListPersistentRequests recover saved requests. Control only
UUIDs in FLD's saved database; ignore other applications' global queue entries.

Pause sets priority 6 while preserving partial node data. Resume restores
priority 2 on the same identifier. Already requested blocks may still finish
after pause. Cancel removes the node request and eventually the staging file.
Exiting leaves running node requests alive; reopening reconnects, resumes
monitoring, and collects completions. Pause/cancel while disconnected are
durable desired states applied on reconnect. Shutdown gives pending node
controls a bounded opportunity to complete, then saves state.

FCP has no restart-request message. Recoverable failures post only the failed
FLD identifier to the local node's native restart form using its current token.
Run this network work off the state lock and GUI thread. Retry backoff reaches
five minutes; connection backoff reaches 30 seconds. Permanent errors stop and
display full details; manual Start retries. There is no 30-minute deadline.

Completion reserves a collision-free target using exclusive create, commits
the reserved path, then atomically moves the staging file. Existing filenames
receive ` (1)`, ` (2)`, and so on. Never overwrite existing downloads. Crash
recovery must handle both a completed staging file without a node request and
a final rename completed before its done-state commit. Preserve unexpected
staging/output data and report a recovery error rather than destroying it.

Recovery depends on queue.sqlite3, Freenet persistent queue/storage, and the
staging directory. Old connection-only downloads cannot be adopted as durable
requests. The application saves bytes only and never executes downloaded files.

Protocol references retained from README:
[ClientGet](https://github.com/hyphanet/wiki/wiki/FCPv2-ClientGet),
[ClientHello](https://github.com/hyphanet/wiki/wiki/FCPv2-ClientHello),
[FCP priorities and persistence](https://github.com/hyphanet/wiki/wiki/FCPv2),
[GetRequestStatus](https://github.com/hyphanet/wiki/wiki/FCPv2-GetRequestStatus).
These links document prior implementation context; recheck primary sources
before making a protocol change.

## Parsing, filenames, and limits

Extract CHK, SSK, USK, and KSK from href values and visible text while preserving
first-seen order and deduplicating keys. Normalize URL-encoded keys. Separate
hrefs from labels and parse visible text by line; HTML block/line boundaries
matter. Plain-text filenames preserve spaces, including repeated spaces.
Recognized .mp4/.jpg/.rar/.zip endings bound filenames followed by descriptions.
Do not crawl links or run JavaScript. The page input limit is 16MiB.

source_key handles bare keys, freenet: prefixes, and recognized local port-8888
URLs. HTTP pages use incremental reads and cancellation. Freenet pages use a
local FCP fetch. Filename sanitization removes Windows-illegal characters,
protects reserved device names, and limits filename length to 160 characters.
Keep existing spaces and key parsing behavior when adding the view-only regex.

Pure key/source/filename rules live in `fld.key_parser`. `fld.freenet` retains
`FCPClient.fetch` for local-node page retrieval and HTTP scraping. The obsolete
connection-scoped file-download wrapper is removed; real downloads continue
through the durable manager and node-owned disk staging path.

## Metrics

One MB is 1,000,000 bytes. Size may be unknown or estimated until finalized.
fetched bytes are an estimate derived from fraction and output size during a
transfer, not a measurement of a sequentially growing file. Compression,
metadata, and error correction make network traffic different from output size.
After completion, saved size and fetched bytes are exact.

Elapsed time accumulates while desired running, including waits/retries, and
excludes pauses. Persist elapsed plus active_since. It can count time while FLD
is closed until node-reported completion; PC-off time cannot be distinguished.
Legacy already-completed records may have unknown durations. ETA uses a rolling
60-second output-progress average and requires five seconds after start/resume.
Include stalls in that window. Unknown size, paused state, or no positive
average gives --:--. A brief recent-speed dropout need not erase ETA.

The graph keeps 120 seconds of summed desired-running speed samples and adapts
its labeled numeric MB/s axis. It continues in the tray and resets per process.
It is not a measurement of all PC or node network traffic. Freenet bandwidth,
peers, availability, and scheduling determine actual download throughput. Do
not change node limits or advertise faster downloads from table optimizations.

## Window and tray

X and File > Hide to tray keep the process alive. Tray click restores; native
menu offers Restore and Exit. File > Exit really exits. Tray failure minimizes
to the taskbar. Duplicate Windows tray notifications must not reopen stale menus.
Native tray integration needs an interactive Windows desktop; mocks are enough
for routine Tk integration tests.

Window preferences preserve normal size plus maximized
state, exclude minimized/hidden state, write asynchronously, and flush before
hide/exit markers. A bottom-right native Sizegrip remains beside the footer.
The grip is hidden while maximized. A guarded programmatic restore preserves
the intended maximize state through deiconify and the following idle cycle;
transient normal Configure events cannot replace it. Read the design's Configure
transition rules before modifying this lifecycle.

## Verification commands and evidence

Run the complete suite after download or parsing changes, and for this feature:

```powershell
python -m unittest discover -s tests -v
python tools/benchmark_ui.py
```

Tests use temporary storage and local mock HTTP/FCP servers. They must not
download third-party files or change Freenet configuration. Existing coverage
includes pause/resume, reconnect/retry, abrupt process exit, duplicate target
names, final-file recovery, controls, touchpad wheel routing, scan heartbeat,
graph/timers/ETA, width persistence, and tray doubles. GUI tests create Tk on
their test thread and collect it there.

To opt into the actual Windows shell tray check and mapped window preferences:

```powershell
$env:FLD_TEST_TRAY = '1'
$env:FLD_TEST_WINDOW = '1'
python -m unittest discover -s tests -v
```

The selection/deletion/icon gate on 2026-11-22 passed all 95 tests with both
Windows opt-ins enabled and no skips in 28.308 seconds. Coverage includes row
gestures and keyboard identity, shared context-menu scope, frozen modal UUIDs,
No/error/exit paths, stale acknowledgements, transactional deletion rollback,
owned active/paused/failed request cleanup, foreign-request preservation, offline
deletion/restart, and post-retry removal verification. All fixtures use temporary
storage or local fake nodes; no user queue, download, or node setting was changed.

The mapped 150-task selection gate measured:

| Scroll workload | Mean | p95 | Maximum |
| --- | --- | --- | --- |
| One row | 10.6ms | 13.5ms | 14.7ms |
| Three rows with progress | 11.7ms | 14.1ms | 15.1ms |

The preceding refactor three-row mean was 11.6ms. These measurements show no
material regression on this PC and are not portable timing guarantees. The
virtual canvas still uses four shared native row controls and a visible-height
slot pool. The production DPI-aware 800x460 layout probe verified two action
rows with full button labels; Open download folder measured 161px actual and
161px requested, replacing the clipped 120px one-row result.

The two icon-generator tests passed deterministic regeneration and generated
asset checks. Visual inspection accepted 16/24/32/48/64px ICO frames on light and
dark surroundings, and the native Windows ICO loading check passed. The SVG,
PNG, and ICO share the same included vector geometry and no new runtime library.
The feature contract and consolidated results are in the
[selection design](selection-design.md#verification-results).

The prior refactor verification on 2026-11-22 passed all 81 tests with both
flags enabled and no skips in 24.520 seconds. Native tray and mapped window
geometry integrations passed. Added coverage precedes the structural changes;
retired wrapper-only tests have explicit active-fetch/durable-queue replacements.
The phase results and cleanup inventory are recorded in
[the refactor design](refactor-design.md#implementation-gates).

The final mapped 150-task benchmark showed no material regression against its
fresh pre-refactor baseline on this PC:

| Scroll workload | Before mean / p95 | After mean / p95 / maximum |
| --- | --- | --- |
| One row | 9.9ms / 11.3ms | 9.9ms / 11.1ms / 17.4ms |
| Three rows with progress | 11.7ms / 12.9ms | 11.6ms / 13.0ms / 18.7ms |

Archive-based AST review found no unintended semantic changes. Moved parser,
page-fetch, metric, protocol, and tray definitions retain their original bodies;
manager differences are limited to the approved SQL ownership boundary.
Application/list changes match the header labels, entry point, and proven dead
definitions. Runtime paths retain their original root locations, the batch
launcher is byte-identical, and production SQL is confined to QueueStore.
The refactored demo recording and refreshed GIF also passed verification, as
described below. Final cleanup removed only verified bytecode caches beneath
the source/test/tool folders and this run's `.demo-capture` scratch, after
checking workspace containment and the absence of reparse links. User queue,
lock, downloads, and recovery files were untouched. At that refactor gate, no
Git repository had been initialized or pushed.

The earlier queue-usability feature run passed all 71 tests with both flags enabled and no skips
in 22.487 seconds. The native window test verified real mapped 1020x610 client
size, maximize, tray hide/restore to zoomed, close/reopen to zoomed, return to
1020x610 normal size, preference persistence, and grip visibility. This covers
native geometry/state transitions, not a physical mouse drag of the resize grip.
The shell tray opt-in also passed. Temporary storage and mock nodes isolated
these tests from the user's queue and downloads. The actual-window demo GIF is
delivered and verified separately from the product tests.

Prior live checks against Freenet build 1507 verified durable disk downloads,
paused-request restoration, a byte-for-byte 890,142-byte completion, and the
native retry preserving request identity. Those checks did not reboot the PC
or restart the user's service. Automated tests simulate node reconnects and
crashes; do not describe them as proof of a real OS reboot.

For the requested demo, follow change-design.md's in-memory harness and actual
GUI capture plan. Pillow is an artifact-building tool available through the
bundled runtime, not an application dependency. Keep synthetic demonstrations
separate from real-node verification and never expose private filenames in GIFs.

Reproduce the Windows recording from the project root. Resolve `$pillowPython`
to the Python executable supplied by `load_workspace_dependencies`; that bundled
interpreter provides Pillow (13.0.0 for this export). It is a build-tool dependency
only, not a requirement for running FLD or the recording harness.

```powershell
python tools\demo_app.py --record .demo-capture\recording
& $pillowPython tools\make_demo_gif.py .demo-capture\recording\manifest.json --output assets\demo.gif
```

The recording command creates BMP frames and a duration manifest. Keep this
scratch recording out of deliverables; retain the encoded GIF and demo tools.

The current `assets/demo.gif` was refreshed for the rabbit icon and two action
rows, preserving the existing demo narrative and `Elapsed (min)`/`ETA (min)`
headings. The `--record` run produced 24 frames over about 22 seconds. The encoded
GIF contains 24 decoded 1280x720 frames, loops indefinitely (`loop=0`), lasts
21,880ms, and occupies 2,855,027 bytes. Its source frames came from the demo's
actual owned Tk window; every encoded frame decoded successfully. The encoded
final frame was visually verified: the rabbit caption icon, both action rows,
Delete selected, and both minute headings are present; two rows have completed
green bars, six rows are Ready, completed elapsed values are 12s and 13s, and
the graph reads 0MB/s. The earlier recording's empty, scanning, and filtered
progress states were already visually verified; this refresh changes only the
visual asset and layout. The root README embeds the current GIF. Recording
scratch is build-only; the reproducible demo/export tools remain.

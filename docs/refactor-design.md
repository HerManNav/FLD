# Source organization and preservation plan

Status: complete. Source refactoring, archive-based semantic review, all 81
native-enabled tests, the mapped benchmark, the refreshed demo, and verified
generated-artifact cleanup passed. This design was completed before
implementation; the gate table records its results.

## Implementation gates

| Phase | Verification | Result |
| --- | --- | --- |
| Header feature | Full suite; mapped 150-row benchmark | 71 run, 69 pass, 2 native skips. One-step 10.2ms mean/12.1ms p95/13.6ms max; three-step 12.0ms/13.6ms/21.7ms. Pre-header baseline: 9.9ms and 11.7ms means. |
| Added behavior coverage, before retirements | Full suite with old APIs/tests still present | 84 run, 82 pass, 2 native skips. |
| Legacy API retirement | Full suite | 78 run, 76 pass, 2 native skips. Six wrapper-only tests replaced by active fetch and durable-queue coverage. |
| Package move and paths | Full suite; unrelated-working-directory subprocess import | 80 run, 78 pass, 2 native skips; subprocess left working directory empty. |
| QueueStore extraction | Queue suite; full suite | 35 queue tests pass; 81 run, 79 pass, 2 native skips. |
| Node protocol extraction | Full suite | 81 run, 79 pass, 2 native skips. |
| Key parser extraction | Full suite | 81 run, 79 pass, 2 native skips. |
| Metric helper extraction | Full suite | 81 run, 79 pass, 2 native skips. |
| Superseded-source and dead-definition cleanup | Full suite; import/call-site audit | 81 run, 79 pass, 2 native skips. Old root implementations removed after consumer migration; approved unused definitions removed. |
| Final semantic review | Compare moved AST definitions with the original source archive; audit imports, SQL, paths, and launcher | No findings. All differences match the approved extraction, storage API, labels, startup entry point, and deletion inventory. `Start FLD.bat` is byte-identical; production SQL is confined to QueueStore. |
| Final native-enabled suite | Both `FLD_TEST_TRAY=1` and `FLD_TEST_WINDOW=1`; full suite | All 81 tests passed, no skips, in 24.520s. Native tray and mapped geometry integrations passed. |
| Final mapped performance | 150 synthetic tasks, one-step and three-step scrolling | One-step: 9.9ms mean, 11.1ms p95, 17.4ms max. Three-step: 11.6ms mean, 13.0ms p95, 18.7ms max. Pre-refactor means were 9.9ms/11.7ms, p95 11.3ms/12.9ms; no material regression observed. |
| Refreshed demonstration | Refactored in-memory recorder; decode every GIF frame; inspect encoded final frame | 24 frames at 1280x720, 21,890ms, loop=0, 2,951,294 bytes. Final frame shows the new unit headings, two completed bars and six Ready rows, 11s/12s elapsed, and 0MB/s. |
| Final cache/scratch cleanup | Verify workspace paths, cache-only contents, and absence of reparse links before deletion | Removed root/fld/tests/tools generated caches and this run's `.demo-capture` scratch. User queue, lock, downloads, and recovery files were untouched. |

## Scope and acceptance boundary

First change the displayed headings to `Elapsed (min)` and `ETA (min)`. Preserve
the existing `MM:SS` values, elapsed/ETA definitions, column order, sorting,
column-width metadata, and all other behavior. This is a separate feature phase
before structural changes. The parent task should update the two existing header
assertions and the affected column contract in `change-design.md`.

Then organize the production code into a small `fld` package and extract four
existing responsibilities. Preserve supported function/class names and signatures, task
payloads, SQLite schema/version, errors, event ordering, locks, and thread
ownership. Do not rewrite the download state machine or virtual list. No new
production dependency, plugin framework, configuration format, database
migration, executor framework, or repository initialization is needed.

The user plans GitHub setup later. Do not initialize Git, commit, or push here.

The launch contract remains `python app.py` and `Start FLD.bat`. Runtime data
remains beside the root launcher: `queue.sqlite3`, `queue.lock`, `downloads/`,
`downloads/.fld-parts/`, and `assets/`. Imports must not instantiate Tk, connect
to a node, open a database, or create runtime directories. Never exercise default
manager construction against the real queue during tests.

All Tk work remains on the GUI thread. Existing scanning, the serialized action
worker, download monitoring, retry requests, and SQLite writes retain their
current worker ownership. This refactor does not add blocking work to callbacks.
Preserve current startup behavior rather than redesigning startup threading.

## Source and usage audit

Before this refactor, the root modules mixed a few independently useful concerns:

* `app.py` owns application UI and lifecycle plus the serialized action worker.
* `gui_widgets.py` owns the single-canvas list, a small speed graph, and three
  plain metric-formatting helpers.
* `queue_manager.py` owns durable download state plus SQLite/process locking,
  persistent FCP framing, and the local-node HTTP restart action.
* `freenet.py` owns key/filename parsing plus page fetching and a direct-stream
  FCP client.
* `windows_tray.py` owns the Windows notification icon and menu.

Production imports show `scrape` calls `FCPClient.fetch`; that client is required
even though durable downloads use `DownloadManager`. Do not remove the client
as obsolete. Its `download` method has no production or tool callers: only
five tests use its old connection-scoped file writer. Retire that writer after
moving its useful guarantees to current page-fetch and durable-download tests.
`DownloadManager.clear` likewise has no application/tool callers; only two tests
invoke the old blanket removal. Retire it and port those assertions to the
current Cancel operation. The `removed` state remains relevant to saved legacy
payloads and must remain readable and recoverable independently of that writer.
`DownloadManager.select_all` has no callers; App's visible-scope Select all uses
`select_many`. Remove the unused manager method and matching unused `clear` and
`select_all` methods from `DemoManager`. Keep the actual App selection controls.

These retirement decisions are based on repository-wide explicit call-site and
dynamic action dispatch inspection, not test counts. The app is the supported
product; these old internal methods are not documented extension APIs.

`tools/demo_app.py` patches the application module's `scrape` and `DOWNLOAD_DIR`
and subclasses `App`. Tests also patch application and clock module globals.
Update those targets to their actual new owners; a shallow root re-export cannot
silently stand in for the application module when patching globals.

The demo's Windows BMP capture/exporter and command-file control are used by
documented recording workflows. `tools/make_demo_gif.py` uses Pillow only for
artifact encoding. Keep these tools and their behavior. Keep `tests/fake_node.py`
and the direct-stream server in `tests/test_freenet.py`: they test distinct
persistent-disk and connection-stream protocols.

## File migration and ownership

Use this concrete structure. Retain a flat package rather than introducing
single-file subpackages. Keep tests and developer tools in their existing folders.

| Current owner | Final owner | Change |
| --- | --- | --- |
| `app.py`: `App`, `action_worker` | `fld/app.py` | Move implementation intact; expose `main()` for startup. |
| `app.py`: startup block | root `app.py` | Thin guarded launcher calling `fld.app.main()`; tools/tests import the implementation explicitly. No compatibility re-export layer is needed. |
| `gui_widgets.py`: `CanvasValue`, `DownloadList`, `SpeedGraph` | `fld/gui_widgets.py` | Move intact; keep native control and rendering ownership together. |
| `gui_widgets.py`: `clock_text`, `elapsed_seconds`, `remaining_seconds` | `fld/metrics.py` | Extract unchanged plain helpers; import explicitly from the widget module. |
| `queue_manager.py`: `DownloadManager` | `fld/queue_manager.py` | Keep state machine, task migration, metrics sampling, finalization, retry scheduling, and worker lifecycle together. |
| `queue_manager.py`: `QueueStore` | `fld/queue_store.py` | Extract SQLite and cross-process lock ownership unchanged. |
| `queue_manager.py`: `FCPConnection`, `restart_node_request` | `fld/node_protocol.py` | Extract persistent transport/framing and local-node restart helper unchanged. |
| `freenet.py`: `PageText`, patterns, `extract_keys`, `source_key`, `filename_for` | `fld/key_parser.py` | Extract pure parsing/filename behavior unchanged. |
| `freenet.py`: cancellation, `FCPClient.fetch`, `scrape`, `PAGE_LIMIT` | `fld/freenet.py` | Retain connection-stream/page behavior together; import parsing helpers. Retire the separate legacy `download` file writer. |
| `windows_tray.py` | `fld/windows_tray.py` | Move intact. |
| module-relative path constants | `fld/paths.py` | Define `PROJECT_DIR = Path(__file__).resolve().parent.parent`, then `DOWNLOAD_DIR`, `STATE_PATH`, and `ICON_PATH` beneath it. |
| none | `fld/__init__.py` | Package docstring only; no eager GUI/network imports. |
| tests/tools imports and mock targets | existing test/tool files | Use `fld.*` owners, including subprocess snippets and demo patches. |
| root implementation files other than `app.py` | removed after migration | Delete the old duplicate sources only after every internal consumer is migrated and tests pass. |

Use explicit relative production imports within `fld`. Do not introduce
`sys.path` changes in production. Existing tools may continue adding the project
root for direct script execution. Retain `Start FLD.bat` byte-for-byte unless a
verified launcher problem requires a separately scoped fix. Keep persisted
paths absolute and rooted at the original project directory.

Do not extract sorting/filter projection, geometry restoration, row control
targeting, finalization, or task migration in this pass. These are stateful
boundaries with substantial existing interaction contracts. The chosen splits
remove distinct storage/protocol/parsing concerns without redesigning them.

## Complete the storage boundary

Moving `QueueStore` alone leaves duplicated SQL in manager startup, task
migration, additions, and preferences. After the class move, complete this one
bounded ownership improvement: production SQL belongs to `QueueStore`, while
the manager retains JSON decoding/validation, metadata key names, task order,
ID assignment, migration policy, notification timing, and its existing locks.

Use exactly these small operations rather than adding a repository framework:

* `get_metadata(name)` returns the raw stored string, or `None` when absent.
  Parameterize the metadata name. Keep existing invalid/missing JSON handling in
  the manager; moving reads must not normalize or overwrite raw stored values.
* Extend existing `save` to
  `save(tasks=None, *, metadata=None, defaults=None)`. Metadata/default mappings
  contain already-encoded strings, matching the current SQLite representation.
  In one existing-style `with self.db` transaction, replace ordered tasks when
  `tasks is not None`, write replacement metadata with `INSERT OR REPLACE`, then
  defaults with `INSERT OR IGNORE`. An empty task mapping clears tasks; `None`
  leaves them alone. `save(tasks)` preserves its original behavior.

Map startup metadata reads to the getter. Migration calls
`save(self.tasks if changed else None, metadata={"next_display_id": json.dumps(next_id)}, defaults={"window_preferences": json.dumps(self.window_preferences)})`.
Adding calls `save(self.tasks, metadata={"next_display_id": json.dumps(self.next_display_id)})`.
Window/width setters call metadata-only `save`, preserving their update-after-
commit order. Ordinary checkpoints remain `save(self.tasks)`. Schema, client
identity initialization, FULL synchronization, transaction statement order,
exception propagation, and process-lock behavior remain unchanged. Production
`DownloadManager` should no longer access `store.db`; tests may use temporary
database connections for fixtures, triggers, and durable-state assertions.

Before this SQL encapsulation edit, characterize the manager's atomic behavior
using temporary databases: force a `next_display_id` metadata insert failure
with a SQLite trigger during `add` and during legacy migration. Verify that the
previous persisted ordered tasks and counter remain together, rather than a
partial new task set. Inspect disk state before normal cleanup can perform a
later checkpoint. Tests may explicitly own a supplied/patched temporary store
for failed construction so its lock can be closed. Preserve existing in-memory
exception behavior; changing failure recovery is a separate fix. Also verify
that width/preferences survive reopening and migration's ignored default does
not rewrite an existing raw preference value. Reuse existing cases where they
already prove these conditions. Run the whole suite before and after this
additional boundary, then verify no production `store.db` references remain.

## Test audit and required additions before structural edits

The existing 71-test suite already covers durable pause/resume/reconnect/retry,
crash identity, duplicate destinations, completed-file recovery, display IDs,
legacy metadata, timing/ETA, node-request isolation, scan responsiveness,
sorting/filtering, tombstones, native-control reuse, wheel routing, geometry,
tray doubles, and optional native Windows checks. Preserve these assertions.
The tests below address failure and boundary gaps; they are not count targets.

Write the following tests against the current owners and run them successfully
before moving production code. After moves, change imports/mock targets only,
then run the same behavioral proof again. Use temporary directories, scripted
socket doubles or loopback servers, and bounded events; never the user's data.

| Area | New meaningful proof | Preserve after move |
| --- | --- | --- |
| Runtime paths | Import production constants in a subprocess with an unrelated working directory; paths resolve to the project root and import creates no runtime files in that directory. | Extend to `fld.paths`; root launcher import remains inert. |
| Store transaction | A checkpoint failing partway through inserts rolls back to the complete previous ordered task set, including payload data. | Exercise extracted `QueueStore` with the same temporary SQLite database. |
| Store compatibility | Reject an unsupported future `user_version` without changing it; release the process lock so a subsequent valid open succeeds. | Keep identical error and lock behavior. |
| Persistent FCP framing | Fragment a message across a socket timeout, then deliver the remainder and a second message; return complete messages once and in order. | Exercise extracted transport using a deterministic socket double. |
| Persistent FCP rejection | Reject invalid/unexpected data framing and newline-bearing outgoing fields; do not send injected fields. | Keep errors and wire output assertions. |
| HTTP page limit | Loopback HTTP page exceeding 16 MiB fails with the existing limit error and never returns extracted keys. | Same `scrape` test after extraction. |
| HTTP cancellation | Cancel while incremental HTTP reads are in progress; scraping stops with `Cancelled` and does not add later keys. | Same cancellation boundary. |
| HTTP decoding | Declared non-UTF-8 charset and unknown-charset fallback preserve expected key filenames. | Same decoded keys and order. |
| Snapshot ownership | Mutating a returned task snapshot cannot alter manager-owned nested task data or durable state. | Same manager API; no reliance on implementation class locations. |
| Action worker | A failing manager action reports an error, calls `task_done`, then processes the next action and stop normally. Scan-add cancellation never calls `add`. | Same serialized worker with queues/events; no Tk required. |
| Recovery data safety | A nonempty reserved destination conflicting with a ready staging file leaves both contents untouched and reports a recoverable save failure. | Same manager finalization behavior; no real node. |
| Durable naming | Complete several started requests with the same output filename; preserve a preexisting file and every payload under distinct suffixed names. | Replace the legacy direct-writer concurrency test's user-data guarantee on the real product route. |
| Durable cancellation | Cancel an active staged task and a completed task: active node request/stage are removed, completed file remains, row history and foreign requests survive. | Replace blanket-clear tests with current Cancel behavior. |
| Legacy removal recovery | Load a saved `desired='removed'` task and reconcile it without calling `clear`; remove its own request/tombstone while preserving completed files and foreign requests. | Retain old persisted-state compatibility after retiring the writer method. |

Tests can group related cases with subtests. Prefer existing test fixtures over
new generic helpers. Add only small shared doubles if they clarify ownership.
If a characterization test exposes an existing bug, stop the structural phase,
report the evidence, and handle that fix separately with its own acceptance;
do not weaken the preservation test to make the refactor pass.

For the header feature, assert the visible heading labels and existing `MM:SS`
cell values. Exercise natural width and a stored 12-column width list to prove
that appending units does not shift persisted columns. Existing elapsed/ETA
behavior tests remain authoritative.

## Phases and gates

1. Record baseline suite results, native opt-in availability, and mapped
   150-task benchmark before product edits. Complete the header-only change,
   its assertions, and affected documentation; rerun the suite and benchmark.
2. Add the gap coverage above against the current architecture. Add direct
   `FCPClient.fetch` characterization for byte integrity/progress, node failure,
   truncation, and cancellation while the old download tests still pass.
   All new tests and the complete suite must pass before structural source moves.
   Then retire only the legacy APIs listed in the cleanup inventory, deleting
   their obsolete wrapper-only tests after equivalent active-route proof exists.
   Run the full retained suite again before beginning the package move. Record
   replaced/added cases explicitly so a lower raw count cannot hide lost proof.
3. Introduce `fld/` and centralized path constants, move the existing five
   implementation modules, update consumers, and keep the root launcher. Run
   the full suite plus the unrelated-working-directory path/import checks.
4. Extract `QueueStore`, then protocol/restart helpers, then pure key parsing,
   then plain metric helpers. Run the full suite after each ownership boundary.
   Keep each intermediate state importable and runnable. Do not combine a
   behavior fix with a move. Complete the storage boundary above as one separate
   step after its transaction characterization tests pass.
5. Remove superseded duplicate source files only after all imports, subprocess
   snippets, mock targets, demo patches, and documentation reference the actual
   package owners. Update the feature map and development inventory. Verify
   the demo CLI and actual in-memory demo startup without a node or user queue.
6. Run the final complete suite, both supported native Windows opt-ins, and
   mapped `python tools/benchmark_ui.py`. Inspect the actual headings. Record
   measured results and limitations in the development reference. Clean only
   verified generated artifacts after verification.

Acceptance requires exactly four shared native row controls, one body canvas,
at most 15 visible slots at a 360px body height, and no widget count proportional
to 150 tasks. Compare benchmark results on this machine to the parent task's
fresh baseline; investigate a reproducible regression rather than treating the
historic approximately 13ms observation as a portable hard threshold.

The final review must confirm all retained original behavior tests, explicit
active-route replacements for retired-API tests, and the added gaps,
the unchanged root launch paths, standard-library-only production imports,
unchanged persistent schema/identity/locations, and no user-data modification.
The package and extractions above are the stop condition. Additional rewrites
or packaging/distribution systems are outside this cleanup.

## Cleanup inventory and retention decisions

| Item | Evidence and action |
| --- | --- |
| Root/tests/tools `__pycache__`, `*.pyc` | Generated interpreter caches observed. Remove only verified paths inside the workspace at the end; tests may recreate them. Existing ignore rules already cover them. |
| Old root `freenet.py`, `gui_widgets.py`, `queue_manager.py`, `windows_tray.py` | Remove only once their package replacements and all consumers pass. These are moved source, not obsolete behavior. |
| `.demo-capture/` and recording frames/manifests | Documented generated scratch output; not observed during design inventory. If present at implementation time, verify it is recording output before removal and add `.demo-capture/` to ignore rules. |
| `queue.sqlite3*`, `queue.lock`, `downloads/` | User runtime/recovery state. Never remove, migrate, open for tests, or classify as temporary. A lock file's existence does not establish staleness. |
| `assets/fld.ico`, `assets/fld.png`, `assets/demo.gif` and icon/demo generators | Product and reproducible artifact assets/tools. Keep. Do not regenerate the GIF solely for this source move. |
| `FCPClient.fetch` | Required page retrieval, cancellation, progress, redirects, framing, limits. Keep and test directly. |
| `FCPClient.download` | Only old tests call the connection-scoped file writer. Remove after direct-fetch characterization and persistent naming/cancellation/data-safety coverage pass. Remove its now-unused imports. |
| `DownloadManager.clear` | Only two tests call the retired blanket-delete API. Remove and port useful assertions to current Cancel behavior. |
| `removed` desired state | Existing persisted tasks can contain this state. Preserve migration/reconciliation and add recovery coverage without calling retired `clear`. |
| Manager `select_all`, demo `select_all`/`clear` | No application, test, or tool callers; dynamic GUI actions use `select_many`, while demo reset uses `reset`. Remove unused methods; retain App's visible-scope Select all. |
| `App.acknowledge_removed` | Definition-only across application, tools, and tests. The active `poll` path handles removal acknowledgements and tombstones. Remove the unused duplicate method; keep `poll` semantics intact. |
| `DownloadList.pointer_cell`, `clear_hover`, `COLUMN` | Definition-only across source/tests/tools. Actual pointer routing uses `update_hover_from_pointer`; Leave binds `defer_hover_clear` and its idle completion. Remove these two unused helpers and the unused name-to-index constant; retain active bindings and stable indices. |
| Demo `last_step` and `_run` local `now` | The attribute has five assignments and no reads; its local monotonic timestamp only feeds those writes. Remove the assignments and then-unused local. Retain the timer loop, 0.5-second wait, frozen branch, wall-clock elapsed accounting, and other time calls. |
| `tests/fake_node.py`, direct-stream mock server, demo tray/manager | Active isolated verification and demonstration helpers. Keep. |
| `AGENTS.md`, design/history references | Current workflow and recovery contracts. Keep; update owner links and header names where affected. |

No other obsolete source was established by this audit. Remove unused imports
only when extraction makes their lack of use explicit. Do not delete comments,
tests, helpers, or protocol branches merely because no product callback names
them directly. Before recursive cache/scratch deletion on Windows, resolve and
verify each target remains inside the project workspace.

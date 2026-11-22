# Developer feature map

Read this index before every feature or fix. Follow the relevant links before
editing; read additional sections only when the task crosses their boundaries.
Use this ownership map to avoid repeating repository-wide discovery. Saved
documentation uses normal English; conversation follows AGENTS.md.

## Workflow and shared constraints

Design uses gpt-6.1-sol with high reasoning; implementation uses gpt-6-luna with
high reasoning. Verify availability and obtain approval before substituting an
unavailable requested model. See [agent workflow](development.md#agent-workflow).
Pass section links and concrete acceptance conditions to the implementation
worker, not entire reference documents.

Keep dependencies limited to the standard library. All Tk work stays on the GUI
thread; network operations and SQLite writes stay off it. Freenet downloads use
the local node. Preserve UUID request identity, existing files, durable queue
state, and other applications' node requests. See
[thread boundaries](development.md#gui-and-thread-boundaries).

## Find the responsible feature

| Task | Owner and required references |
| --- | --- |
| Scan, parsing, keys, filenames | `fld/key_parser.py`, `fld/freenet.py`, scan coordination in `fld/app.py`; [parsing and limits](development.md#parsing-filenames-and-limits) |
| Persistent queue, IDs, migration | `fld/queue_manager.py`, `fld/queue_store.py`; [queue storage](development.md#queue-and-persistence), [ID/history contract](change-design.md#persisted-task-identity-and-start-history) |
| Start, pause, cancel, retries, recovery | `fld/queue_manager.py`, `fld/node_protocol.py`; [local protocol/recovery](development.md#local-node-protocol-and-recovery), [task/thread ownership](development.md#gui-and-thread-boundaries) |
| Remove unstarted, late events | `fld/queue_manager.py`, `fld/app.py`; [removal contract](change-design.md#remove-unstarted-and-stale-events) |
| Remove completed, standalone Windows executable, release smoke | `fld/queue_manager.py`, `fld/app.py`, `fld/paths.py`, `fld/smoke_test.py`, `FLD.spec`, `build_windows.ps1`; [release contract and acceptance](release-design.md) |
| Regex filter, sorting, action scope | `fld/app.py`, `fld/gui_widgets.py`; [saved sort and unified selection contract](list-fixes-design.md#durable-named-sort-preferences) |
| List scrolling, headers, columns, performance | `fld/gui_widgets.py`; [viewport anchoring, 11-column map, and v2 widths](list-fixes-design.md#viewport-behavior) |
| Hover, keyboard, row controls | `fld/gui_widgets.py`; [hover/targeting contract](change-design.md#hover-and-control-targeting) |
| Row highlights, Ctrl+A, confirmed deletion, rabbit icon | `fld/gui_widgets.py`, `fld/app.py`, `fld/queue_manager.py`, `tools/generate_icon.py`; [current unified selection contract](list-fixes-design.md#unified-highlighted-row-selection), [historical selection/icon design](selection-design.md) |
| Window geometry, grip, minimize, tray, exit | `fld/app.py`, `fld/windows_tray.py`; [window/tray context](development.md#window-and-tray), [geometry contract](change-design.md#window-size-maximize-state-and-resize-grip) |
| Progress, elapsed, ETA, graph | `fld/queue_manager.py`, `fld/metrics.py`, `fld/gui_widgets.py`; [metric definitions](development.md#metrics) |
| Demo, README GIF | `tools/demo_app.py`, `assets/demo.gif`; [toy-demo contract](change-design.md#deterministic-toy-demonstration) |

The current list-fixes work is governed by
[the list stability, unified selection, and saved-sort contract](list-fixes-design.md).
It supersedes the older checkbox, four-control, session-only sort, and width
metadata rules in the historical references. It records the reproduced scroll
cause, exact compatibility rules, implementation handoff, and acceptance checks.

For a complete file inventory, consult
[files and ownership](development.md#files-and-ownership). The detailed change
contract describes the implemented queue-usability behavior. The prior
104-test suite and 95-test selection/icon gate are historical v0.1.0 results;
the v0.1.1 final Windows suite passed 108 tests with both native opt-ins and no
skips. Selection
and deletion include focused GUI, durable-state, and local-node coverage. The
rabbit icon passed size, determinism, visual, and native ICO checks. See the
[selection verification](selection-design.md#verification-results) and measured
evidence and reproduction commands in the development reference.
The v0.1.1 actual-window README demo has 24 decoded 1280x720 frames, 21,880ms
duration, and verified selected and completed views. The mapped 150-row probe
held its 959px viewport through ten passive reorders. Benchmark timings,
minimum-layout proof, and isolated executable smoke are recorded in
[list-fixes verification](list-fixes-design.md#implementation-verification).

The release's concrete design, acceptance conditions, and measured results are
in [the standalone release contract](release-design.md). It adds table-local
Ctrl+A, queue-wide completed-history cleanup, and a portable executable with
bundled Python/Tk. Source gates, minimum layout, refreshed demo, and isolated
actual-executable smoke passed;
the contract keeps acceptance requirements separate from measured results.

## Verification and maintenance

The completed source refactor's preservation boundary, test-gap audit, file
migration map, cleanup retention decisions, and gate results are in
[the refactor design](refactor-design.md). Its added coverage passed before
structural changes, and the header-label feature was verified separately.

Run `python -m unittest discover -s tests -v` after changes to download or parsing
logic, and for the queue-usability feature. Tests use local mock servers and
temporary storage. Never migrate or reset the user's real database for a test.
List-rendering changes also require `python tools/benchmark_ui.py` in a mapped
window with 150 synthetic tasks. The list-fixes contract requires three shared
native row controls after removing the Select checkbox. Preserve
at most 15 canvas slots at a 360px viewport; larger windows scale with visible
height. Existing scroll behavior is roughly 15ms, subject to actual measurements.

See [verification commands/evidence](development.md#verification-commands-and-evidence)
and [feature acceptance checks](change-design.md#verification-and-deliverables)
for exact coverage and manual Windows checks. After a behavior change, update
this map and only the affected deep sections. Keep the root README focused on
startup, usage, and the demo; keep engineering detail in this directory.

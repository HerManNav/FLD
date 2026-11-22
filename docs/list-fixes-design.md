# List stability, unified selection, and saved sorting

This contract covers the reported intermittent list scrolling, removal of the
redundant Select column, and restoration of the last column sort after restart.
It supersedes older checkbox, session-only sort, and four-control requirements
in the linked historical designs. Download identity, local-node ownership,
completed files, and the existing virtual canvas architecture remain unchanged.

## Diagnosis and evidence

`DownloadList.move_to_y` clamps a pixel offset against the maximum travel, then
passes `y / maximum_travel` to Tk's `yview_moveto`. Tk interprets the fraction
against the full scrollregion height. Every attempted preservation therefore
overshoots whenever the viewport has positive height. Passive metric sorting,
desired-state changes, membership changes, and divider resizing use this helper.
The symptom appears intermittent because unchanged projections bypass it and
actual reorders depend on the chosen sort and incoming state.

A real mapped `DownloadList` with 150 synthetic rows, a 375px body, and a 4800px
scrollregion reproduced the mechanism on 2026-11-22. Repeated preservation of
the existing offset moved `959 -> 1055 -> 1151 -> 1247 -> 1343 -> 1471` pixels.
Replacing only the division with the full region height held 959px for five
successive passes. A separate production-path probe swapped the progress of
only the last two rows and called `refresh_metric_order` five times. Although
the reorder happened below the viewport, it produced exactly the same drift;
the first visible UUID changed from 29 to 32, 35, 38, and 41. These probes used
plain synthetic dictionaries, no database, and no node connection.

There is no evidence that passive keyboard-focus enforcement caused this
report. Do not remove the intentional arrow-key scrolling or replace the
existing UUID anchoring policy with a fixed scrollbar fraction.

## Owners and implementation sequence

One implementation worker owns the coordinated changes in `fld/gui_widgets.py`,
`fld/app.py`, `fld/queue_manager.py`, `tests/test_app.py`, and
`tests/test_queue_manager.py`. It should finish these together, then update
`tools/demo_app.py`, `fld/smoke_test.py`, release version metadata, and affected
documentation. Existing `QueueStore.save(metadata=...)` is sufficient; do not
change the SQLite schema or introduce a new dependency.

Required reference sections are
[thread boundaries](development.md#gui-and-thread-boundaries),
[rendering invariants](development.md#rendering-and-interaction-invariants),
[queue storage](development.md#queue-and-persistence),
[view semantics](change-design.md#view-model-sorting-and-filter-semantics),
[viewport anchoring](change-design.md#fixed-header-and-bounded-rendering),
[column compatibility](change-design.md#scope-and-boundaries),
[selection gestures](selection-design.md#pointer-and-keyboard-contract), and
[executable smoke](release-design.md#actual-executable-smoke-proof).

The parent performs final review, mapped benchmark/layout checks, actual-window
demo export, isolated executable smoke, binary delivery, and the already
authorized commit/push/release. These final steps must use the implemented
source and must not modify the user's live queue. Avoid concurrent edits to the
same source/test files. A separate packaging or demo worker is optional only
after the source interface is stable.

## Viewport behavior

Keep `move_to_y`'s pixel clamp, but divide the clamped coordinate by the full
region height, not the maximum travel. Use the same region height used to set
the canvas scrollregion. Tk row increments and the canvas border can introduce
a pixel of coordinate offset; repeated preservation must not accumulate it.

Preserve the existing first visible UUID and its intra-row offset on automatic
metric/state/membership reorders. If that UUID disappears, clamp the old pixel
offset. Preserve vertical position during width changes, including auto-fit.
An unchanged automatic order must not write yview, increment the projection
generation, hide controls, or invalidate a held press. Explicit user changes to
the sort, Running first, and filter still start at the top. Arrow navigation
continues to reveal its target, with normal row-increment rounding.

## Unified highlighted-row selection

Remove the Select header, painted checkbox graphics, native Checkbutton,
checkbox variable, checkbox callbacks, and checkbox-only cached slot data.
The remaining exact column order and indices are:

`ID` (0), `File` (1), `Done / Size (MB)` (2), `MB/s` (3), `Status` (4),
`Start` (5), `Pause` (6), `Cancel` (7), `Progress` (8),
`Elapsed (min)` (9), `ETA (min)` (10).

Keep exactly three shared native row controls: Start, Pause, and Cancel. Update
every hard-coded column use, including sort keys, metric refresh detection,
unknown-size detection, natural widths, text/progress placement, fallback hit
actions, overlay placement, and test coordinates. Prefer centralized keys or
named indices so the same mapping governs painting and interactions. Keep the
existing bounded row pool, one fixed header, and single body canvas.

Use `DownloadList.selected_ids` as the sole active selection for both Download
selected and Delete selected. Keep all existing click/Ctrl/Shift/arrow/range,
right-click, and Ctrl+A behaviors keyed by UUID. Single-row Start/Pause/Cancel
still act only on their own UUID without replacing highlights. A canvas Space
press toggles the focused visible UUID's highlight once, makes that UUID the
anchor, and keeps focus/details/viewport stable. With no visible focus it is a
no-op. Native Start/Pause/Cancel retain normal Space activation.

Select all unions the current projection into the highlight set, matching
Ctrl+A. Select none subtracts only the visible projection. Both preserve hidden
highlights, focus, anchor, details, and viewport. Hidden highlights survive a
filter and return when it clears. Empty visible actions remain harmless.

Download selected takes a fresh ordered tuple of selected visible UUIDs that
still exist in `App.rows` and are not done, then queues that explicit UUID list
through the existing action worker. Preserve the prior non-done eligibility
rule; do not change the manager's Start semantics in this feature. No hidden or
unhighlighted row may start. Disable the button when that scope is empty or the
application is exiting. A single selection-change callback should refresh both
download and deletion button states; guard early construction where appropriate.
Deletion keeps the existing frozen UUID snapshot, default-No confirmation,
durable acknowledgement, tombstones, and completed-file protection.

New scans and restarts begin without highlights. Do not auto-highlight new rows
or reconstruct highlights from obsolete saved checkboxes. Selection is session
state; the user can use Select all or Ctrl+A before starting a whole queue.

Remove App's pending-checkbox merge state and acknowledgement cleanup, its
special `table_action('select', ...)` branch, and the unused manager/demo
`select`/`select_many` writers. Retain each existing task payload's `selected`
field exactly as loaded. Keep the new-task `selected=True` compatibility field
for older readers, but never consult or change it for current GUI behavior.
Tasks with no `selected` field must also render and act correctly. Do not bulk
rewrite task JSON, renumber display IDs, alter UUIDs, or change schema version.

## Width migration without ambiguity

Use a new metadata name, `column_widths_v2`, for the 11 current columns. The old
metadata name `column_widths` already uses length 11 for the pre-ID layout and
length 12 for the ID-plus-Select layout. Reusing length 11 under that old name
would silently shift widths on the next restart.

Load a valid v2 11-integer array first. Otherwise, read old `column_widths` using
the prior validation, prepend the 50px ID width for an old 11-array, and remove
the resulting Select entry at index 1. Thus old 12 becomes
`[old[0], *old[2:]]`; pre-ID old 11 becomes `[50, *old[1:]]`. Clamp widths to
40..2000 and reject booleans/non-integers, malformed JSON, and wrong lengths.
Invalid v2 may fall back to valid legacy metadata. With neither valid, use
natural widths. A valid v2 must win over conflicting old metadata.

`manager.column_widths` exposes only the converted 11-array. `set_column_widths`
validates 11 and writes only `column_widths_v2` through the action worker. Leave
the old metadata untouched for backward readers; no eager write is required at
startup. Test named-width preservation with distinct widths, not repeated 60s.

## Durable named sort preferences

Add metadata `sort_preferences` containing a JSON object:

```json
{"column": "id", "descending": false, "running_first": false}
```

Stable sortable column names are `id`, `file`, `size`, `speed`, `status`,
`progress`, `elapsed`, and `eta`. Persist names, never numeric indices or
formatted header labels. Missing, invalid JSON, non-object, unknown/non-sortable
column, absent values, or non-boolean direction/grouping restore the complete
default above. Extra fields may be ignored. Read validation belongs to
DownloadManager; no Tk import may be added to queue storage or management.

Expose manager `sort_preferences` and `set_sort_preferences` following the
existing preference save pattern. The setter saves metadata before publishing
the new in-memory value, under the manager lock. All production writes use
`App.manager_action`, so SQLite work stays off Tk. Do not checkpoint task payloads
for this metadata-only operation. Preference write errors follow the existing
action-worker error path.

Add a harmless default sort-change callback to DownloadList. Emit a complete
named preference object after explicit `sort_by` and actual
`set_running_first` changes. The Running first action retains its current rule:
enabling it selects Progress descending; later headings sort within its groups;
disabling it keeps the current column/direction. Save the resulting state,
including grouping. Passive progress/state refreshes, filters, and width changes
must not save sort preferences.

Provide a restore path that sets column, direction, and Running first directly
without firing persistence or the enable-grouping Progress reset. Restore it
before the first task projection, and initialize App's Running first BooleanVar
from that state. A restored File ascending with grouping enabled must remain
File ascending. Filter text and highlights remain transient. The serialized
action worker already places queued preference writes before normal exit.
Update DemoManager to supply and accept the same preference interface in memory.

## Verification and delivery

Add focused regression coverage to the existing suites rather than duplicating
the whole UI. Required acceptance conditions are:

1. With a mapped 150-row table scrolled into the middle, multiple passive
   progress reorders below the viewport preserve its first visible UUID and
   offset. An unchanged tick preserves yview and a held native press. Membership
   and desired-state updates preserve anchors; removal clamps safely. Divider
   resizing and auto-fit preserve position. Explicit sort/filter/group reset to
   the top, and arrow navigation still reveals offscreen targets.
2. No Select header, painted/native checkbox, or checkbox action remains. Three
   native controls work after scrolling, sorting, filtering, and row recycling.
   A 360px body uses at most 15 slots; larger bodies scale by visible height.
3. Click, Ctrl/Shift ranges, arrows, Space, Ctrl+A, Select all, and Select none
   change highlights only. Filtered bulk downloads start exactly highlighted
   visible non-done UUIDs. Empty/unselected/completed-only scopes disable
   Download selected. Hidden highlights survive. New scans and reopen select
   nothing. Delayed snapshots cannot overwrite highlights. Existing deletion
   confirmation/acknowledgement and foreign-request tests remain meaningful.
4. Tests reopen temporary databases for all eight saved sort names, directions,
   and grouping states. Invalid values safely restore defaults. App's heading
   indicator, actual projection, grouping variable, and manager preference agree
   after restart. Explicit sort/group writes run on the action worker; passive
   ticks and startup do not enqueue saves.
5. Distinct old12, pre-ID old11, v2 current11, conflicting, missing, malformed,
   and invalid-width cases retain their named widths or defaults correctly.
   Legacy `selected` true/false/missing values do not affect highlights or bulk
   download scope and survive metadata saves/reopen unchanged.
6. Run `python -m unittest discover -s tests -v`; use `FLD_TEST_TRAY=1` and
   `FLD_TEST_WINDOW=1` for final Windows proof. Run
   `python tools/benchmark_ui.py` in its mapped window with 150 synthetic tasks.
   Record timings and the three-control/slot bound. Tests use temporary storage
   and local fake servers only, never the live queue or node.
7. Update the demo's manager contract and 4-second selection scene to highlight
   Orchid and Harbor using row gestures rather than checkbox invokes. Regenerate
   `assets/demo.gif` through the existing actual-window export workflow, decode
   all frames, and inspect the selected and final views. Update current behavior
   sections in development/change/selection/release references and the feature
   map. Label historical measured results as historical rather than rewriting
   their counts. Keep root README concise: unified highlights, saved column sort,
   and the v0.1.1 download link.
8. Bump executable version resources to 0.1.1 and use the existing pinned
   `build_windows.ps1`/`FLD.spec` packaging. Extend actual executable smoke to
   assert 11 headings, no Select, and exactly three shared controls; retain
   Ctrl+A, durable cleanup, icon, sentinel preservation, and shutdown checks.
   Build `dist/FLD.exe`, perform isolated smoke from an unrelated directory with
   the existing sanitized PATH procedure, then replace the delivered root
   `FLD.exe` with those verified bytes. Publish release v0.1.1 and its SHA-256
   through the parent's already authorized release workflow. Never commit the
   binary or overwrite/migrate the user's live database as part of testing.

The feature is complete when these behavior checks, refreshed documentation and
demo, and the verified delivered executable all correspond to the same source.
No new download behavior, queue schema, toolkit, dependency, or network path is
part of this work.

## Implementation verification

Source implementation, docs, demo, and 0.1.1 executable now use this contract.
The final Windows suite passed 108 tests with `FLD_TEST_WINDOW=1` and
`FLD_TEST_TRAY=1`, with no skips. Focused queue-manager metadata tests passed 44
tests; mapped viewport and saved-sort regressions passed five GUI tests.

The independent mapped scroll probe repeated ten passive progress reorders at
959px and retained the same first visible UUID and pixel offset. The 150-task
benchmark measured one-row scroll at 10.7ms mean, 16.6ms p95, 31.1ms maximum;
three-row scroll measured 11.7ms mean, 14.5ms p95, 19.4ms maximum. The 800px
minimum layout fit all buttons.

Actual-window demo export produced 24 decoded frames at 1280x720, loop 0,
21,880ms duration, and 3,037,053 bytes. Visual checks confirmed the selected
Orchid and Harbor rows, enabled Download selected button, and final completed
rows with highlights.

Isolated smoke passed against the actual 0.1.1 executable: 15,225,753 bytes,
SHA-256 `df6b0cdbcf22b2e290bad1349346a8d3584739a8ed22b70932e3efdaa9121705`.
The report passed all checks, including 11 headings, no Select heading, three
shared controls, native icon load, worker shutdown, and completed-file sentinel
preservation. It ran from an unrelated directory with sanitized PATH.

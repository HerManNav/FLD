# Row selection, safe selected deletion, and rabbit icon

This is the implementation contract for the requested selection, deletion, and
icon changes. Preserve the existing queue-usability contracts except where this
document explicitly extends them. Production remains standard-library-only.
Implementation and focused review are complete. The 95-test native gate and
mapped benchmark passed; measured results appear at the end of this document.
The [standalone release contract](release-design.md) extends this baseline with
Ctrl+A and Remove completed. Its acceptance checks require separate results.
Their completed source checks and refreshed demo are recorded in the
[release verification](release-design.md#verification-results); the historical
95-test results below remain the previous selection/icon baseline.

## Owners and boundaries

- `fld/gui_widgets.py`: transient row selection, pointer and keyboard handling,
  one context menu, selected backgrounds, and existing virtual rendering.
- `fld/app.py`: Delete selected button, confirmation, action-worker dispatch,
  acknowledgement handling, details, and status.
- `fld/queue_manager.py`: explicit UUID deletion, durable removal intent,
  owned-node cleanup, and late-event protection. Keep QueueStore's schema and
  complete transactional checkpoint interface.
- `tools/generate_icon.py` and `assets/fld.svg`, `fld.png`, `fld.ico`: one shared
  vector geometry, generated entirely with the standard library.
- Focused tests belong in existing GUI/manager tests and use temporary storage,
  in-memory tasks, and the local fake node. Update the toy manager only as needed
  for the production interface. Preserve the demo narrative; refresh its GIF
  when needed to show the current icon and action layout.

Read the feature-map links for rendering, hover, filtering, removal, persistence,
and local-node recovery before implementation. Do not change parser behavior,
column order, tray lifecycle, file naming, or existing download checkbox scope.

## Two independent selections

The existing `task['selected']` boolean is the persisted **download checkbox**.
Select all, Select none, Download selected, Space, and the row checkbox retain
their current meanings. Clicking or extending a row highlight must never write
that boolean or call `manager.select`/`select_many`.

Add transient DownloadList state: `selected_ids` (UUID set), `anchor_id`, and
`focus_id`. These are separate from hover, the shared native-control target,
and App's single `selected_row` detail identity. Keep a selection-change callback
with a harmless default so existing callers remain compatible. Provide a method
returning selected visible UUIDs in current projection order; App must use it
instead of canvas slot order or display IDs.

Selection survives sorting, progress-driven reordering, scrolling, and filtering
by UUID. Filtering preserves hidden selected UUIDs, but deletion acts only on
the selected visible UUIDs. Clearing the filter restores their highlights.
Prune UUIDs only when absent from the full acknowledged task collection. Never
persist row highlights or copy them into a newly added task with the same key.

An anchor that survives but becomes hidden remains stored. For a new Shift
gesture, if the anchor is not in the current projection, use the visible focus
UUID; if that is unavailable, use the clicked/target UUID. Set this replacement
as the anchor. If an anchor is removed, clear it; the next gesture applies the
same fallback. A sort retains anchor identity and interprets a later range in
the new current order. Range endpoints are inclusive.

## Pointer and keyboard contract

Apply row gestures to non-control cells, including ID, filename, metrics, status,
and unused horizontal space on a valid row. Keep checkbox and Start/Pause/Cancel
cells independent: they act on their one UUID without replacing row highlights.
Both painted fallback cells and native overlays must follow this rule.

| Gesture | Row highlight and anchor behavior |
| --- | --- |
| Plain left click | Replace highlights with clicked UUID; set focus and anchor to it. |
| Ctrl+left click | Toggle clicked UUID; set focus and anchor to it, even when toggled off. |
| Shift+left click | Replace highlights with anchor-to-click range in current visible order; keep anchor. |
| Ctrl+Shift+left click | Union existing highlights with that range; keep anchor. |
| Plain Up/Down | Move focus one visible row; replace highlights with target and set anchor to it. |
| Ctrl+Up/Down | Move focus only; retain highlights and anchor. |
| Shift+Up/Down | Move focus and replace highlights with anchor-to-target range. |
| Ctrl+Shift+Up/Down | Move focus and add anchor-to-target range. |
| Ctrl+A | Add all currently visible UUIDs to highlights; preserve hidden selections, anchor, focus, details, viewport, and download checkboxes. |
| Space | Toggle focused row's existing download checkbox; leave highlights unchanged. |
| Delete | Invoke the same confirmed selected-deletion path as the button. |

Arrow bindings work on the canvas and all four shared controls, scroll the target
into view, show its controls, and update details. With no visible focused UUID,
Down begins at the first visible row and Up at the last. Plain movement at an
edge retains that row. Hover must never change `focus_id`, anchor, or highlights.
Do not add root-level Delete bindings that intercept editing in source/filter
entries. Canvas and native-checkbox Space use the focused UUID even when hover
moved the shared overlay to another row; toggle once and suppress the checkbox
class binding. Native Start/Pause/Cancel buttons retain normal Space activation.
Ctrl+A follows the same canvas/four-control binding scope. It has no root/global
binding and preserves source/filter entry text selection. Empty visible scope
is a no-op. See the [extension contract](release-design.md#ctrla-row-highlights).

Commit a row click only for a matching press/release UUID and projection
generation. Sort, filter, removal, or scroll between press and release cancels
the gesture, as it already cancels painted control activation. Capture modifiers
at press. A plain click on empty body clears row highlights, focus, and anchor;
modified empty clicks are harmless. Empty space never selects a nearby row.
Empty clicks need not clear the existing detail panel. Headers and scrollbars
do not select rows.

Use one shared Tk Menu with one command, **Delete selected**. Right-click on an
already highlighted visible row preserves the selected group. Right-click on
an unselected row replaces highlights with that UUID and sets anchor to it.
Both cases focus/show details for the clicked UUID and open the menu there.
Right-click on empty space does not select or show a row menu. Handle native
overlays using root-to-canvas coordinates, not the overlay's local coordinates.
Dismissal/Escape performs no deletion; any selection change made by right-click
remains. Release menu grab in a finally block. Delete from the menu runs the
same fresh snapshot-and-confirmation path as the bottom button.

## Highlight rendering and limits

Background precedence is selected+hover `#b8d8f5`, selected `#c8e1f7`, hover
`#dceeff`, then existing alternating `white`/`#f7f8f9`. Keep dark text and current
progress/button/checkmark readability. Native controls retain Windows styling.
Use one background-color helper in hover updates and slot placement/rendering,
so moving the pointer away cannot erase a selected background. Repaint only
affected visible backgrounds after selection changes; do not trigger sorting,
full filename measurement, or row widget creation.

Keep exactly four shared native row controls. One shared context menu is allowed;
there is no per-row menu or widget. At a 360px viewport keep at most 15 canvas
slots. Existing recycled-slot identity and native press validation still apply.

## Button, confirmation, and acknowledgement

Use two fixed action rows so the production DPI-aware window fits its 800px
minimum without clipped labels. The first row contains Download selected,
Pause all downloads, and Cancel all, in that order at the left. The second row
contains Remove unstarted, Remove completed, and Delete selected at the left,
with Open download folder aligned at the right. Keep the existing small gaps
between adjacent buttons and about 4px between the two rows. Two shared layout
frames are sufficient; no responsive breakpoint or resize handler is needed.
The added completed cleanup button requires a fresh production minimum-width
measurement; the historical results below covered the previous two-button row.
Enable Delete selected only when at least one
selected visible UUID exists and the app is neither exiting nor already showing
or processing a selected-deletion confirmation. Completed rows are eligible.
The menu and Delete key use identical guards. No selection is a harmless no-op.

Before opening the modal, capture an immutable tuple of selected visible UUIDs
still present in App.rows. Deduplicate it. Set the confirmation guard before
entering Tk's nested modal loop. Call `messagebox.askyesno` with parent=root,
title **Delete selected tasks?**, warning icon, and default **No**. Exact body:

```text
Remove N selected task(s) from the list?

Active or paused downloads will be cancelled. Completed files will be kept on disk.
```

Use the snapshot count for N. No/Escape/window close enqueues nothing. On Yes,
enqueue only the captured UUID tuple; never recompute selection after the
dialog. If exit began during the modal loop, enqueue nothing. Completion during
the dialog does not exempt a confirmed UUID; any final output already created
must survive. Removal/re-addition during the dialog cannot target the new UUID.
Keep one deletion pending until its acknowledgement/error and reset the guard
on every cancellation/error path.

Selected deletion does not cancel scans or increment the scan generation. New
scans and scan_add may continue; they cannot broaden the frozen UUID scope.
Keep existing Remove unstarted semantics, including scan cancellation, intact.

Add explicit `DownloadManager.delete_tasks(identifiers) -> list[str]` and an
action-worker `delete_tasks` branch emitting `delete_done` with accepted UUIDs
after the durable manager call succeeds. Process `delete_done`, like
`remove_done`, before scan-generation filtering. Record all acknowledged UUIDs
in App.removed_ids before applying any coalesced snapshot, including a stale
snapshot from earlier in that poll. Clear only those pending checkbox intentions,
row highlights, anchors/focus, and detail identities. Re-added keys have fresh
UUIDs and remain visible. Acknowledged zero-result deletion is valid.

After updating rows report **Removed N selected task(s). Completed files were
kept on disk.** N is the actual accepted UUID count, not the earlier dialog
count. Preserve this result at least through the acknowledgement poll; later
normal status updates may replace it. A dedicated deletion error must clear
the pending guard and use existing `Error: <message>` reporting, even after a
new scan generation. Never optimistically hide rows before durable acceptance.

## Durable deletion and local-node safety

Under the manager lock, deduplicate input UUIDs in order and accept only existing
non-removed tasks. All desired states are eligible, including new, running,
paused, cancelled, failed, and done. Mark the accepted tasks `desired='removed'`
through the existing state/timer machinery, zero speed, clear pending control
send markers, force a transactional checkpoint, notify a hidden snapshot, and
wake reconciliation. Return exactly the accepted UUIDs. Do not add a blanket
clear API or change `remove_unstarted` into selected deletion.

Selected deletion is atomic with its durable checkpoint. If the SQLite save
raises before commit, restore the accepted task payloads (including timers and
ETA fields), their removed `sent`/ETA-sample entries, and the previous dirty/save
bookkeeping under the same manager lock, then re-raise. Do not notify a hidden
snapshot, enqueue node cleanup, wake reconciliation, or emit `delete_done` for
that failed operation. The ordinary App error path keeps the rows and releases
its pending guard. Limit this rollback contract to `delete_tasks`; do not refactor
other manager controls into a new transaction framework. Once the database
commit succeeds, deletion is accepted and a later notification failure must not
roll back only memory. Keep save/acceptance distinguishable from notification.
If there are no accepted UUIDs, return an empty list without an unnecessary save.

Retain removed task payloads as durable hidden tombstones until local-node
cleanup is proved. Offline deletion must survive process exit and reopening.
`snapshot()` already hides desired removed records; preserve that rule.
Re-adding a removed key remains permitted and receives a new UUID/display ID.
Deletion never calls unlink on task['path'] or any completed output filename.
Only the existing UUID-specific `.fld-parts/<uuid>.ready` staging path may be
removed under the existing cancellation/recovery rules.

Use existing reconcile and RemovePersistentRequest, on the existing network
worker. Act only on UUIDs retained in this manager's persisted tasks, never on
unrelated global node requests. Reconnect must finish ListPersistentRequests
before deciding an absent request is gone. If the UUID remains on the node,
request its removal and wait for removal acknowledgement/confirmed absence.
Keep a durable tombstone while disconnected or while cleanup is uncertain.
Never perform socket writes while holding the manager lock. Queue protocol
messages in the existing outbox and flush them after releasing the lock.

Late progress, DataFound/completion, PersistentGet, retry results, ProtocolError,
and callbacks must never change a removed task back to running/failed/done or
finalize its staging file into a final output. In particular, current generic
ProtocolError handling needs a removed-state guard; code 15 can acknowledge an
absent request, while other codes leave removal pending for reconnect/retry.
Retain existing cancelled-task behavior. Serialize completion and deletion with
the same manager lock: completion first preserves its final file, deletion first
prevents finalization. Ignore later Start/Pause/Cancel/checkbox actions for
missing/removed UUIDs.

An already running asynchronous retry may finish after deletion. Do not reclaim
its tombstone while the UUID is in `retrying`; a retry result must not revive it.
When such a result arrives, clear any old removal send marker and verify/remove
the UUID again before reclamation. A conservative retained tombstone is preferable
to forgetting an uncertain owned request. No new protocol, retry service, or
network dependency is needed.

## Rabbit icon geometry

Keep the recognizable rounded blue tile, white downward arrow, and green tray.
Add an original white leaping-rabbit silhouette at upper right, inspired by the
Freenet rabbit motif without copying or downloading a third-party asset. No
runtime remote asset is used. This is vector/code-native work, not an
image-generation task.

Use the existing 64-by-64 coordinate system, tile radius 7, background `#14467d`,
white foreground `#ffffff`, and tray `#4be191`. Retain existing tray points:

```text
13,43 19,43 19,49 45,49 45,43 51,43 51,55 13,55
```

Move the arrow left and narrow it to make a separate rabbit area:

```text
17,12 27,12 27,29 34,29 22,42 10,29 17,29
```

Add a connected rabbit polygon, with two long upward ears, right-facing head,
rounded-body approximation, forward foreleg, and bent rear leg:

```text
36,23 40,20 46,20 49,17 48,7 51,6 54,16 56,6 59,7
58,17 61,19 61,22 56,24 53,25 60,29 59,32 51,28
48,25 46,30 51,35 49,38 41,32 38,27
```

Add its small tail as polygon `35,22 38,22 40,25 38,28 35,27 34,24`.
Render tile, arrow, rabbit, tail, then tray. Keep geometry constants shared by
SVG and pixel rasterization. Existing 4-by-4 supersampling is sufficient.
Generate PNG at 64px and ICO frames at 16, 24, 32, 48, and 64px with existing
stdlib encoders. Preserve the normal asset filenames and icon path consumers.
Visually inspect the actual 16/24/32/64 raster sizes on light/dark surroundings.
The arrow, tray, and two rabbit ears must remain separately recognizable.
If the proposed silhouette needs geometry changes to meet that condition,
return that design decision to the Sol design phase before implementation edits.

## Acceptance and verification

1. All existing 81 tests pass. Add focused GUI tests for plain/Ctrl/Shift/
   Ctrl+Shift selection, reversed ranges, keyboard behavior, empty hits, independent
   checkboxes, and preserved UUID selection across sorting/filtering/removal.
2. Test right-click on selected/unselected/empty rows, shared native overlays,
   cancelled menu, selected/hover background precedence, and stale press/release.
3. Test button/menu/Delete routing, disabled/no-selection behavior, No default,
   cancellation, snapshot scope across a nested-dialog mutation, completion during
   confirmation, and exiting during confirmation. Mock the dialog; no live node.
4. Test mixed-state exact-UUID manager deletion, idempotent missing/removed IDs,
   completed output preservation, late start/progress/completion/ProtocolError,
   and re-adding the same key under a fresh UUID. Inject a transactional SQLite
   save failure and prove task/timer/send/ETA/dirty state is restored, no hidden
   notification or node cleanup escapes, reopening retains the tasks, and a
   later successful deletion works. Do not test against the user's database.
5. Use the fake node plus temporary database/files to prove active/paused/failed
   owned request cleanup, foreign-request preservation, and offline deletion
   followed by restart/reconnect. Prove tombstones survive offline and never
   finalize late completions; include an in-flight retry cleanup case.
6. Test stale deletion acknowledgement across scan generations and snapshots,
   removed selection/details cleanup, new scan independence, zero-result/error
   recovery, and unchanged Remove unstarted behavior.
7. Run `python -m unittest discover -s tests -v` and the mapped
   `python tools/benchmark_ui.py`. Record actual timings, four shared controls,
   and the 360px slot bound. Exercise both native Windows opt-ins where available.
8. Run the icon generator twice and verify deterministic identical outputs, ICO
   frame sizes, PNG dimensions, and SVG geometry consistency. Inspect generated
   icon sizes and the real app/tray appearance without touching user downloads.
9. Update only affected feature-map/development/change-design references and
   concise end-user usage text. Keep design constraints distinct from measured
   verification evidence; never claim a test was run based on this contract.

## Verification results

The completed selection/deletion/icon change passed all 95 tests on 2026-11-22
with `FLD_TEST_TRAY=1` and `FLD_TEST_WINDOW=1`, no skips, in 28.308 seconds. The
earlier 81-test refactor suite is a historical baseline, not the current count.
Review found no remaining actionable implementation findings after the focused
hover, filter enable-state, retry verification, keyboard, and minimum-layout
corrections.

New coverage proves row gesture and keyboard selection, independent download
checkboxes, hover precedence, shared-menu scope, frozen confirmation UUIDs,
default No/cancel/error/exit handling, worker error recovery, stale acknowledgements
and fresh re-added UUIDs, transactional save rollback, owned active/paused/failed
request deletion, foreign-request preservation, offline restart cleanup, and
post-retry request verification. Tests use temporary storage and fake local nodes.

The mapped 150-task benchmark measured one-row scrolling at 10.6ms mean, 13.5ms
p95, and 14.7ms maximum. Three-row scrolling with progress measured 11.7ms mean,
14.1ms p95, and 15.1ms maximum; the preceding mean was 11.6ms. Four shared native
row controls and the visible-height slot bound remain intact.

The production DPI-aware 800x460 check verified full labels on both fixed action
rows. Open download folder measured 161px actual and 161px requested; the rejected
single-row arrangement provided only 120px. Two icon-generator tests passed,
including deterministic asset regeneration. Visual review accepted the arrow,
tray, ears, and rabbit limbs in every 16/24/32/48/64px ICO frame on light and dark
surroundings. Native Windows ICO loading also passed. No runtime dependency was
added and no completed user file or foreign node request was removed.

The actual-window toy demo was recorded again for the rabbit icon and two action
rows, without changing its narrative. All 24 encoded frames decoded at 1280x720;
the GIF lasts 21,880ms, loops indefinitely (`loop=0`), and is 2,855,027 bytes.
The encoded final frame visually shows the rabbit caption icon, Delete selected
and both action rows, minute headings, two completed rows, six Ready rows,
12s/13s completed elapsed values, and a 0MB/s graph. Demo data remain synthetic
and separate from the user's queue and local node.

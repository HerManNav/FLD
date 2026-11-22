# Queue usability change design

Status: product implementation verified against this approved contract. The
original 71-test feature suite passed with both native Windows opt-ins and no
skips. The subsequent source-refactor gate passed all 81 tests. The current
selection/deletion/icon suite passes all 95 tests with both native opt-ins and
no skips; measured scrolling and native geometry evidence appears in
development.md. The refreshed actual-window demo GIF is delivered, decoded,
and visually verified. This contract was written before product changes.
Start with [the feature map](README.md); read only the sections relevant to the task
and linked architecture constraints in `development.md`. Update the index and
affected references when implementation changes the contract.

## Scope and boundaries

Add window size recovery, a resize grip, safe removal of unstarted tasks, fixed
headers, persistent numeric IDs, sorting, filename regex filtering, and row
hover highlighting. Preserve the standard-library-only application, local-node
downloads, asynchronous queue actions, and virtual canvas rendering. Do not
replace the list with Treeview, a widget-per-row table, or another toolkit.

The visible column order is exactly:

`ID`, `Select`, `File`, `Done / Size (MB)`, `MB/s`, `Status`, `Start`, `Pause`,
`Cancel`, `Progress`, `Elapsed (min)`, `ETA (min)`.

Elapsed and ETA cells keep their `MM:SS` values. The minute suffix describes
the metrics; it does not change the displayed value format.

Use stable column names or centralized indices. New indices are 0 through 11;
checkbox is 1, action buttons are 6/7/8, and progress is 9.

Load old `column_widths` arrays of length 11 by prepending the natural ID width
(at least 50px and wide enough for its heading). Keep every old width mapped to
its original named column, unchanged apart from existing 40..2000px validation.
Accept new arrays of length 12 directly. Ignore malformed/other-length metadata
and use natural widths. Write the new 12-width form on the next width change;
manager.set_column_widths must validate 12. Do not silently discard old widths.

## Persisted task identity and start history

Keep `task['id']` and its `fld-<uuid hex>` value unchanged. It identifies the FCP
request, staging file, actions, selections, detail panel, and canvas row.
Add `display_id`, a positive integer shown in the first column. Never use its
current row position as the display ID.

Assign display IDs in `DownloadManager.add` in incoming key order, after
duplicate-key rejection. Duplicate keys consume no number. Append later scans
after the existing sequence. Sorting, filtering, completion, and removal do not
renumber records. Re-adding a removed key creates a new UUID and display ID.
Store the next available number as JSON/integer metadata `next_display_id`.
Never decrease this counter, including when the queue becomes empty.

Migrate old task payloads during manager initialization, before the network
thread starts. Existing `QueueStore.load` order is `tasks.rowid`; use it as the
best available original scrape order. If all records lack display IDs, assign
1 through N in that order. Otherwise preserve unique valid IDs and assign
missing/invalid/duplicate values in load order above the maximum retained ID.
Choose `next_display_id` as the maximum of its valid saved value and one above
the maximum assigned ID. Persist payload migration and counter together before
accepting actions. An add must commit new tasks and its updated counter in the
same SQLite transaction. Existing UUIDs, saved filenames, desired states, paths,
timers, and progress remain intact.

Add persisted boolean `ever_started`, initially false. In the same lock and
checkpoint transaction that accepts a Start action, set it true before changing
the desired state to running. This includes requests waiting for a connection,
requests with zero progress, and attempts which subsequently fail. Never reset
it during pause, cancel, retry, completion, restart, or reconnection.

For records lacking this field, conservatively preserve a task if its desired
state is anything except `new`/`removed`, or if elapsed time, active_since,
fetched bytes, progress, retries, path, or completion metadata indicates a prior
attempt. Only pristine legacy `new` tasks become false. Treat an inconsistent
existing false flag with positive start evidence as true. Favor preservation
when history cannot be proved. Save the inferred flag during migration.

Keep schema version 1: these are additive JSON payload and metadata fields.
Do not rename tables or rewrite identifiers. Existing version-1 readers ignore
extra payload fields, but an old binary does not enforce the new removal rule;
do not advertise full behavioral rollback compatibility.

## Remove unstarted and stale events

Replace the Clear button with **Remove unstarted**. The operation acts on the
entire queue, including hidden rows, regardless of checkboxes or sorting. Keep
every task with start history, including running, paused, cancelled, failed, and
completed tasks. Completed files and started requests are untouched.

Implement `DownloadManager.remove_unstarted()` as one atomic locked operation:
compute candidates from current authoritative tasks; delete only tasks with
`ever_started == False`; checkpoint; return their UUIDs. A never-started task has
no node request to cancel. Do not route this action through the old blanket
`clear()` or `action(..., 'removed')` operation. Retain `clear()` only if existing
internal tests or tools still need the old explicitly destructive API; no user
control should call it.

At the UI action, cancel the current scrape, increment its generation, and stop
the scan indicator, as the old Clear operation did. This prevents a page scan
already in progress from repopulating the queue. Keep the source URL, regex,
sorting, widths, and surviving selections. Existing submitted downloads continue.
If scan_add was already executing, the single action worker completes that add
before remove_unstarted; all newly added unstarted records are then eligible.

Do not optimistically clear the list or retain `pending_clear`. The action
worker emits a dedicated acknowledgement containing the actual removed UUIDs,
after the manager call succeeds. App maintains session `removed_ids` tombstones
for acknowledged UUIDs. `sync_tasks` discards these IDs from every snapshot,
including a snapshot coalesced earlier in the same poll or delivered late.
UUIDs are never reused, so a tombstone cannot hide a later re-added key. This
avoids a queue-empty acknowledgement condition, which would never complete when
started tasks survive. On error, keep rows and show the existing action error.

After acknowledgement, discard pending selection entries and detail focus only
for removed UUIDs, refresh the projection, and report
`Removed N unstarted file(s). Started files were kept.` A zero-result operation
is valid. Continue applying later snapshots for surviving tasks normally.

Make identifier-taking manager actions tolerate absent/removed UUIDs. A queued
checkbox, pause, cancel, or start event can target a row just removed. Ignore it
without raising KeyError. The action worker defines ordering: Start before
Remove preserves the row; Remove before Start removes it and the later Start
is ignored. The GUI must not decide start history from a stale snapshot.

## Row multi-selection and confirmed deletion

Row highlights are transient UUIDs, separate from the persisted download
checkbox. Sorting, progress reordering, scrolling, and filtering preserve
highlight identity; deletion captures only selected visible UUIDs. Keep the
selection, keyboard, menu, modal, acknowledgement, and owned-node cleanup rules
in the [selection design](selection-design.md).

`DownloadManager.delete_tasks` accepts exact existing non-removed UUIDs,
checkpoints their `removed` intent, and returns accepted UUIDs. Keep tombstones
until local-node cleanup is proved. The action worker reports acknowledgement
after the durable call; App records UUID tombstones before applying coalesced
snapshots. This operation neither cancels scans nor changes Remove unstarted.
Failed durable acceptance restores only this operation's in-memory changes.
Late node messages cannot revive removed UUIDs, and asynchronous retries require
post-retry absence/removal verification before tombstone reclamation.

Delete selected, the shared row menu, and table Delete key use the same frozen
visible UUID snapshot and default-No confirmation. Completed output files remain
on disk. Keep the two fixed action rows: Download selected/Pause all downloads/
Cancel all above Remove unstarted/Delete selected, with Open download folder at
the right of the second row. This preserves full labels at the production
DPI-aware 800x460 minimum. The implemented feature passed the
[95-test native gate and mapped benchmark](selection-design.md#verification-results).
The README demo was refreshed to show the rabbit icon and action rows while
preserving its existing narrative; its 24 encoded frames and final view passed
verification. Exact artifact statistics are in
[the development evidence](development.md#verification-commands-and-evidence).

## View model, sorting, and filter semantics

Keep `App.rows` as the complete acknowledged queue keyed by UUID. Keep
`DownloadList.tasks` as plain task data and `DownloadList.order` as the currently
visible UUID projection. Filter first, then sort. Store a UUID-to-projection-index
map for hit testing, keyboard movement, overlays, and scroll anchoring; avoid
`order.index` scans during every pointer movement or overlay placement.

Default view is ID ascending with **Running first** unchecked. Add that native
checkbutton beside the existing selection controls, keeping graph at the right.
Enabling Running first sets the active sort to Progress descending. Its grouping
key is `desired == 'running'`: connecting, retrying, and reconnecting desired
running tasks belong to this group. Paused, cancelled, failed, done, and new
tasks do not. Later header clicks keep grouping enabled and change the sort
within each group. Unchecking grouping preserves the current column and direction.

Sortable headings: ID, File, Done / Size, MB/s, Status, Progress, Elapsed, ETA.
Select, Start, Pause, and Cancel headings remain nonsortable. Clicking a new text
heading starts ascending. A new metric heading starts descending, except ID
starts ascending and ETA starts ascending. Clicking the active heading toggles
direction. Show an up/down triangle in the active heading and include its width
in natural header measurement.

Sort unformatted values, never display strings:

| Column | Value |
| --- | --- |
| ID | display_id |
| File | filename.casefold() |
| Done / Size | total size in bytes, then fetched bytes |
| MB/s | current speed while desired running, otherwise zero |
| Status | base status.casefold(), excluding appended percentage |
| Progress | fraction, clamped to 0..1 |
| Elapsed | elapsed_seconds(task, shared_now), unknown if timer_known is false |
| ETA | remaining_seconds(task) |

Unknown sizes/times sort last in both directions. Equal values always break ties
by display_id ascending, including descending sorts. Capture a single `now` for
a sort pass. Do not mutate queue insertion order when sorting.

Apply header/group/filter changes immediately. Process desired-state changes
immediately so running membership is accurate. Re-evaluate metric ordering on
the existing one-second progress tick, not on every FCP event. Reuse the existing
coalesced task update path. A checkbox toggle must not trigger filename fitting
or a complete sort if no sort key changed.

Separate membership/filename changes from order-only changes in set_tasks.
Sorting or filtering an unchanged full task collection must not call
resize_columns(measure=True) or font.measure for every filename. Cache natural
widths by full-collection filename membership; reuse them during progress sorts,
filter projection changes, checkbox toggles, and pointer movement. Reordering
only invalidates visible slot positions, not text measurements.

Add a **Filename regex** entry directly after Find links in the top source row.
Give it about 22 characters at the default width and a small inline error label
on the following row, hidden when valid. Source entry takes remaining width;
at 800px minimum width both entries and Find links must remain usable. Use a
shorter source entry before hiding or truncating the filter. Example hint text
or tooltip: `\.jpg$ or \.mp4$`. Do not put a fake pattern into its actual value.

Compile standard-library `re` expressions with IGNORECASE and use `search` on
the full filename, including its extension. Empty pattern shows all rows.
Debounce edits by 150ms; Enter applies immediately. Invalid syntax keeps the
last successfully applied pattern and result, shows `Invalid regex: <message>`
beside/below the filter, and leaves queue status messages independent. Never
show an empty list solely because a pattern is invalid. A valid zero-match filter
shows an empty body with headers and scrollbars still valid. Display
`Showing X of Y files` while a nonempty valid filter is applied.

Cache filter matches by filename and compiled pattern. Queue progress and speed
changes must not repeatedly compile or match filenames. Recompute membership
for new, removed, or renamed filenames. Standard `re` has no execution timeout;
normal filename expressions on 150 rows are the supported performance case.
Do not add dependencies, regex restrictions, subprocess workers, or a regex
execution service for this change.

Filter is a view and action scope, not a scraping restriction: scrape still adds
every discovered unique key. **Select all**, **Select none**, and **Download
selected** act only on visible matching rows. Hidden selections are preserved
but never started by Download selected while hidden. With zero matches these
actions are harmless no-ops with a clear status message. Use explicit UUID lists
for bulk selection instead of manager.select_all when filtering. **Pause all
downloads**, **Cancel all**, and **Remove unstarted** remain global, as their
labels imply. Counters and speed graph remain global; add the visible count
without changing their meaning. Retain detail selection if its task is merely
filtered out; clear it only if the task was removed.

Filter text, active sort, and Running first are session view state. Persist only
the requested task IDs, history, column widths, and window preferences.

## Fixed header and bounded rendering

Create one fixed-height clipped header frame above the existing body canvas.
Keep native header labels in that frame. Keep exactly one scrolling canvas for
row graphics and exactly four shared native row controls. The header frame is
not a second canvas and is never part of the canvas scrollregion.

Table grid: header frame row 0/column 0; body canvas row 1/column 0; vertical
scrollbar row 1/column 1; horizontal scrollbar row 2/column 0. Only row 1 expands.
Place header labels at x = cumulative_column_width - horizontal_scroll_offset,
y = 0. The frame clips horizontal overflow. Vertical movement never changes
header visibility or y. The upper-right corner is a blank frame cell.

Body coordinates now start at y=0. Row i spans i*ROW_HEIGHT through
(i+1)*ROW_HEIGHT. Remove HEADER_HEIGHT offsets consistently from scrollregion,
row placement, hit tests, visible-row calculation, overlay placement, and keyboard
scrolling. HEADER_HEIGHT describes only the fixed frame. Configure the body
scrollregion as `(0, 0, total_width, max(viewport_height, N*ROW_HEIGHT))` to keep
empty and short lists at a stable y=0. Clamp/reset yview after shrinking or
filtering; an empty list resets to the top and hides controls/highlight. First
repopulation must show ID 1's position immediately without a wheel event.

Use one overscan row above and one below the actual viewport. Reuse slots for
overlapping UUIDs, hide unused slot items, and invalidate position/cache only
when required. At a 360px body viewport, pool size must remain at most 15.
Larger windows may allocate enough slots for their actual visible height;
allocation must never scale with all 150 or 148 queue records. Do not impose a
hard 15-row limit that leaves a tall maximized window partly blank.

After a sort/state update, preserve the first visible UUID and its intra-row
pixel offset when it survives. If it disappears, clamp the previous numerical
offset. Explicit filter edits and header/group changes start at the top, so
users can see the new leading results. Width changes preserve vertical position.
Hide overlays before projection changes; recompute their targets afterward.

An automatic metric/state rebuild must first compare its candidate UUID order
with the current order. If unchanged, refresh values and control enablement
without hiding overlays, cancelling presses, incrementing the projection
generation, or changing the viewport. A stationary hovered button and a click
spanning an ordinary one-second tick must remain usable. Explicit header,
filter, or grouping actions may still cancel the press and reset the viewport.
On an actual reorder, cancel canvas and native pressed state, advance the
generation once, hide overlays, update/anchor/render, and then recompute pointer
hover/control targeting. If the pointer has no visible row, preserve the prior
visible keyboard target only while keyboard focus belongs to the table or its
shared controls. Respect the existing scroll quiet period. Never transfer a
held native button press to a different UUID after retargeting.

Header dividers retain their existing 5/6px hit zones, drag throttling at 33ms,
40..2000px bounds, double-click auto-fit, and persistence. A divider press always
means resizing, never sorting. A non-divider press/release on the same heading
sorts only if no drag occurred; divider double-click never sorts. Keep wheel
and Shift+wheel routing over headers, body gaps, and native controls.

## Hover and control targeting

Use a full-row light blue background for the UUID under the pointer, across the
entire row width. Preserve selected background when not hovered, and striped
background for unselected rows. Selected/hover precedence and exact colors are
defined in the [selection design](selection-design.md#highlight-rendering-and-limits).
Canvas text,
progress, and painted buttons remain readable. Native controls keep their normal
Windows appearance; their surrounding row background stays highlighted.

Track pointer hover separately from keyboard control focus and detail selection.
Apply motion/enter/leave handling to the body and all four shared controls using
root pointer coordinates converted into body coordinates. Moving from the canvas
onto a native button must not clear the row. Moving to a header, scrollbar,
another app, or empty body space clears hover. Leaving a row does not clear its
checkbox, details, or keyboard identity. Pointer movement changes only the old
and new row backgrounds, without a full list repaint.

At scroll start, hide native overlays and clear any pressed cell. Clear/recompute
hover for the newly exposed row from current pointer position after rendering.
Keep the existing 150ms scroll quiet period before restoring native controls.
Use one cancellable after callback to restore/re-evaluate controls after scrolling
stops even if no new Motion event occurs. If keyboard navigation explicitly moves
focus, show controls for that visible focused UUID without waiting for pointer
motion. A row filtered out or removed cannot remain a control target.

Record pressed UUID and column; release invokes only if the same UUID and column
are still under the pointer and still actionable. A sort, filter, scroll, or row
removal between press and release cancels the press. Native commands must validate
their current UUID too. No click should transfer to a different recycled row.

## Window size, maximize state, and resize grip

Add `ttk.Sizegrip` at the lower-right footer corner. Keep status, scanning label,
and progress indicator to its left. Show it in normal state and hide/disable it
when maximized. Native frame borders remain resizable. Keep minsize 800x460 and
default normal client size 1280x650.

Persist metadata `window_preferences` as
`{"width": 1280, "height": 650, "maximized": false}`. Save client size, not
position, minimized state, tray-hidden state, or maximized screen dimensions.
Manager exposes loaded preferences and a locked setter running on the existing
action worker. Validate positive non-boolean integer dimensions and boolean
maximized state; malformed/missing values use defaults. Clamp restored normal
size to at least the minimum and at most the current screen's usable bounds,
as available through Tk/Windows; reduced-resolution screens must stay reachable.

Initialize normal geometry before restoring maximized state. Restore zoomed
state with an idle callback after the initial map. Startup restore must not
overwrite saved preferences with transient 1x1/default Configure events.

Listen only to top-level Configure/state changes, ignoring child Configure
events. Track last stable normal width/height only while visible and state is
normal. A maximize transition sets maximized=true while retaining the previous
normal size. A restore transition sets false and captures settled normal geometry.
Debounce Configure capture by 250ms; re-read settled root state and dimensions
at callback time. Use Tk's stored normal geometry while zoomed if needed to
resolve an ambiguous transition; never substitute current maximized dimensions.

Maintain last non-minimized state separately. Minimize, withdraw, tray hide, and
restore transitions do not turn maximized=false or overwrite normal dimensions.
Before hide_to_tray, capture/flush pending normal geometry and last non-minimized
state, then enqueue its preference write before withdrawal. Restore re-applies
the tracked maximize state after deiconify. Tray failure minimizes without
changing saved maximize preference.

Freeze the intended maximize state throughout programmatic startup/tray restore.
Cancel pending geometry capture and set a restore guard before deiconify; do not
let transient normal Configure or Map events replace the intended state or
normal size. Apply the captured normal/zoomed target after mapping, keep the
guard through the following idle cycle, then update the grip and schedule the
usual settled preference capture. Map must not schedule a competing state apply
while guarded. Cancel any pending restore callback during destruction. Native
verification must preserve 1020x610 across zoomed hide/restore, zoomed restart,
and return to normal state.

Before begin_exit, enqueue the final preference snapshot before setting exiting
and before the worker's exit marker. This ordering is required because
manager_action currently rejects new work once exiting is true. Synchronous
App.close also flushes preferences before its stop marker and join. Configure
capture only reads Tk on the GUI thread; all SQLite writes remain on the action
worker. Cancel pending geometry, filter, hover, and render callbacks on destruction.

## Verification and deliverables

Extend focused existing unittest cases instead of duplicating implementation.
Cover migration with a copy/temp database, not the user's live queue.sqlite3.
Prove IDs survive sorting/removal/restart and never reuse removed numbers;
legacy started tasks survive removal; Start/Remove ordering is deterministic;
stale snapshots cannot resurrect acknowledged removals; pending scan results
cannot re-add after Remove; missing queued UUID actions are harmless.

GUI coverage must exercise numeric versus lexicographic sorting, tie/unknown
rules, Running first plus progress ordering, filter invalid/zero/clear cases,
visible bulk selection/download behavior, global pause/cancel/remove scope,
headers after vertical/horizontal scrolling, empty-to-populated transition,
hover across native controls, scroll quiet recovery, and stable click targets.
Test geometry normal/maximized/hide/exit preferences with mocked state where
needed, then manually verify actual Windows maximize/restore and resize grip.

When native pointer automation is unavailable, add one opt-in mapped Windows
test in tests/test_app.py, enabled only by FLD_TEST_WINDOW=1 on Windows. Use the
existing temporary-database/mock-node fixture and a fake tray, with real Tk
window state/geometry calls and no state/geometry mocks. Deiconify, set normal
client geometry to 1020x610, and await actual size plus asynchronously saved
preferences; assert the Sizegrip is mapped. Maximize, await actual zoomed state
and saved maximized=true, preserve the 1020x610 normal dimensions, and assert
the grip is hidden. Hide/restore through existing App methods; verify withdrawn
then actual zoomed state with unchanged normal preferences. Close while zoomed,
reopen the same temporary database, map, and verify actual zoomed startup.
Restore normal and verify the saved normal client size, maximized=false, and
mapped grip. Use bounded existing event-pumping waits, not fixed sleeps or a
new GUI framework. A Sizegrip event_generate drag is optional if Tk supports it;
otherwise report physical pointer dragging as manually unverified. This test
proves real mapped geometry/persistence transitions, not a user's mouse action.

Run `python -m unittest discover -s tests -v`. Run mapped-window
`python tools/benchmark_ui.py` with 150 synthetic records and concurrent progress.
Target the existing roughly 15ms normal scroll behavior; report actual mean and
p95 rather than claiming a hard cross-machine guarantee. Count shared controls
and pool slots at a 360px body viewport. Benchmark real mapped paint, not a hidden
window's item-update time. Do not broaden test repetitions after these pass.

Update README to concise end-user usage, startup/recovery essentials, and a
relative `assets/demo.gif` embed. Keep developer details here and in
development.md. Deliver a GIF captured from the actual application window using
the demo below. Do not draw a substitute UI or capture real user filenames.

## Deterministic toy demonstration

Add `tools/demo_app.py`. It imports the real App and supplies an in-memory
DemoManager implementing App's narrow manager API, including preferences,
remove_unstarted, and callbacks. It never opens QueueStore, live download folders,
FCP sockets, web endpoints, or the real queue. Its close method stops demo timers.
Start empty and use the real Find links button and action worker. Patch only
`fld.app.scrape` inside this tool to return fixed toy keys after a cancellable 750ms
delay, with ordinary scan progress events. Production scrape remains unchanged.

Use these eight filenames in this order: Orchid.jpg, Harbor.mp4, Notes.txt,
Sunset.jpg, Trail.mp4, Archive.zip, Portrait.JPG, Audio.ogg. Use stable toy CHK
keys and UUID-shaped IDs, with display IDs 1..8. Total sizes in decimal MB are
18, 120, 0.05, 12, 80, 24, 9, 8. DemoManager uses a lock, deep snapshots,
ever_started, and the same action semantics as the product. Simulate progress
only for desired-running records using a fixed half-second schedule; pause stops
increments, and cancellation preserves start history. No bytes are downloaded.

Keep demo timers faithful to production: stored elapsed contains only finished
active intervals; active_since supplies the current running interval. Start sets
active_since only if absent. Pause, cancel, and either manual or automatic
completion accumulate that interval once and clear active_since. Progress updates
must not fabricate elapsed values, restart the timer, or increment stored elapsed
while active_since already contributes. Freezing synthetic fractions does not
freeze elapsed running time. Elapsed must not decrease on completion, and pauses
must not count toward it.

Optional command-file control belongs only to the demo harness: poll a JSON
command file with monotonically increasing sequence numbers, acknowledging each
sequence. Allow setting deterministic task progress and resetting the demo.
Do not expose a remote control socket or production command interpreter.
Use actual UI clicks/typing for normal demonstrated actions; command files may
freeze progress or synchronize capture. Keep this feature outside App.

For isolated native-window verification, add a read-only `inspect` command:
`{"seq": N, "action": "inspect"}`. Its acknowledgement is exactly the existing
`seq`/`ok` envelope plus these two objects:

```json
{
  "seq": 1,
  "ok": true,
  "window_preferences": {"width": 1280, "height": 720, "maximized": false},
  "window": {
    "state": "normal",
    "client_size": [1280, 720],
    "normal_size": [1280, 720],
    "hidden": false
  }
}
```

Read window_preferences as a locked copy of DemoManager's last accepted values.
Read state from root.state(), client_size from current root dimensions,
normal_size from App's tracked normal size, and hidden from App.hidden. Capture
Tk values on the GUI thread. Pass a read-only inspection callback into the
demo's CommandFile controller; do not add an inspection API to production App.
Inspect does not flush pending writes, resize, restore, or otherwise change the
window. After a real resize/maximize/restore action, allow the existing 250ms
Configure debounce and action worker to settle, then inspect again if necessary.
Use this acknowledgement alongside actual native-window observations to verify
geometry and resize grip behavior without opening a real SQLite database.
Database persistence itself remains a temporary-database unit-test concern.

Capture story, about 15-25 seconds: start empty; enter
`https://example.invalid/toy-links` and click Find links; show the scan indicator;
show eight rows and their persistent IDs; click Download selected with the
default eight selections; show changing progress. During transfer, enable
Running first and hover a complete row, including its native Pause button.
Apply `\.jpg$` to show three matching filenames, then clear the filter to show
all eight rows again. End with filled progress bars and completed statuses.
Include a header sort if legible within the short sequence. Keep the recording
focused on URL entry, discovery, and progress, with filter/sort/hover as brief
supporting interactions. Do not click Remove unstarted in the GIF; verify that
operation through the automated tests and isolated test harness instead.

Use a real visible Tk window at a readable fixed normal size, around 1280x720,
without exposing unrelated desktop content. A native window capture tool may
provide PNG frames. Assemble those frames with the bundled Pillow runtime into
`assets/demo.gif`, using roughly 100-200ms moving-frame delays and 700-1000ms
holds for important states. The README image must match the resulting behavior;
review its first/middle/final frames and actual playback before delivery.

### Demo-owned visual export fallback

If native capture tools cannot return images, the demo may export its own
actual window through a narrowly scoped visual-test path. Keep this code in
tools/demo_app.py (or a demo-only helper); production App gains no capture or
automation API. A `--record DIRECTORY` option enables it explicitly. The demo
starts its own ordinary visible Tk window, uses only toy in-memory tasks, and
never touches the user's database, node, downloads, or other windows.

The capture function accepts the demo root and an output path, never an external
HWND. Derive the wrapper HWND with GetAncestor(root.winfo_id(), GA_ROOT=2).
Verify that GetWindowThreadProcessId reports os.getpid() on every capture;
otherwise fail. Set ctypes argument/result types correctly for 64-bit handles.
Use GetWindowRect and GetWindowDC for that HWND, create a compatible memory DC
and bitmap, select it, and call documented PrintWindow(hwnd, memory_dc, 0).
The window itself renders its real title bar, canvas, and native child controls.
Restore the previous selected bitmap before GetDIBits, which requires an
unselected bitmap. Request a top-down 32-bit BI_RGB image and write a BMP with
the appropriate headers. Release both DCs and delete bitmap resources in
finally blocks. Do not use desktop capture, screen blitting, external-window
enumeration, cursor/input injection, or generated substitutes for UI pixels.

PrintWindow is synchronous: capture only after normal Tk idle painting and do
not overlap captures. This demo export is separate from performance measurement.
Before the first capture, wait for the demo root to be mapped, its owned wrapper
HWND to be visible, and its native window rectangle to have positive dimensions.
Run idle layout once, then schedule capture through a 50ms Tk callback so native
map/paint events can run. Recheck readiness at capture time and stop with a clear
error if it does not settle within two seconds; do not block the GUI thread.
On a blank-image rejection, report dimensions, mapped/visible state, native
capture/readback return codes, per-channel extrema, and sampled RGB-tuple
diversity. Test actual RGB pixels rather than a fixed byte stride that can sample
only a constant color or alpha channel. Uniformly flat RGB frames are invalid;
varying RGB data must not fail solely because one channel stays constant.
If capture fails or frames are blank/missing native controls, stop and report
the failure with a nonzero exit status rather than manufacturing a replacement
image. Inspect the first
frame early before recording the whole sequence. The API details are supported
by Microsoft's [PrintWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-printwindow),
[GetDIBits](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-getdibits),
[GetAncestor](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getancestor),
and [GetWindowThreadProcessId](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getwindowthreadprocessid)
documentation.

Use a demo-only after-driven scenario with the real application's Tk variables,
Entry methods, Button.invoke, and checkbox controls, as in GUI integration
tests. It may call DemoManager.set_progress to supply deterministic synthetic
transfer events. No OS input scripting is involved. Schedule stages through
root.after; do not sleep on the GUI thread or prepaint fake progress bars.
Run the demo frozen so only the scenario changes fractions. Gate the rows/start
stages on their ordinary App event acknowledgements; abort with a clear error
if a gate does not settle within three seconds.

For self-export, this exact roughly 22-second variant supersedes the all-selected
capture story above:

| Time | Real application action or demo data update |
| --- | --- |
| 0-1s | Show empty window; capture readable initial state. |
| 1s | Insert https://example.invalid/toy-links into the real source entry. |
| 2s | Invoke Find links; capture the ordinary scan indicator during its 750ms delay. |
| 3s | Await eight Ready rows with IDs 1..8; hold long enough to read filenames. |
| 4s | Invoke Select none, then activate/invoke the checkboxes for Orchid and Harbor. |
| 5s | Invoke Download selected; await exactly those two desired-running tasks. |
| 6-9s | At successive seconds set fractions to (.08,.04), (.20,.12), (.34,.24), (.48,.36). |
| 8.5s | Invoke Running first; leave all eight task records intact. |
| 10s | Insert `\.jpg$` into the actual filter variable and apply through its normal handler; show three matches. |
| 11-12s | Set Orchid/Harbor fractions to (.60,.50), then (.72,.66); hidden Harbor still progresses. |
| 13s | Clear the filter through its normal handler; show all eight records again. |
| 14-16s | Set fractions to (.82,.76), (.90,.84), then (.96,.91). |
| 17s | Set fractions to (1,.96); Orchid completes normally through DemoManager notification. |
| 18s | Set both fractions to 1; both chosen files now show completed status. |
| 19.2s | Capture again after the ordinary one-second App tick: no running tasks and a 0MB/s graph header. |
| 19.2-22s | Hold the settled final view: IDs retained, two filled bars, six other files still Ready. |

An optional generated Tk Motion event may exercise the actual row-hover handler
with local/root coordinates, as a GUI test does. It must highlight real canvas
items and position the real shared native controls; do not draw a synthetic
cursor or overlay. Omit hover if it is unstable with the physical pointer.
Do not invoke Remove or change window geometry during this short recording.
Native maximize/restore verification can remain separate through the inspect
acknowledgement and native controls.

Export sequential BMP frame names plus a JSON manifest containing frame paths
and millisecond durations. Capture approximately every 200ms during visible
changes; static holds may reuse one frame with a longer manifest duration.
Keep all captures at the same dimensions. Use the bundled Pillow runtime after
recording to convert these BMPs to RGB/PNG as needed and assemble assets/demo.gif
with looping and explicit durations totaling 15-25 seconds. Frames and manifests
are temporary build artifacts; only the GIF and demo source need delivery.
Visually verify empty, scanning, eight-row, filtered, and completed frames, then
check GIF playback, dimensions, frame count, and total duration before calling
the demonstration complete.

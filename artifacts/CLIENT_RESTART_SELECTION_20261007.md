# Client restart multi-selection preservation — complete

2026-10-07, window 01a10d34-f634-7170-8c2e-2c050baf417d. User supplied launcher,
hotkey and script screenshots and asked 点击重新启动后，多选客户端都会断开，能否让他保持，因为只是重新启动.
User item IDs unavailable. Scope: preserve UI multi-selection during manual launcher
restart, not automatic resumption of scripts/combat or a guarantee of uninterrupted
game connection. No agents or applicable skills; quick memory search gave no relevant facts.

Backend XuanShu.py now records restarting handle/title before removal and transfers
the reservation to the newly launched handle. UpdateHookedClients includes restarting
titles. Auto-hook assigns that reserved title; other allocation paths avoid reserved
pN slots. Successful hooking, launch failure, disappearance or explicit unhook releases
the reservation. Native launch/account credentials and existing automation stopping
logic unchanged; no new login API or automatic Play behavior.

src/gui/main.py forwards restarting titles to selectors. tab_hotkeys.py, tab_actions.py
(script and combat), and tab_fishing.py temporarily retain checked AND unchecked values
only for explicitly restarting titles; absent clients remain unavailable. Normal removal
clears retention. Existing first-selection/all/default semantics remain for other clients.
No commands/tasks are started by restoring selections. Other client changes during
the restart are preserved.

tests/test_restart_client_selection.py: 12 new methods (four-page subcases, actual
UpdateHookedClients branch, AST-extracted manual restart/number assignment, fake
process/launch only). Checks selected/unselected values, All trap, empty single-client
interval, failed-restart cleanup/slot reuse, multiple restarts, changes to other client
choices, closed replacement, explicit unhook, reserved numbering and unrelated launches.
41 distinct related tests passed across this file, test_hotkey_group_ui,
test_fishing_group_ui, test_launcher_selection_order and test_ibao_shutdown.
Scoped git diff check passed. Files imported/AST-parsed by tests, syntax verified.

Sandbox test run hung in Python socketpair while creating the Windows event loop,
before test setup. Interrupted that exact run, traced with bounded faulthandler,
then ran inspected offline tests outside sandbox successfully; no real game/network
or account interaction. Do not count failed initialization as a test-code defect.

No executable rebuilt, no real client restarted, no real credentials accessed, no
commit/push/upload. Existing unrelated edits retained. Source work complete; user
must restart source or rebuild EXE to apply it. Actual game reconnection unverified.

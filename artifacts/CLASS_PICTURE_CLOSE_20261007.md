# COMPLETE Zafaria class-picture automatic close

User screenshot shows ClassPicture quest photo with red exit button; reports
这个不会自己关掉. Supplied log still uses prior runtime teleport line numbers.
Scope fixed this known panel only, no generic Escape/X or other popup cleanup.

Existing src/paths.py already defines WorldView/ClassPicture/exit, previously
used only in several NPC-end menu lists. Added close_class_picture_popup in
src/script_popups.py and hooked shared _close_automation_popup_owned after
loading guard. Resolves visible panel and its own exit, re-resolves both after
mouse/input wait, skips missing/hidden/loading. Existing ownership wrapper
therefore covers script watchers, solo/party quests and followers. No generic
photo discard or friend/purchase confirmation behaviour changed.

Tests: new tests/test_class_picture_popup.py seven tests (exact path, hidden/
missing UI, mouse-wait disappearance/loading, replaced panel fresh button,
shared routing priority). Ran new suite + ScriptPopupTests + pet-level suite:
32 tests PASS; one old integration fixture failed BEFORE changed code because
it lacked four preexisting quest handlers (HEAD verifies same prechecks).
Added only four false AsyncMock prechecks to that fixture, reran ONLY failed
test PASS. 33 distinct scoped tests pass. Scoped diff check PASS. No redundant
full tests, no EXE build, no live-game clicks. Source helper imported/executed
by tests; existing old EXE requires repackage/restart.

Prior fixes unchanged. Memory only used to recall exact-path and shared-popup
safety preference, verified against current implementation. Memory citations:
MEMORY.md358-362; rollout summary2026-09-05T15-51-13-5iw8... lines45-46,49-52;
rollout ID01a07244-4cd6-7b93-85d3-82e3db610c78. No skills or agents used.
Ready to deliver. Current window01a115a8-0038-7751-b18e-c6cdbfb39c93.

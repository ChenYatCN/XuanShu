# NPC already-accepted quest list loop - source fix complete

Window: 01a1160e-45fa-7dd0-8906-8852ea8f63d7.
User authorized 修复 after diagnosis of d77a0391 log + Amara Blackmane screenshot.
Evidence: 20:12:43 current quest already Snake's Footprints, then 20:12:46..57
same title selected four times and Complete dialogue replayed; list retry cap hit.
Preserve all earlier dirty work and do not package or control the live game.

Changed src/questing.py _select_npc_mainline_menu only:
- Confirm selected quest ID / owned turn-in goal progression before reselecting
  when dialogue returns directly to the menu (old confirmation branch was bypassed).
- Tracked owned quest shown in list: read current objective. If it does not require
  talking to this NPC, return to existing menu cancellation and continue current task.
- Preserve actual current-quest talk/turn-in via existing objective/name matcher;
  prioritize that row and record before_goal for same-ID progress confirmation.
- Unknown goal or NPC identity preserves menu without guessing. Existing explicit
  expected-ID selection, ambiguity/title protections, bounded retries retained.
- Recheck loading/battle/zone/quest/goal/UI/owner/stop/refill/probe before closing.

tests/test_npc_mainline_menu.py: six regressions for auto-dialogue-only owned goal,
other-NPC talk, valid turn-in, acceptance straight back to list, unknown goal,
and an already-hit old retry limit. Existing tracked-world test now supplies a
matching talk objective rather than assuming an owned quest is always selectable.

Verification: all 37 NPC-menu tests passed with mock clients; git diff --check passed.
No live-game or installed EXE validation. Restart source process / rebuild old EXE.
The unrelated 838u no-path case is still not resolved or claimed fixed by this change.

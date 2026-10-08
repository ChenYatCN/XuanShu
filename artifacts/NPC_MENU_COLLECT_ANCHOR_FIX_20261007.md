# COMPLETE NPC response + collection anchor repair

User explicitly requested 修复 with two screenshots of previous diagnoses:
NPC list selection returned Complete dialogue then incorrectly exhausted the two
unanswered-click budget; successful collection1/3 discarded anchor and resumed
full-map search. Current window01a115a8-0038-7751-b18e-c6cdbfb39c93, user item IDs
not returned. Both requested fixes implemented and proportionately verified.

Files: src/questing.py, src/collecting.py, tests/test_npc_mainline_menu.py,
tests/test_collecting.py. They were clean before this task; all other dirty work
including prior collision/Steam/chat/restart fixes preserved.

NPC: shared _record_npc_menu_dialogue called only after actual dialogue visibility,
normal dialogue and finder branches. Pending list clicks with Complete/More/Accept
responses reset UNANSWERED counter, not total count; late response revives ONLY
no_response failure. Wrong/ambiguous offers remain blocked. Completely unresponsive
list still stops after2 clicks; responding-but-unconfirmed same list has4 total
click cap. Existing actual quest identity/offer checks, ownership guards retained.
Tests cover Complete->list return, late invitation, finder late response, wrong
offer stays blocked, bounded repeated response, existing branch/stop/UI behaviour.

Collect: store first candidate XYZ ONLY on count_increased, preserve across next
worker runs keyed by zone/quest/goal/total. Nearby candidates within existing3147u
region distance only; no candidate -> verified safe collision TP back to anchor,
check actual landing (<=750u, existing prompt tolerance), then poll fresh entities
every2sec via existing wait_at_anchor. No full-map route after success. First
anchor never replaced by later pickup. Task/zone changes invalidate state; stop/
loading/battle/cancellation abort; refill/other recovery checks scoped to new
anchor branches (original general active() unchanged). Previous recent15/30sec
retry limits unchanged. Incorrect popup/failed pickup doesn't create anchor.

Verification: full directly affected two test modules,67 distinct tests PASS in
8.570s, exit0 (offline mocks;45sec faulthandler guard outside sandbox). Final
safety-guard placement narrowed to anchor branches, reran only2 affected tests:
PASS in0.401s. Scoped git diff--check PASS, final production diff inspected.
No full build, no game connection/account access/real UI inputs, no live-game
success claim. User must rebuild/restart existing EXE; source-only changes.
No relevant memory facts, skills or subagents used. Ready to deliver both fixes.

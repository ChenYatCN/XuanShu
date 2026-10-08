# COMPLETE generic nearby-landing + walk recovery

Completed 2026-10-07 in window01a115a8-0038-7751-b18e-c6cdbfb39c93. This section
supersedes the untested/NEXT wording below. Added tests/test_collision_approach.py:
12 synthetic geometry tests +24 offline mocked-client async tests all PASS (36
distinct tests,0.458s). Test command outside sandbox with45sec faulthandler exit
guard completed exit0. No failures or repeated suites. Covers floor cache/upper
floor/cliff/mesh gap/obstacle detour/interaction range/selection separation/budgets,
TP-only blockers vs actual stalls, timeouts/cancellation, zone/dialogue/battle
interruption, recovery3candidate cap,2failure threshold,30sec cooldown/reset,
strict landing failure, ordinary success and missing-geometry legacy navmap.
Scoped source syntax and git diff--check PASS; final scoped diff inspected.

Only production changes are src/collision_math.py and src/teleport_math.py. New
test and this progress record also added. Earlier dirty work preserved. Ready to
deliver: generic nearby safe landing+verified walk on repeated failures, floor/
mesh/obstacle safeguards, bounded attempts/cooldown. Existing EXE not rebuilt;
user should repackage/restart for executable use. No live-game success guarantee;
special doors/quest prerequisites and incomplete collision data still need specific
handling. Do not resume old Steam repair or add unrelated dependency fixes/tests.

User request: after explanation of pasted log, explicitly 进行修改. Log repeated
ZF_Z03_Savannah target (25252.82,2276.864,566.09), direct TP rejected, strict landing
383u away, walk truncated0/5 repeatedly. User wants generic recovery without per-quest
adapters, with terrain/floor safeguards. Current window01a115a8-0038-7751-b18e-c6cdbfb39c93;
exact new user item IDs unavailable. Earlier Steam native repair COMPLETE, do not resume.

Scope only src/teleport_math.py, src/collision_math.py and new directly affected tests.
Preserve all unrelated dirty XuanShu/XuanYi edits. No agents authorized, no skill
applies; memory quick rg had no relevant hits/facts. No real game movement or EXE build.

Implemented but NOT YET tested (syntax only passes):
- Walk-grid keeps all height surfaces, floor-aware A* state and sampled edges; rejects
  static obstacles, supplied blocked areas, mesh gaps, abrupt100u height changes.
- A* optional interaction radius allows stopping120u from target without entering object.
- find_approach_paths tries strict TP candidates within~1000u/all directions, rejects
  wrong floor/previous landings, verifies walk connection; caps12 path probes/1000nodes
  each,3 spatially separated candidate landings.
- Final-walk helper returns actual progress result. Preferred TP-blocker detour falls
  back to raw walkable mesh because TP rejection alone doesn't prove an impassable wall.
  Live stalled steps remain forbidden; each goto<=3s, entire walk<=12s, at most2 short
  attempts per waypoint. Stops on zone/dialogue/battle; cancellation propagates.
- Per-client same-target state: two failures -> alternative recovery; three failed
  candidates ->30sec movement cooldown (other interaction checks still run). Reset on
  target>20u/zone change/cooldown expiry or successful approach. Normal navmap fallback
  remains for missing geometry. No fake claim of task completion.

NEXT: add synthetic geometry + mocked-client tests for floor/edge/path/approach selection,
stall/timeout/interruption/cancellation/recovery/cooldown/reset/legacy fallback. Offline
async tests must run require_escalated due known sandbox event-loop socketpair hang;
use faulthandler.dump_traceback_later(45,exit=True). Check failures only after first run.
Inspect scoped diff, repair only task-related defects, update COMPLETE and deliver.
No EXE repackage requested; existing EXE needs user repackage/restart. Actual logs show
precise resolver unavailable; this remains evidence limit, not in-scope dependency fix.

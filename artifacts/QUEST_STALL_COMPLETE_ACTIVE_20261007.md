# Source work complete: progress-stall walk + NPC Complete button

## Final checkpoint (supersedes the earlier partial record below)

Window 01a1160e-45fa-7dd0-8906-8852ea8f63d7. Supplemental user requirements:
actually walk to the original quest TP point, not merely nearby; diagnose the no-path
log. Evidence: 6c8de927 log shows short/overshot walks rejected and later NPC retries;
d87b1680 log shows a Savannah goal 838u from the strict landing, no on-foot path,
no alternate approach, then cooldown. This does not prove an actual wall or arrival.

Completed:
- questing.py watcher now requires 30s + 3 samples, keeps direct TP until then,
  maintains walk-only mode between bounded recovery attempts (even after retreat),
  resets on actual task/zone/target progress and preserves dedicated-stage guards.
- Reentry uses existing collision recovery, 500..1500u path-connected landings,
  goal_radius=5u. A prompt alone does not truncate this actual walk; real dialogue,
  battle/loading, stop/probe/refill/ownership still stop it safely.
- Ordinary collision TP final walks and alternatives also use 5u tolerance, including
  the final waypoint. Timed goto gets up to 12 measured corrections, damped after the
  first input, rather than stopping after two short moves or mistaking an overshoot
  for a wall. Two stationary reads still mark a stall. Input and path time budgets
  remain bounded; no-path remains failure, not a blind walk or a successful arrival.
- Complete/Done/Finish buttons now clicked directly after guarded UI re-read,
  throttled 1.5s and capped at 3 unchanged clicks; actual quest/goal change confirmed
  after closure. Accept/More and title/list safety checks retained.

Verification: initial 85 affected tests passed; after supplemental exact walk/correction
changes, 67 collision/reentry and special-stage checks passed; final prompt guard
adjustment passed its 3 affected tests. No full-suite/build/live-game verification.
Touched tests: collision approach, trigger reentry, NPC menu, one Overgrown Estate
Complete-button fixture. No subagents, broad cleanup, commits, or packaging.
Old EXE must be rebuilt; source process restarted. If 838u no-path persists after
reload, new runtime evidence is needed; every coordinate's reachability is not proven.

## Earlier partial record (historical, not pending work)

Current window01a115a8-0038-7751-b18e-c6cdbfb39c93. User explicitly 也修补一下缺口:
keep direct TP speed; after same goal30sec without task/interaction/zone progress,
TP farther then walk. Latest added screenshots show selected NPC list becomes
dialogue with 完成 button; user says frequently stuck there. These requests both
active. Earlier source fixes complete; preserve all existing dirty work.

Inspected current _maybe_reenter_quest_trigger: existing snapshot+owner+special-stage
guards, seen3 observations,350u near distance,2 attempt limit; _reenter currently
blind goto away900 then goto exact target. _trigger_reentry_blocked treats ANY nearby
prompt as blocked. Need use matching prompt when tracking this target, avoid suppressing
recovery because wrong nearby NPC. No new global movement system; reuse this watcher.

Changes SO FAR UNTESTED for this request:
- collision_math.find_approach_paths optional min/max distance and goal radius.
- Small goal_radius can safely refine from last hex node to exact goal on connected
  mesh, needed because35u tolerance is less than100u grid spacing.
- teleport_math._walk_remaining_to_target configurable goal_radius and async
  stop_condition; _recover_near_target accepts same plus distance bounds.
These partial changes not yet connected to quest watcher! No claim of completion.

NEXT: add quest recovery wrapper loading collision world and reusing _recover with
500..1500u candidates/35u forced walk; callback stops on task/zone/target/UI changes.
Use existing _trigger_reentry state timer30sec +3 samples, retain walk-only during
recovery retries (no direct TP between),2 attempts bounded; reset on actual progress.
Preserve all dedicated-stage exclusions. Update existing reentry tests with controlled
clock and add forced-walk/radius/callback tests. Then latest NPC fix: selected list
dialogue Complete currently goes SPACEBAR; click actual visible advance button when
caption recognized Complete/Done, with existing ownership/state guards. Screenshot
requires actual completed stage, not just resetting unanswered counter. Add regression.

Run only affected modules (mock clients, require_escalated due sandbox async hang,
faulthandler45sec). Existing collision36tests/NPC prior suite passed BEFORE partial
changes; do not reuse results as current verification. No real game/UI/account access.
No EXE build requested. No subagents or skills apply, prior memory scan no stall hits.

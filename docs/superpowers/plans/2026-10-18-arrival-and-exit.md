# Arrival-without-popup and dungeon exit implementation plan

> **For agentic workers:** Use superpowers:executing-plans for the implementation and verification below. Diagnostics for the two independent bugs may run in parallel; production edits stay with the primary agent.

**Goal:** Stop same-target arrival/no-popup navigation spin and repair the existing quester dungeon exit handoff without changing Deimos ordinary X.

**Architecture:** Bug A uses a client-scoped arrival recovery budget, separate from the legacy evaluator receipt. Bug B must preserve the existing quester-first movement/exit action and hand confirmation to the existing session observer; no new party architecture or general END fallback.

**Tech Stack:** Python 3.13, asyncio, existing wizwalker/Quester/DungeonSession, unittest offline clients.

**Spec:** User attachment `C:/Users/ChenYat/.codex/attachments/21c8d8e2-1a8f-47a8-8a93-3a639028d9e1/已粘贴的文本.txt`.

## Constraints

- Maximum two production files and five existing production functions. No teleport_math changes. Stop before exceeding either limit.
- Preserve all existing dirty/untracked work; baseline is `Back/arrival-exit-20261018`, not HEAD.
- Ordinary X remains runtime safety + npc_range/readable popup + current quest_position distance <750 + localized branch. No evaluator permission gates.
- At most two full pure-popup A-search attempts, followed by explicit pending/blocked movement suppression. Clear on lifecycle changes, allow ordinary X when popup appears.
- No task/map/object/coordinate allowlists, packaging, commit, push, reset, clean, checkout or entry/session rollback.

## Review focus

- Already at target vs real failed movement: only actual successful arrival can suppress navigation.
- A-search never sends X, never calls evaluator, and never renews attempts from worker recreation.
- Missing title must not veto an otherwise readable ordinary popup; no speculative UI path.
- Task/zone/target/loading/battle/dialogue change must retire the old recovery state.
- A real exit/load must hand to confirmation, not cancel its own owner; task/token replacement must still cancel stale input.

## Task A — Arrival recovery

- [x] Pin both solo and leader production caller chains with focused RED tests, including second-search success, two failures, natural popup, evaluator isolation and readable untitled popup.
- [x] Compare reader with upstream d7ef56eb51c5b5316cadba40ca554ec442a5dcee and current HEAD's prior object implementation.
- [x] Add bounded arrival-without-prompt state near existing search/navigation responsibility, retaining normal worker cadence.
- [x] Run GREEN and Deimos/A-search/object/Talk/Photo/movement regressions; distinguish unchanged baseline failures.

## Task B — Existing dungeon exit owner

- [x] Read final task → shared target → movement → exit confirmation → observation/retire, distinguish the harmless source-replay reject log from a true block.
- [x] Verify the actual persistent worker's next tick handles departure and exit UI correctly. Replace the initial raw-helper handoff hypothesis with RED for stale probe pending after transition absence/expiry; retain safe retry/task change/peer exit/retirement coverage.
- [x] Release only unowned stale probe pending in established, ready sessions; retain real follower probes and initial/potion gates. Keep original movement/exit owner and peer-crossed guards; no new END exit.
- [x] Run GREEN and dungeon entry/transition/exit/session/follower/rescue regressions; distinguish unchanged baseline failures.

## Final verification

- [x] Targeted remaining regressions listed by the user; report baseline failures separately without treating them all as infrastructure errors.
- [x] Verify function/file count, baseline hashes, no new ordinary evaluator call, no hardcoding, AST, py_compile and git diff --check.
- [x] Save an 18-point report at Back/arrival-exit-20261018/REPORT.md. Final sentence: 尚未实机验证。

# Solo probe movement release implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline. Freeze the behavior oracle before writing RED or production code. One read-only review at the end.

**Goal:** Prevent one terminal follower from releasing quester movement before current-party probe results settle, while preserving the successful solo gear lifecycle exactly.

**Architecture:** Change only ordinary probe release authority in XuanShu.py. Keep complete_zone_probe, gear, restore, detection, FriendBusy cleanup/backoff, arrival, follow, DungeonSession and ordinary interaction unchanged. Reuse current follower result/reference and the existing primary probe owner; no new generation or manager.

**Tech Stack:** Python 3.13, asyncio, real extracted app closures, existing offline clients.

**Spec:** `C:/Users/ChenYat/.codex/attachments/69d0eac8-d5af-466a-b0b3-4d506cb2299b/已粘贴的文本.txt`.

## Constraints

- Task baseline is Back/solo-probe-release-20261019, including all existing dirty/untracked work.
- At most 2 production files, 3 existing functions and 1 new pure predicate helper. Stop before changing any forbidden lifecycle or fourth existing function.
- No package/commit/push/reset/clean/checkout or unrelated changes; stay in the user-authorized dirty checkout.
- The oracle requires complete → set2 completed → solo committed → movement release → worker restart, exactly once. Freeze complete_zone_probe body and its successful caller post-effects.

## Steps

- [ ] Trace terminal publication, ordinary clear/release, primary solo completion and questing.py consumer.
- [ ] O1/O2: actual successful solo/multiplayer closures and observable state/event order PASS before production changes. Include set0 restore as an unchanged oracle.
- [ ] RED R1–R8: current terminal + pending/cleanup/unknown/stale peers cannot release; all unavailable reuses old complete path; any real arrival follows multiplayer; exactly once and next lifecycle isolation.
- [ ] At the release boundary, require current expected results and use the existing primary completion path. Do not edit complete_zone_probe/detection/equipment/cleanup/arrival/dungeon/follow bodies.
- [ ] GREEN both oracle and focused tests, then requested targeted regressions; compare failure/error IDs to baseline.
- [ ] Independent read-only review and proportional fixes within the same scope.
- [ ] AST/source-byte audit of every forbidden function, baseline hashes, py_compile, AST parse, git diff --check; save 16-point report and live limits.

## Initial evidence

The offending ordinary terminal fast path calls clear_ordinary_quester_probe before refreshing the current reference. other_ordinary_probe_active only recognizes present pending nonterminal states. The existing successful release helper already calls determine_solo_zone → complete_zone_probe(True) under the primary probe, then publishes terminal and logs release. Quester only returns False after quest_party_probe_pending is already False; its friend-probe-terminal log does not independently grant permission.

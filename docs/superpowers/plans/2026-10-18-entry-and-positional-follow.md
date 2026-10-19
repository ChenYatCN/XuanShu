# Dungeon entry lifecycle and ordinary positional follow implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline, in strict A then B order. One fresh-context review after both tasks.

**Goal:** Release stale dungeon-entry one-shot receipts only across invalid lifecycles, then restore the existing ordinary same-zone follower movement handoff.

**Architecture:** A stays with `_enter_deimos_dungeon` and only proven toggle/reinit cleanup owners. B will reuse the actual follower coordinator, movement owner, threshold and cooldown found in current source, without changing A's lifecycle.

**Tech Stack:** Python 3.13, asyncio, Quester/DungeonSession, current unittest offline clients and AST-extracted app functions.

**Spec:** `C:/Users/ChenYat/.codex/attachments/bb64f6ce-21f4-4c8d-8fe3-0a564a7bc1aa/已粘贴的文本.txt`.

## Constraints and rulings

- Baseline: dirty-worktree snapshot `Back/entry-follow-20261018`; preserve all pre-existing changes. No reset/clean/checkout/package/commit/push.
- Maximum two production files and five existing production functions; stop before exceeding either budget. Small single-purpose helpers only.
- Preserve Deimos ordinary X, two A-search attempts, arrival suppression, title optional, Photo/Z, Talk, FriendBusy, gear, rescue, mainline, exit, GUI/ibao/Crown Shop.
- No new map/quest/NPC/entrance/coordinate allowlists or distance constants.
- Work in the user-authorized current dirty checkout; no worktree move or commits, overriding generic skill steps.
- Current source proves entry receipt `_deimos_quest_x` outlives `clear_dungeon_session` and `_init_client_attrs`; `deadline` is never consulted by the matching receipt gate. Do not fix by expiring live entry X and repeating it.

## Review focus

- Live session and in-flight preparation must still suppress duplicate X across worker recreation.
- A cancelled before-send attempt must not leave an unowned permanent receipt.
- Retirement/stop/rehook may release stale receipt, but no stale owner may clear a replacement receipt.
- B must distinguish verified instance arrival from continuous positional following and retain higher-priority movement owners.
- Position following must stop near the quester, resume when distance grows or unsafe state ends, and use existing thresholds/cooldowns.

## Task A — Entry receipt lifecycle

- [x] Trace accepted diagnostics, actual enter caller, prepare/begin/send, stop/reinit cleanup.
- [x] Add real caller RED covering A1–A7, active-session idempotence, pending outcome cancellation, retirement, reinit and evaluator isolation. Confirmed RED: 9 tests, 4 failures, 0 errors.
- [x] Bind entry receipt to its session/hook/in-flight owner; clear only entry receipts in proven lifecycle cleanup paths. Add deduplicated `[dungeon_entry_flow]` diagnostics.
- [x] GREEN: 9/9 focused and 86/86 protected tests. Entry group 90 tests retains exactly its baseline failure `test_initial_entry_loading_wait_returns_with_unconfirmed_session`; failure/error IDs compared. A completed before B investigation.

Ruling: reinit fixture must provide the camera API once it supplies game_client; startup toggle probe must complete before a qualified entry attempt. These are existing safety contracts, so no production readiness checks were loosened to satisfy incomplete fixtures.

## Task B — Same-zone positional follow

- [x] After A GREEN, trace `_follow_quester_session`, verified-area branch and actual movement coordinator. Use current diff/blame/history to establish origin.
- [x] Real B1–B8 tests: existing continuous near→far→far follow already works. RED catches missing input ownership, stealing cleanup owner and TP every tick after no landing.
- [x] Keep the existing positional branch and 900u threshold. Add the shared input owner, existing fresh-follow preflight and follow backoff; retain verified-area friend-probe skip and special owners.
- [x] GREEN: B focused 11/11; final B regression 250/250. A focused 10/10; final A regression 186 tests retains exactly the original loading-wait failure. Compare failure/error IDs with baseline.

Ruling: user explicitly chose to retain 900u. The reported 437u/179u/78u gaps are within the original range; do not claim that log alone demonstrates a broken positional handoff. Preserve the already-working continuous follow and repair only reproduced input-ownership/backoff gaps.

Ruling: the first review found sender-preflight cancellation still retaining an unsent receipt and an overly broad unconfirmed-session follow veto. Both received real-caller RED→GREEN corrections within the same four existing functions. Source.entry_sent=False refunds the reservation; unknown input remains protected. Session identity/entry-wait checks replace the blanket veto.

## Final verification

- [x] Fresh-context read-only review; two Important findings corrected through RED→GREEN, final review clean and reviewer independently passes all 21 focused tests.
- [x] Check all 2715 baseline file hashes: only two production files changed, four existing functions and one new diagnostic helper. All other module AST unchanged. No ordinary evaluator or distance changes; diff, AST, py_compile, git diff --check passed.
- [x] Save requested 23-point report to Back/entry-follow-20261018/REPORT.md. Full test discovery was also attempted with fail-fast: baseline and current both stop at test 59 on the same pre-existing missing refresh_member fixture error; no claim of a fully passing suite.

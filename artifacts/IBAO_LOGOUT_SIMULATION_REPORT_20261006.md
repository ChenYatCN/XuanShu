# ibao logout follow-up simulation, 2026-10-06

## Latest: three risks fixed and verified

User subsequently requested 进行修复. Completed in window
01a10d34-f634-7170-8c2e-2c050baf417d (user item ID unavailable).
Logout now clicks one freshly observed control per polling pass, prioritizes
confirmation, and rechecks modal/Play/loading before clicking Quit. No change to
the generic helper used elsewhere. Transient UI-read exceptions are retried
locally with .12-second polling and a finite budget; HookNotActive,
ClientClosedError and cancellation propagate rather than being swallowed.
Menu time and loading time have separate cumulative budgets (15 / 60 seconds).
Exhaustion raises existing CharacterSelectionError for existing recovery without
the generic 180-second inactivity wait. Hung operations remain timeout-bounded.
Character verification and the supervisor/relaunch implementation are unchanged.

Final 16-probe simulation run: passed=16, failures=[]; overlay now produces one
Quit click, one confirmation click, one Play; transient read now enters target;
16-second loading now enters target, with no input during loading. Times are
accelerated/offline, not live measurements. RuntimeError recovery-route probe
still deliberately confirms the generic policy is unchanged; handled transient
reads no longer escape to that policy. Runner now asserts outcomes and exits
nonzero for any mismatch.

50 distinct directly affected tests passed: final 23 character/logout tests plus
27 manager/shutdown tests that passed in the first run; only test fixtures changed
between these runs, so those 27 were not redundantly rerun. Initially four fixture
errors/one failure came from async mock lambdas returning unawaited coroutines;
changed them to actual async callbacks, then all 23 character tests passed.
Six new regression methods cover overlay priority, transient/persistent reads,
slow/permanent loading and missing-connection propagation. Scoped git diff check
passed. No build, real game connection/input, real restart, commit or upload.
Source changes complete; running source must restart, and old EXE needs repackaging.

---
Historical detection-only report follows; its three findings are superseded by
the verified source fixes above.

Request: 模拟检测是否有其他问题. Detection only; no production changes.
Window: 01a10d34-f634-7170-8c2e-2c050baf417d. User item ID unavailable.
Runner: ibao_logout_simulation_20261006.py. Uses create_session's real cloned
logout_and_in, is_visible_by_path and click_window_until_gone, fake UI windows,
fake keyboard/mouse, and real IbaoGroups for recovery routing. No game connection,
real input, account restart, API call or executable build. Prior 44 passing tests
were not rerun because production files were unchanged.

Final run: 16 probes; process exit 0 indicates the diagnostic runner completed,
not that all scenarios behaved desirably. Time scale 0.1: 15 game seconds maps to
1.5 wall seconds. Initial 0.01 scale was rejected for analysis due Windows timer
granularity producing artificial normal-path timeouts; rerun above timer
granularity produced the results below. Durations are simulated, not live timings.

## Three remaining risks reproduced

1. If confirmation overlays Settings while Quit still reports visible, the
   generic click-until-gone loop never returns to the outer confirmation check.
   Simulation: 47 Quit clicks, 0 confirmation clicks, no Play, then 15-second
   CharacterSelectionError. This is conditional on real UI visibility flags;
   it does not prove that the supplied game screenshot had this exact condition.
   Locations: ibao_core.py click_window_until_gone around 113, logout around 530.
2. A single transient RuntimeError while reading is_loading bypasses the local
   logout loop and short recovery error classification. The worker raises; real
   IbaoGroups recovery probe with inactivity_timeout=180 did not begin immediate
   recovery, whereas CharacterSelectionError did. Runtime source confirms generic
   exceptions wait the remaining inactivity interval. No 180-second wall wait used.
   Locations: ibao_core.py around 522; ibao_runtime.py generic-error branch ~391.
3. The fixed 15-second timeout includes healthy loading. Simulated loading for
   16 game seconds aborts at 15 seconds without any input, leading to recovery
   even though the game could complete loading. This is a timeout-policy risk,
   not evidence the real game requires 16 seconds. Location: ibao_core.py ~520.

## Expected behavior observed in other probes

- Delayed settings: target entered; ESC/Quit/confirm/Play each once.
- Existing settings: target entered; no ESC; Quit/confirm/Play each once.
- Existing confirmation: target entered; no ESC or Quit; confirm/Play once.
- Already on selection: target entered; only Play once.
- First ESC lost: recovered via second ESC; target entered, Play once.
- Delayed confirmation: target entered; confirmation clicked after appearing.
- Quit vanishes on helper lookup: next polling pass recovers; target entered.
- Two-second loading then settings: target entered; zero input during loading.
- Controls never appear: short recovery error; no Play.
- Hung loading read: short recovery error; no input or Play.
- Cancellation: propagated cancellation; no Play.
- CharacterSelectionError recovery routing: immediate existing recovery reached.
- RuntimeError recovery routing: generic inactivity-delay route observed (risk 2).

Recommend separate fixes only after user authorization: exit the inner Quit loop
when modal/selection appears, retry transient read errors within a bounded stage
while preserving HookNotActive/cancellation, and separate menu-transition budget
from bounded loading allowance. Do not remove safeguards or blindly click Play.

# COMPLETED unattended efficient production-line character selection

## Follow-up completed 2026-10-06: logout stuck on Settings

LATEST follow-up: user asked additional simulation, then explicitly 进行修复.
Three resulting risks fixed: logout uses single observed-control clicks with
modal priority/fresh checks; transient UI reads retry without swallowing missing
connection/cancellation; menu15sec and loading60sec have separate cumulative
allowances. src/ibao_core.py and tests/test_ibao_character_selection.py only
production/test changes; generic shared helper and manager/relaunch unchanged.
50 distinct affected tests and final16 offline probes passed, scoped diffcheck
passed. See IBAO_LOGOUT_SIMULATION_REPORT_20261006.md for evidence and history.
No EXE build or live-game input/restart, no commit/upload. Complete, ready to deliver.

User supplied live log and Settings screenshot, then explicitly requested 修复.
Window 01a10d34-f634-7170-8c2e-2c050baf417d; user item ID unavailable.
Old logout sent ESC once and immediately tried Quit; when Settings opened late,
Quit could be missed and later loops never retried it. An isolated simulation
confirmed Settings visible with zero Quit clicks; no real game input performed.
Changed only logout_and_in's pre-selection transition in src/ibao_core.py:
observe loading/Play/confirmation/Quit each poll, click existing visible controls,
send ESC at most once per second only when neither menu nor confirmation is shown,
wait without input while loading. A 15-second asyncio timeout covers reads/clicks
and raises existing CharacterSelectionError for immediate existing recovery.
Existing character verification, per-click hook/cancel cleanup, supervisor and
relaunch implementation unchanged. Added five regression test methods to
tests/test_ibao_character_selection.py and adapted its fake client/asyncio for
loading/timeout. Delayed menu and confirmation, pre-opened settings/confirmation,
loading input suppression, hung click cancellation/timeout, user cancellation,
and already-selected/no-ESC behavior covered. 44 directly affected tests passed
in tests.test_ibao_character_selection/tests.test_ibao/tests.test_ibao_shutdown;
scoped git diff --check passed. No EXE build, live game input/restart, commit or
upload; unrelated dirty edits preserved. Source complete; needs source restart or
user packaging to update EXE. Live-game behavior remains unverified.

Completion window 01a10b90-cda5-7742-a5eb-f40be7aeb571. Recovered latest user request from current thread read: user item 01a10b87-89f5-79e2-bc36-02f3bab9aac7 in turn 01a10b87-89b8-7481-8181-0c9d0476fe60 requested continuous unattended operation without efficiency regressions.

Implementation and directly affected verification now complete. Added tests for target changing immediately before Play, persistent unreadable selection, transient read failure after page flip, selection cancellation, immediate selection-error recovery with 180-second ordinary timeout, recovery exception then empty result then success, missing-client protection throughout retries, cancellation during retry sleep preserving another client. Updated only test_ibao_shutdown fixture from obsolete wizlaunch.launch_instance mock to current launch_account_instance mock; production launcher unchanged by this task. All 39 tests in tests.test_ibao_character_selection, tests.test_ibao, tests.test_ibao_shutdown passed (2.065 seconds). Scoped git diff --check passed; AST syntax check passed for five changed Python files. Only subsequent code change was spacing in a comment.

No executable built, no game input or real relaunch performed, no live throughput or long-duration game verification. Recovery requires an existing vault-account association. Normal-path no added fixed wait, selected target avoids unnecessary Tab and duplicate Play; recurrent failures use 5..30-second backoff and failed recovery retries every 30 seconds until stopped. Permanent service/network failures cannot guarantee uninterrupted collection. Explicit closed/missing-client and HookNotActive behavior retained. No relevant memory facts used. Deliver concise Chinese outcome and limits; no further work required within authorized implementation scope.

---
Earlier in-progress checkpoint follows for traceability; its pending steps are superseded by completion above.

Window 01a107fb-978e-7702-b152-d900569b9c44. Latest user goals (item IDs unavailable): asked selection cycles all characters; answered yes eligible-list and found4sec timeout blind Play. Asked how fix; only read/proposed pause (no edits that turn). User then explicitly requested 我需要不需要人工检查持续不断，而且效率还不能出问题. This authorizes implementation of automatic selection recovery, continuous retries, no blind Play, preserving normal-path efficiency. User informed can't guarantee zero interruptions from game/network; normal operations should avoid extra sleep. Prior chat API/gear/BMP fixes complete and delivered; not current goal. No agents allowed, no computer-use, user owns EXE builds, no actual input/relaunch/API/commit allowed or done. Preserve dirty checkout.

Repo D:/XuanShuTool/XuanShu-Project/XuanShu, Python .venv/Scripts/python.exe -X utf8. Skills none for local software code. Earlier memory quick searches found irrelevant topics; no fact used. No need redo broad exploration.

Relevant facts read:
- src/ibao_core.py wizardInfo.__eq__ onlyName+Level; startup azothFarmer scanseligible characters by configured location. logout_and_in originally TAB before reading, optional flip and stale wizard reuse,4sec deadline breaks then unconditional Play, duplicatePlay onmatch. needSwitch=False means reload CURRENT wizard; caller previously passed nextWizard (ignored before); new strict validation needs caller to pass wizard in this case.
- src/ibao_runtime.py IbaoGroups per-client groups, supervisor monitors progressSignature, ordinaryerrors wait180sec thenrecovery, repeated input notprogress. Original3consecutive restarts stops/manual required. recoveryNone is meaningful external limitation. remove_missing does NOT stop groups with recovering=True, so keep flag throughout retry waits. _recover_ibao_client in XuanShu.py4336 resolvesvaultnick thenexisting_relaunch_managed_client closesold,launchesnew/login/hooks/replaces; missingvaultlogin cannotinventcredentials. Don't modify actualrelauncher casually.

Current changes (APPLIED, not finalverified yet):
- src/ibao_core.py adds CharacterSelectionError(RuntimeError), class reused by cloned session globals. logout_and_in afterlogout readsselectedname/level/location via localasync selected_wizard (rejectsemptyname/level).3boundedlocalscans; budget max(4,CHARACTER_SWITCH_DELAY*8+1) each, scales with delay. If target alreadyselected verifies2freshreads thenonePlay (noTab, noold .5sleep). OnlyTab after currentnotmatch. Seenkeys(page,Name,Level) detect completepagecycle; flips onlywhenpagecycle exhausted and otherpageunseen, rereads afterflip. No stalewizard after exceptions; sleep configured interval onreadfailure. Exhaustion raises CharacterSelectionError, neverPlay; cancellation propagates. Same-characterreload validated too; azothFarmer line~460 caller now nextWizard ifneedSwitch elsewizard. Warning andlocalretryinterval .3default. No globalwizardInfo equality changes.
- src/ibao_runtime.py new internalconstants IBAO_RECOVERY_BACKOFF=5.0, IBAO_RECOVERY_RETRY_DELAY=30.0. Supervisor recognizes CharacterSelectionError and skips180secwait; othererrorsretainexistingidlethreshold. No3restartstop; after3consecutive failures progressively5..30secbackoff; collectioncallback resetscounter. Backoff is INSIDE recovering=True try/finally to preserve missing-client guard. Recovery exceptions/None result retriedevery30sec with flagTrue; cancellation propagates, checks stopping. Successful replacement updatesworkers/oldflags sameexisting. No recovery handler still explicit failure; offline/HookNotActive behavior remains existing (not broadened to reopen explicitclosedclient). Normalpath unchanged exceptselectorquicker.
- tests/test_ibao_character_selection.py NEW8methods using create_session and localfakeasyncio+fakeclock, no live input: selectedtargetnoTab/1Play,order2Tabs,missing3scansnoPlay,nozonewait,pageturnre-read,seventh disabled,delay10notpremature4sec,transientreadfailure,noswitchreloadverified.
- tests/test_ibao.py original test_three_restarts_then_stops_despite_repeated_input updated to test_more_than_three_restarts_continues_and_remains_stoppable; patchesbackoff=.001,waits4recoveries,checksstillrunning,finallystop. Existing repeated-input timeout guard remains.

Verification so far:
Ran test_ibao_character_selection + test_ibao + test_ibao_shutdown:32methods,31passed. Only ERROR tests.test_ibao_shutdown.IbaoShutdownTests.test_cancelled_replacement_drains_config_and_unhooks_new_client. AST-extracted XuanShu.py _relaunch_managed_client fixture namespace missing launch_account_instance at4246; task raisesNameError and readywait timesout2sec. It likelypredatesourchange (XuanShu.py untouched this task), MUST inspectfixture/currenthelper/baseline to confirm; don't casuallypatchunrelatedproductioncode. Latest session no running testprocess, commandcompletedexit1. User informed existingrestoretest dependency missingneedsclassification, andremainingrestore/cancel/cross-pagechecks pending.

Next steps REQUIRED beforefinal:
1 Inspect relevantshutdownfixture (~140-190) and launch_account_instance definition/import toconfirmmissingtestnamespace, maybe boundedfixturefix ifdirectlynecessaryverification orrecordpreexistingfailure. Avoid wholebackendread.
2 Add targetedmanager tests: CharacterSelectionError immediaterecovery evenlarge180 idlethreshold; transientrecoveryerror/None thenresumes (patchretrydelaytiny); cancel while recoveryretry/backoff stops and preservesotherclient; no3stop andcollection reset alreadyexistingbutensurepasses.
3 Addselector criticaltests: currenttargetchanges betweenfirst/secondfreshread =>no wrongPlay; stale/exception afterflip noenter; cancellation propagates noPlay; empty/persistentfailedread raisesratherthan staleuse. Check-loop never blindPlay afterflip/timeout.
4 Run directlyaffectedtests only; passing31unchangedneednotrerununnecessarily unlesscodechanged. ASTsyntax/scopedgitdiffcheckpending. Need possiblecode review ofcaughtPlayexception andreadtimeouts: currentlyselected_wizard await notwrapped individual timeout; supervisor overall180sec bounds, acceptablebutroutine maxdeadline doesn't boundhungread. Don't expandunaskedabstractlayers.
5 Savecompletioncheckpoint, Chinesehandoffefficientfastnormal/noarbitraryPlay/unattendedretries/backoff; truthful noEXEbuild/livegameverification/absolutecontinuousguarantee. Vault-associatedclient needed forrelaunch, can'tinventlogin/solvepermanentnetworkoutage. No newhumanpauseforrecoverablecases.

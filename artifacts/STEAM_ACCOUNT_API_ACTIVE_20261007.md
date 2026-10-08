# COMPLETE Steam account API repair

Completed 2026-10-07, window 01a115a8-0038-7751-b18e-c6cdbfb39c93.
User request recovered via current read_thread, user item
01a115a5-c5fb-7ed2-85fb-b5436c7461ad: 修复这个问题, screenshot missing
wizlaunch.create_steam_account. This section supersedes all ACTIVE/NEXT text below.

Confirmed Rust implementation and module registration already existed in
libs/wizlaunch/src/python.rs. Actual .venv module version 0.3.1 lacked the API;
distribution version 0.1.0 did not distinguish the stale binary. Git history
2c0aec3 confirms old metadata list filters out credential-less entries, so merely
setting Steam mode/reordering cannot safely substitute for native Steam creation.
No compatibility/fake-password code was added, no Rust source changes needed.

Rebuilt current Rust source and installed native .venv module with maturin develop
--release --locked --manifest-path libs/wizlaunch/Cargo.toml; build session 52253
exit0, release compile 22.36s. Fresh Python import confirms actual .pyd exports
create_steam_account plus all required spec APIs. Previous installed package and
dist-info preserved under .build-tools/wizlaunch-before-steam-api.
Existing Rust compiler not found; installed official Rust1.88.0 minimal into
project-local .build-tools/cargo and .build-tools/rustup using --no-modify-path.
System PATH unchanged. Compiler/build downloads required authorized escalation;
did not invoke the destructive existing full packaging batch or rebuild EXE.

Changes: XuanShu.spec now checks create_steam_account before packaging and provides
rebuild command for missing APIs (3 added lines). New tests/test_wizlaunch_api.py
checks actual native exports, rejects old same-version mock, rejects only-missing
Steam API, and calls empty-nickname validation using isolated temporary APPDATA
without writing metadata. Seven tests passed in initial scoped run (3 new API,
4 existing private-server helpers). Empty-nickname test body passed but sandbox
temp cleanup failed WinError5; reran ONLY that test unsandboxed: PASS. Thus eight
distinct scoped tests passed. Scoped diff check and syntax passed; no repeated
full suites/build. All other existing dirty user work preserved.

Required work complete. No true account save/login, real account credential read,
or game interaction tested. Existing running source process must restart to load
new native module. Existing packaged EXE still embeds old dependency and must be
repackaged via XuanShu打包.bat, then restarted. Final clearly deliver project fix,
8 tests, packaging/restart needed for old EXE; do not claim live Steam login fixed.
No relevant memory facts/skills/subagents used for this repair.

Window 01a10d34-f634-7170-8c2e-2c050baf417d; latest user item ID unavailable.
User explicitly requested 修复这个问题 with screenshot account-save error:
module 'wizlaunch' has no attribute 'create_steam_account'. Log GUI SaveAccount,
2026-10-07 17:14:05.477 handle_gui:6371. Active goal fix this exact Steam save bug.
Root D:/XuanShuTool/XuanShu-Project, actual repo XuanShu subdirectory. Preserve dirty
earlier ibao/chat/restart selection fixes; no broad restore/cleanup. No subagents
authorized, none spawned. No skills apply so far. Memory quick rg wizlaunch and
create_steam_account returned no relevant facts; no memory citations required.
No notes/history tools available in metadata, using this file as continuation record.

Only initial inspection done; NO changes for this request yet. User commentary:
报错是当前加载的 wizlaunch 模块没有 create_steam_account 接口。我先核对账号保存调用、项目中的接口实现和打包配置，确认是接口缺失还是加载了旧模块，再针对性修复。

Facts from last tool:
- XuanShu.py SaveAccount case ~6350: unpacks nickname, steam_mode, private_mode;
reject simultaneous Steam/private, reject has_account; Steam calls
wizlaunch.create_steam_account(nickname); normal mode prompt_save_account in
asyncio.to_thread then set_account_steam(False); then set_account_private_server.
Catches RuntimeError/AttributeError, logs failure, always updates account-list UI.
- libs/wizlaunch/wizlaunch.pyi line15 declares create_steam_account(nickname)->None.
- rg create_steam_account in libs/wizlaunch/src/lib.rs gave no hit! Need inspect
lib.rs and other Rust modules/export definitions, implementation may be elsewhere
or missing registration. Do not infer implementation exists from stub.
- XuanShu.spec line87 require_venv_package('wizlaunch'), line96 imports current
native wizlaunch; _required_wizlaunch_api tuple ~97..105 checked with hasattr at106;
raises if missing listed APIs, emits module __version__ at115; hiddenimports and
collect data include wizlaunch. Need read tuple to see missing create_steam_account.
- libs/wizlaunch/pyproject.toml project version0.1.0, build maturin1..2 release
pyo3 extension, uv cache keys pyproject/Cargo.toml/Cargo.lock/src/**/*.rs.
- git status scoped libs/wizlaunch & spec showed no existing changes in last call.

NEXT: inspect Rust API registration/Steam creation implementation and installed
.venv wizlaunch exports/version/file (metadata only; do NOT call credentials APIs
or read account data). Inspect spec required tuple/build batch dependency handling.
Find why loaded native lacks API; implement narrow compatible fix or rebuild
existing native module as warranted; do not invent Steam fake-password semantics.
Add targeted save/export/build-guard tests. No real accounts/saves/logins unless
explicitly authorized. EXE full build not automatically requested; user usually
packages themselves, final distinguish source/native install vs old EXE.

Earlier tasks completed, not active:
- Restart multiselect preservation: manual RelaunchClient reserves title in
_relaunching_clients handle-> {'title','launching'}, payload restarting titles,
auto-hook restores pN; other allocations avoid reservations. UI hotkeys/bot/combat/
fishing cache checked+unchecked only during explicit restart, backend releases
on failure/disappearance/unhook. 41 targeted tests pass. No EXE/live restart.
See CLIENT_RESTART_SELECTION_20261007.md. These edits remain dirty and belong to user.
- Latest Subata request D:/Subata/subata inspected READ-ONLY installed Flutter3.0.18.
Static app.so strings show KINP V3 website/Steam, web token capture URL/fragment/
cookie, MSG_USER_AUTHEN_V3(27), MSG_WEB_AUTHEN24/VALIDATE25, login.us.wizard101.com12000,
response14/16, Stage3 game launch. No source/personal caches read. Official provided
download URL lsmhq.github.io/subata/subatamd/download.html verified3.0.18; public
github.com/lsmhq/wizard101_start_flutter main repo page showed readme.md only.
Explained branch order/fallback/params not verified. Completed, do not resume it.

Environment issue: sandbox Windows asyncio event-loop socketpair can hang before
tests initialize (confirmed bounded faulthandler). For inspected offline async/UI
tests use exec_command require_escalated with justification no real game/account
access; last scoped regressions passed outside sandbox. Avoid hanging unbounded
tests; faulthandler.dump_traceback_later(30,exit=True) wrapper available.

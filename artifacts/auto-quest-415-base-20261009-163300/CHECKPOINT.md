# 4.1.5 自动任务基底实施续接

当前窗口 01a11f99-521f-7c01-aa8c-85c0c601c342。最新用户明确批准实施“4.1.5 自动任务基底、逐项移植有效修复”，只自动任务及直接流程，保留当前界面启动器无关功能；要求列明旧11失败、验证后给游戏实测清单再打包。不要停止在方案，必须完成源码和对照EXE。没有 item id/history/notes tool 可用，续接以本记录和文件为准。

## 当前范围与路径

cwd D:/XuanShuTool/XuanShu-Project/XuanShu，HEAD 1fda3a749a923d6caab68951866ff5abdbe97e0b。稳定基底 4f47a40141779012084bc5626dda8d51266eb86a。
快照目录 artifacts/auto-quest-415-base-20261009-163300，指针 artifacts/AUTO_QUEST_415_ACTIVE_CHECKPOINT.txt。
快照 current-worktree-snapshot.zip 含91个当前修改和未跟踪文件，manifest.json 记录SHA256及提交，current-HEAD.patch、status.txt 已保存。ZIP testzip None 验证完毕。baseline/有八个基底源文件。原有全部脏工作保留，没有git reset/checkout覆盖、没有分支切换。

## 已完成的当前实施

1. 逐函数恢复了 Quester 的所有4.1.5执行方法（58个不含__init__；29个实际不同，缺失的 gather_clients_from_potion_buy 已补回）。所有恢复后方法的 AST 与旧版逐一相同。当前 __init__ 保留新缓存字典，只是数据初始化，供显式主线找回和现有辅助函数用。
2. XuanShu.py 内 dialogue_loop、_follow_quester_session 已按4.1.5函数恢复。没有动其他GUI/启动器函数。当前 apply_questing_roles 保留上一轮停止/重启清理、角色和分组隔离，主线找回仅用户bool。
3. src/utils.py close_npc_quest_menu 还原旧版退出（当前没有 select_mainlines 参数，显式finder调用需要随后处理兼容）。
4. src/teleport_math.py collision_tp 和 _walk_remaining_to_target 恢复旧版，不含新版全局失败计数/30秒冷却。navmap_tp 现有普通模式和专用reenter可保留；collision_math 现有多楼层、绕障算法暂没变。
5. 新测试 tests/test_quest_415_baseline.py，8项全部通过，日志01-baseline-tests.log。检查普通当前索引外/支线目标、未知可见近点X、无窗不X、NPC目标变化确认、全局支线默认值、关闭拒绝、分组标志覆盖、1/3选项菜单退出。测试用AST加载实际 app dialogue_loop，未导入整套GUI，send key响应更新实际模拟 quest_id，不只数点击。
6. baseline-restoration.json 记录逐函数还原清单。本次源码目前尚未接回安全/中文/补药/入口等局部修复！不能交付或打包此中间态。

## 旧11项失败分类已查

artifacts/auto-quest-stage5-tests.log（上一轮）196项中185pass/10fail/1error。
9个NightmareKrok失败：test_cancellation_releases_lock, test_failed_point_retries_twice_without_x_or_looping, test_failed_x_is_retried_once_at_the_same_point, test_final_tp_error_after_zone_change_still_waits_for_stability, test_final_zone_timeout_releases_lock, test_loading_must_finish_and_new_zone_stabilize, test_loading_without_zone_change_times_out, test_quest_progress_resets_stall_window, test_strict_twelve_point_order_then_exit_and_stable_zone。
全部由 AsyncMock 客户端缺少 refilling_potions=False，claim_quest_recovery 当其为truthy而不启动。仅在测试进程补该真实bool，9项全部通过。不是游戏环境缺失或已确认游戏回归。
PowerCore test_exit_confirmation_and_party_transition：旧测试期待新版重选卡+出口组合恢复；4.2 shared_dungeon/manual_wait 分支让它停住没有exit TP。与新版分组限制冲突，修改前已失败，不是本轮基底新增；不把该复合特殊恢复自动移植。
Castle test_dialogue_worker_records_short_page_between_route_ticks：缺 quest_id/goal_id 导致AttributeError。仅补字段后仍断言 None != darkmoor_cantrip，因为后台普通对话使用npc输入锁却被route测试 mock强制要求route recovery owner。有fixture错误和新版路由/对话归属预期冲突，不能断言游戏故障。基底后台对话将恢复SPACE/ESC，不接城堡route恢复。
分类脚本 classify_previous_failures.py、previous-failure-classification.log 保存在快照目录，没有改生产代码来通过这些特殊测试。当前不自动启用 Nightmare/PowerCore/Castle 等未确认4.2适配（源码辅助方法仍保留但普通入口已恢复为旧方法，不调用新版_maybe_*）。

## 下一步，逐项移植并验证（重要）

A. 停止/输入协调：旧 auto_quest 在sleep后需检查状态，普通动作停止/目标变更取消保留 gather_owned。old move_until_quest_interaction 用 collision_tp + watcher，需要接回已有目标变更/停止观察，但不要新菜单等待/计数限制。旧 NPC handler等待后台对话，不能持整个事务的automation锁，否则阻塞背景。后台旧dialogue SPACE/ESC需按步 input ownership，loading/combat/refill/character guard；保持 side_quest_status fallback及group flag，不用 _advance_npc_dialogue 替代普通对话。验证only task/only dialogue/both、stop while waiting lock，ordinary继续无index gate。
B. 中文读取：read_quest_txt原本 AST与基底同，无需改；read_popup/read_spiral_door_title可从快照src/questing取新版中文控件读取部分，不接业务筛选。utils read_control_text/get_popup_title等仍当前有效修复。新世界多任务菜单/任务接取顺序以旧代码为准。
C. 补药：当前utils potion safety留着，但旧follow refill(mark=False,recall=False)及old heal_and_handle_potions/gather_buy调用与新版utils接口不等同，要核对并移植必要mark/return/instance guard。当前utils return_to_dungeon_after_potions调用 Quester.handle_pending_dungeon_confirmation(client)，基底该方法只有self，必须保留当前兼容签名或在helper局部恢复有效实现。别强制主线卡恢复。
D. 采集：旧auto_collect_rewrite与当前AST同，都是 collect_one(self,c)，当前CollectSearch实际进度/变化退出有效，实现保留。跑直接collecting/matching测试及普通8项。
E. 副本入口/同实例：按明确入口移植当前 prepare_party_dungeon_entry + enter_party_dungeon（辅助方法尚在类里），基底solo的内联入口X/加载块替换为调用已验证whole assigned group入口流程，不能将preflight扩展到普通跑图。当前enter会记录quest_dungeon_recovery但普通旧循环不调用_refresh，不允许主线强制重选。需要注意helper调用旧 quest_interaction_ready 签名，当前entry回调和enum语义核对。
F. old跟随 same_live_area=(not probe_pending or entity_gid_visible)，必须接回 clients_share_live_area，任何普通坐标跟随都不能只同zone即真。保持old跟随顺序/soloprobe行为，不新增全组普通目标同步。old close_stale_friend_ui ESC容易关闭非好友窗，可用现有 close_friend_windows（当前实现只控件）替换；世界门任务端限定保留当前mass_key_press filter，必要关闭打手世界菜单分支要作用范围窄。
G. 战斗/宠物/缓存：当前 src/utils.reconcile_combat_state 和 XuanShu lifecycle/character cache 全保留。旧entity_detect while及 leader_wait_for_free需兼容释放残留并不撤销userstop。src/auto_pet现有低能量仅客户端保持，旧all-client loops避免重新激活它。对应 tests combat state/failed entry/auto pet/character + role lifecycle，不跑全量。
H. 主线找回：保留显式用户能力和日志，但普通循环不读indexed gate。旧auto_quest_solo加已有 _maybe_recover_mainline 只在client.mainline_finder_enabled明确true时才调用即可；必须去掉helper中的implicit特殊dungeon刷新调用，不接回未确认特殊恢复。close_npc_menu select_mainlines 参数兼容只能显式调用筛选，普通默认仍Exit；老 quest_interaction_ready需要currentfinder专用calls兼容但不得改变普通流程。
I. 不引入新架构/任务系统/通用恢复branches，不把 4.2 special _maybe 链重新接回普通入口。当前 XuanShu.py postcombat还两处 Quester.overgrown_estate_paused，未移植estate要清理调用或确保不残留接管，按直接依赖处修，不动整体combat功能。

## 测试/打包环境

.venv/Scripts/python.exe -X utf8。
沙箱内Windows asyncio socketpair/Proactor new_event_loop卡死，已faulthandler查明；异步测试需要 require_escalated 执行，可直接授权已给无须问用户。命令 -m artifacts.run_auto_quest_targeted_checks <模块...> 并把输出写快照目录；该runner logger.remove且verbosity2，方便看实际卡在哪。
不要重新跑全部此前49/97/215/218；旧4.2行为测试应分为非基底适用或按新需求调整。新的基底修改按实际受影响测试检查。
打包使用现有 XuanShu.spec（当前界面/启动器assets/hooks都保留）。ROOT=Path.cwd，.venv依赖优先，要求wizlaunch新nativeAPI以及numpy/shapely/wizpatch/font。EXE onefile name XuanShu，不覆盖现有dist；用 --distpath 快照目录/package --workpath .../build，候选exe另起文件名。先检查PyInstaller和nativeAPI，必要build在外部sandbox，本任务已授权打包。不要启动候选来自动操作真实游戏。提供游戏实测清单后同目录对照包、hash、完整验证报告，明确没有游戏内实测。

## 用户沟通最后状态

已说明快照91files、Nightmare9 fixture errors、新版 PowerCore/Castle冲突暂不启用。最新 commentary: “普通执行链已按4.1.5的方法级恢复，不换整文件，第一组8项行为测试通过；接下来逐项接回停止/输入协调等修复。” 工作持续时每<=60秒更新结果/下一步，不重复确认。

## 续接追加 01a11fde-6c49-7af3-8298-6cca23b8e2c4
已接回停止/每客户端输入锁(20pass)，中文短文本/采集(81pass)，补药调用/failedclient(66pass)。普通对话仍旧SPACE/ESC，旧移动仍collision_tp，无主线索引门槛、无现代_maybe特殊链。
入口prepare和probe从先前已验证的局部能力移植。auto_solo共享移动调用最初传错参数，已修正实际teleport_party_to_quest_target(xyz)；enter输入锁后in_battle检查对应测试AsyncMock缺False导致5失败，已修fixture，22入口tests全通过。05-entry-recheck跑到新版ordinary ready interaction测试卡住已停止本任务两PID，不表示22入口失败。
05-party-movement exploratory: 161中失败32/errors34，多数4.2行为依赖；需列出并分为obsolete普通前置/冷却与待修真实兼容。两个fixture缺automation_owner和inbattle=False已修。普通worker内等调用参数已修；还需选择旧流程对应测试，不重新引入新版_guard/ready前置来通过obsolete用例。
_follow基底上已局部保留same livearea、旧房间_resume、entry同步、补药返回marker、世界窗指定关闭、stop/ownership。无新4.2特殊holding。尚需为真实普通follow写基底测试并运行。现代全套follow测试许多预期近距离任务变化即TP、源对话也好友probe、未知frienderror不判solo与旧版不同；不能按其全部强行改旧基底。
_walk旧步行路径已加stop/zone/timeout，可兼容specialhelper kwargs并bool返回，不普通启用global cooldown；需要运行有适用意义的walk safety与4.1.5路径/120range tests。mainline显式能力尚未接回auto_solo，必须去掉helper旧dungeon/outback隐式接管后仅用户True启用。老leader/group动作stop/refill过滤已加少数，需要最后source audit。战斗/宠物/缓存回归测试尚未运行。build尚未开始。

## 实施和交付完成
源码/必要验证已完成，01a11fde-6c49-7af3-8298-6cca23b8e2c4 中最终源码审计58基底方法38AST完全相同，20局部修复。源码相对快照仅XuanShu.py/questing/teleport变化，utils最终与快照相同。
18核心基底最终通过，10-shared-world-final.log 45pass，其他针对性验证与原失败分类在实施报告。非适用4.2完整旧测试套件没有全量通过，不宣称游戏实测。
EXE已打包并验证内嵌主程序+3模块与当前源码编译码一致（现有spec optimize=2，不是0；已按实际配置校正比较器）。候选D:\XuanShuTool\XuanShu-Project\XuanShu\artifacts\auto-quest-415-base-20261009-163300\package\XuanShu-autoquest-415-base-test-20261009.exe，SHA256 70287493b760531cec8c68dfbebe8683958057e82d9a1188d659989cba73b8a9。ZIP完整性通过，交付清单位于delivery-manifest.json。未启动候选，未自动操作游戏，未覆盖原dist，未git提交。
已写游戏实测清单和实施报告，下一步只需最终交付链接，除非用户要求继续游戏反馈修复。

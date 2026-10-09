# 玄枢：4.1.5 自动任务基底实施与验证记录

状态：源码已完成，针对性验证与独立 EXE 打包完成。尚未启动候选控制游戏，没有游戏内验收。优先对照普通跑图、NPC 对话、多任务菜单和打手跟随。

## 基底、当前提交和可恢复快照

- 稳定基底：4f47a40141779012084bc5626dda8d51266eb86a（v4.1.5）。
- 当前 HEAD：1fda3a749a923d6caab68951866ff5abdbe97e0b；没有切换分支、提交或撤销整个工作区。
- 修改前快照：`auto-quest-415-base-20261009-163300/current-worktree-snapshot.zip`，91 个当时修改/未跟踪文件；`manifest.json` 有 SHA256，`current-HEAD.patch` 和 `status.txt` 保留。ZIP 完整性检查通过。
- 保留了此前局部修改及原有无关工作；以函数为单位恢复自动任务基底，再加入必要的兼容修复。没有用旧版整文件替换当前界面或启动器。
- 4.1.5 Quester 58 个执行方法作为恢复依据。最终 38 个方法仍与旧版 AST 完全相同；20 个方法仅有本文列出的文本、停止、输入、补药、实例或接口兼容修复。逐方法清单位于 `candidate-source-audit.json`。

## 恢复的普通规则

1. 普通自动任务按游戏当前追踪目标，主线索引只记录日志。取消多任务端隐式开启找回；明确开启找回后才进入专用流程，关闭后释放找回保护状态。
2. 恢复 4.1.5 的 NPC 对话、菜单和接取规则：空格推进；有拒绝按钮且“接受支线”关闭时 ESC；菜单默认点击自己的退出按钮。没有加入新的选择优先级、普通菜单失败持有状态或主线同步门槛。显式找回的专用筛选仍保留。
3. 复用同一个旧对话循环支持“仅自动任务”；自动对话也开启时由现有对话线程处理页面。全局支线开关作为默认值，分组客户端显式标志可覆盖；分组退出原来缺少标志时删除属性，默认值能够再次生效。
4. 普通移动使用 4.1.5 的 collision_tp 与最后步行顺序，普通范围恢复到 120。撤回新版普通移动失败计数/30 秒冷却入口；保留旧碰撞重解、绕障和可达路径，不引入新的通用路线恢复。
5. 普通跑图由任务端推进，打手按旧版 900 距离、本地跟随与稳定后好友传送顺序跟随。打手暂时加载、补药或停止不占用普通任务端迭代。真实副本入口和确认的共享房间保留全组同步。
6. 恢复旧版任务点附近可见窗口的 X 规则，未知提示也可在当前任务点、空闲、未停止且任务/目标未变时单次尝试。没有窗口不按 X，目标变化后不使用旧落点交互。
7. 不重新接入 4.2 的 `_maybe_*` 复合特殊路线链。地牢停滞不会通过普通迭代或显式 Finder 旁路强制重选主线。

## 保留并核对调用兼容的修复

- 停止/重启、子任务取消与排空、分组运行隔离；等待输入锁后重新检查状态，避免停止后继续移动/输入。
- 输入锁按每次移动或对话页面使用，NPC 等待不占住后台对话所需的锁；加载、战斗、补药和角色切换期间避免误输入。
- 中文 ControlText/ControlList 短文本读取；保留控件可见性和客户端范围。没有顺带移植未确认的特殊传送门业务选择。
- NPC 不只看点击：保留旧文本/目标变化确认，并识别实际 Quest/Goal ID 变化，兼容文字和坐标没有变化的连续接取。
- 补药标记、地牢返回按钮、任务/区域 ID 与同实例校验；旧整组补药顺序保留，失败只停止相应客户端并移出本地动作列表；现有全局名单不被重写。
- 同名区域不作为同实例证明。本地坐标跟随要求 live-area 证据；确认副本不会因好友忙碌错误改判为单人区。入口倒计时保留锁定，成员未齐不会单独进入。
- 旧房间补跟只使用保留目标和有效源实例令牌。实际 movement watcher 允许任务端已换房的慢打手继续通过原入口；令牌变了就退出，不使用新房间坐标。
- 世界门交互/世界选择任务端限定，包括旧队长调用路径；已有打手世界列表只做受控关闭。普通手动前台 X 和普通打手交互保留。
- 现有战斗残留/失败入战恢复、战后交接、采集实际进度与目标变化退出、宠物低能量客户端范围、换角色缓存刷新和用户停止状态均保留。
- 停止/重启清理 Outback、城堡/庄园、Lemuria 等旧持有状态；新版辅助方法源码保留，普通入口不启用未确认适配。

## 修改范围

相对于本次实施前快照，生产源码最终只变化：`XuanShu.py`、`src/questing.py`、`src/teleport_math.py`。`src/utils.py` 最终保持快照里的中文/补药/菜单退出与显式找回接口版本；其普通默认仍是旧菜单退出规则。

`src/collision_math.py`、`src/collecting.py`、`src/script_popups.py`、`src/mainline_progress.py` 与实施前内容相同。界面、启动器、Rust native 源码和其他模块保持原有工作；完整差异与源文件哈希保存在快照目录。新增核心测试 `tests/test_quest_415_baseline.py`，按实际范围调整了直接关联的补药、主线、入口、跟随、碰撞步行和战斗 AST 测试。

## 那 11 项旧失败，逐项分类

原阶段 5：196 项中 185 通过、10 个失败、1 个错误；不能用该结果证明全部特殊适配有效。下表是原先那 11 项，不把本次探索性兼容检查混入这个数字。

| # | 场景 | 原测试名称 | 已确认原因 |
| --- | --- | --- | --- |
| 1 | NightmareKrok | `test_cancellation_releases_lock` | 测试替身缺 refilling_potions=False；被当作正在补药，无法进入恢复。仅补真实布尔字段后通过。 |
| 2 | NightmareKrok | `test_failed_point_retries_twice_without_x_or_looping` | 测试替身缺 refilling_potions=False；被当作正在补药，无法进入恢复。仅补真实布尔字段后通过。 |
| 3 | NightmareKrok | `test_failed_x_is_retried_once_at_the_same_point` | 测试替身缺 refilling_potions=False；被当作正在补药，无法进入恢复。仅补真实布尔字段后通过。 |
| 4 | NightmareKrok | `test_final_tp_error_after_zone_change_still_waits_for_stability` | 测试替身缺 refilling_potions=False；被当作正在补药，无法进入恢复。仅补真实布尔字段后通过。 |
| 5 | NightmareKrok | `test_final_zone_timeout_releases_lock` | 测试替身缺 refilling_potions=False；被当作正在补药，无法进入恢复。仅补真实布尔字段后通过。 |
| 6 | NightmareKrok | `test_loading_must_finish_and_new_zone_stabilize` | 测试替身缺 refilling_potions=False；被当作正在补药，无法进入恢复。仅补真实布尔字段后通过。 |
| 7 | NightmareKrok | `test_loading_without_zone_change_times_out` | 测试替身缺 refilling_potions=False；被当作正在补药，无法进入恢复。仅补真实布尔字段后通过。 |
| 8 | NightmareKrok | `test_quest_progress_resets_stall_window` | 测试替身缺 refilling_potions=False；被当作正在补药，无法进入恢复。仅补真实布尔字段后通过。 |
| 9 | NightmareKrok | `test_strict_twelve_point_order_then_exit_and_stable_zone` | 测试替身缺 refilling_potions=False；被当作正在补药，无法进入恢复。仅补真实布尔字段后通过。 |
| 10 | PowerCore | `test_exit_confirmation_and_party_transition` | 旧测试要求新版主线重选+出口组合；既有 shared_dungeon/manual_wait 调用路径先等待，未执行出口 TP。修改前也失败。 |
| 11 | Castle | `test_dialogue_worker_records_short_page_between_route_ticks` | 缺 quest_id/goal_id 先触发 AttributeError；补字段后仍因测试要求 darkmoor_cantrip 路由归属、实际后台普通对话使用 NPC 输入归属而失败。 |

分类结论：9 项属于离线测试替身问题，不是缺游戏环境导致；PowerCore 是修改前已有的调用阻塞与新版组合预期冲突，Castle 同时有替身缺字段和新版路由归属预期冲突。没有证据把这些全部认定为本次新增回归或已确认游戏故障。Nightmare 修正替身后 9 项通过也不等于游戏适配有效，因此这三组新版复合恢复仍未移植到普通执行入口。

分类证据：`classify_previous_failures.py`、`previous-failure-classification.log`；原记录 `auto-quest-stage5-tests.log` 和 `auto-quest-special-initial-comparison.log` 保留。

## 本次验证及失败处理

- 停止与输入协调：20 项通过（02 日志）。
- 中文读取、现有采集/匹配和普通基底：81 项通过（03 日志）。
- 补药与实例回程：66 项通过（04 recheck 日志）。
- 入口、倒计时、同实例和普通区域探测：22 项通过（05 entry recheck 日志中完整入口组；之后不适用的新版普通前置测试已停止）。
- 普通三种对话组合、显式找回、停止和战斗状态：73 项通过（06 recheck）；宠物低能量、角色缓存和失败入战检查在 06 初次检查通过。
- 跟随与步行选定 37 项：29 项首轮通过，8 项校正后分别通过 07 recheck / 08 日志。校正涉及旧几何接口替身、状态提示断言、旧重试节奏和将全组探测测试限定到真实共享房间。
- 最终真实源房间令牌/NPC ID 变化、停止和补药调用：26 项通过（09 日志）。
- 最终共享移动/世界门/核心基底：45 项通过（10 日志），其中核心基底 18 项。
- 这些分组互有重叠，不相加当作独立测试总数。语法检查与四个相关源文件 diff whitespace 检查通过。

中间探索性 `05-party-movement-tests.log` 曾运行 161 项，出现 32 个失败、34 个错误。已处理真实调用兼容问题：共享移动误传两个参数、入口新增战斗检查的替身缺 False、AST 测试缺输入锁/对话 worker、旧步行替身缺 to_hex/walk_z。原日志保留，不用删日志掩盖。

其余原测试中，要求 4.2 普通交互前置顺序、持有菜单/特殊恢复状态、近距离因任务变化立即坐标同步、对话中好友探测、普通碰撞全局冷却和精确走到 5 单位等预期，不作为旧基底候选的普通规则。直接受影响的验收测试按新要求调整/选定，没有为通过这些测试把撤回限制重新接回。旧完整测试套件尚未全面改写和全部通过，也未声称全量验证完成。

Windows 沙箱中的异步 socketpair/Proactor 事件循环会卡住；局部异步测试使用受授权的普通本地环境执行。没有修改游戏或系统设置来验证。

## 对照包

文件：`XuanShu-autoquest-415-base-test-20261009.exe`，72,001,527 字节。

SHA256：`70287493b760531cec8c68dfbebe8683958057e82d9a1188d659989cba73b8a9`。

沿用现有 `XuanShu.spec`，输出和临时构建在独立目录，原 dist 未覆盖；PyInstaller 6.20.0、当前 venv wizlaunch 0.3.1 必要 API 已确认。中文字体、wizpatch helper、native wizlaunch 及 numpy/shapely 打包资源已核对。

已读取 EXE 内嵌归档，主程序、questing、utils、teleport_math 与当前源码按现有 optimize=2 编译后的字节码、常量、名字、行表及异常表一致；仅排除编译路径文件名。不是只根据构建成功推定源码正确。证据 `packed-code-verification.json`。构建日志 `11-build.log` 保留；有 pycparser 可选生成表 hidden-import 警告，未进行候选启动验证。

压缩包同时包含游戏实测清单、本报告、SHA256 和源码/内嵌代码验证记录。候选当前界面与软件版本标识保持当前软件值，仅自动任务基底变化。

## 未实测场景和剩余等待边界

没有游戏内实测普通连续跑图、真实 NPC 接取、多任务端、掉队/补药/断开、单人区、门口切区、多楼层、实际副本进入和停止输入时序。源代码、单元测试和构建不替代这些场景。

真实入口/共享房间组员未齐、同实例证据缺失、补药后无法确认原副本、游戏控件/目标不可读，仍会等待或停止相应客户端；可按提示手动归队/修复追踪后重开。旧队长模式仍有 4.1.5 原有队伍等待、动态队长和好友忙碌/单人区域重试规则。本候选的已恢复保证不包括未经确认的 4.2 专用复合路线；这类阶段暂由玩家手动完成。

下一步按 `AUTO_QUEST_415_BASE_GAME_CHECKLIST_20261009.md` 做同场景对照。优先记录第一个不推进的客户端/动作和全组任务、目标、区域、入口与实例证据，再修具体问题。

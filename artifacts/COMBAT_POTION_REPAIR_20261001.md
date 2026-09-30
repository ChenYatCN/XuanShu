# 补药返回、战斗对象与近距离任务点修复

## 当前结果

源码修复、相关自动测试和独立打包已完成；单客户端、多客户端游戏实测尚未完成。没有替换正在运行的旧版 XuanShu，也没有接管或移动游戏客户端。

测试成品：`outputs/combat-potion-check-20261001/XuanShu.exe`

SHA256：`6F0819967E2A26B0C0DAE1BC718EE429DFD2C0647D2E4986EE947D1F5E54497E`

## 根本原因与修复

### 战斗对象失效

日志中的异常发生在读取战斗成员 owner_id 时。wizwalker 的 CombatMember.get_participant 已经会重新读取 participant，并在对象不再存在时抛出 MemoryInvalidated；问题不是它把 None 当作有效成员，而是上层异步流程保留了失效的成员控件、没有在当前回合内处理该异常。原外层异常处理会退出当前战斗控制器并重新开始处理。

现在在当前控制器内最多尝试三次读取，重新读取成员并清空卡牌控件缓存。未发出战斗操作的重试会恢复相对回合计数等状态。已开始施法、附魔、弃牌或过牌时不重放本回合，而是交给原有的下一回合等待流程。目标补救点击前会按原 owner_id 重新读取成员，目标消失不会替换成其他成员。

### 补药返回后的打手副本同步

日志中后续的坐标同步来自手动 XYZ 同步热键，不是自动跟随成功确认了副本。旧确认只检查打手侧的实体列表，补药返回后确认超时还会将打手 questing_status 置为 False，导致其不再自动归队。

现在检查双方实体列表，忽略已失效的单个实体。地牢补药出发前记录已经直接证明同副本的队友；红色返回按钮回传经过原有任务、区域、Loading 等验证后，结合未离开队友的 ClientZone 对象连续性、Zone ID 和观察到的区域变化代次恢复证明。队友加载、切图、区域对象替换，或返回者再次离开，都会使这项保留证据失效。

没有把 Zone 名称或 Zone ID 单独当作副本实例唯一标识。没有补药前证明、又无法读取实时实体时，仍然保留安全等待，不能保证自动归队。

自动战斗坐标同步和手动 XYZ 同步都在移动前检查同副本证据；读取坐标后再次检查 Loading 和区域。补药返回确认超时不再永久停止打手，保持检测并给出明确状态，不用好友 TP 或坐标 TP 绕过证明。角色分配记录打手对应的任务客户端，兼容打手独立补药。

### NumPy 与近距离无进展

系统 Python 的 NumPy 不会自动进入 `.venv` 打包环境；排查时该环境缺少 NumPy 和 Shapely。已在实际打包环境安装 NumPy 2.5.3、Shapely 2.1.2，版本满足现有项目约束。打包现在要求它们来自 `.venv` 且可导入，显式纳入依赖，并在成品检查中验证基础模块及原生扩展。

日志中的近距离任务点距离约 3 单位，collision_tp 在距离不超过 5 单位时本来就直接返回；反复调用并不意味着任务交互已推进。原重新进场恢复只覆盖 Talk/Use，且两次恢复耗尽后返回 False，使调用方重新进入普通 TP 循环。

现在复用原有两次步行重新进场恢复，并通过既有语言表补充“Go To／前往”目标。两次仍无进展时停止该任务点普通 TP、输出一次警告，持续读取任务和交互；实际任务、区域或目标变化后重新判定。没有增加传送次数，也没有改动战斗/收集目标的规则。日志未提供当时完整目标文本，不能声称已实机确认该任务具体属于哪种目标类型。

## 本次文件

源码/打包：

- `src/combat_targeting.py`
- `XuanShu.py`
- `src/utils.py`
- `src/questing.py`
- `src/interaction_prompts.py`
- `XuanShu.spec`
- `packaging/verify_bundle.py`

测试：

- 新增 `tests/test_party_area_proof.py`
- 修改 `tests/test_combat_hitter_wait.py`
- 修改 `tests/test_quest_trigger_reentry.py`
- 修改 `tests/test_quest_party_solo_follow.py`
- 修改 `tests/test_quest_task_lifecycle.py`

保留此前补药、聊天翻译、特殊地图恢复等已有修改。测试中补齐模拟客户端的 refilling_potions=False，以及独立 AST 测试所需的 mainline_finder_enabled 默认值，未因此修改业务规则。

## 验证结果

- 111 项相关自动测试通过（91 项战斗/任务交互/补药/单人区域测试，18 项区域证明与生命周期测试，另两项新增超时及失效实体树测试）。
- 实际 `.venv` 的 NumPy、Shapely 导入及几何计算通过，collision_tp 所在模块可导入。
- 独立 PyInstaller 打包完成，成品依赖与来源检查通过；未覆盖原 dist 或运行中的 EXE。
- 成品内 XuanShu.py、utils、combat_targeting、questing、interaction_prompts 的编译代码与当前源码一致。
- 针对本次文件的差异格式检查通过。
- 未验证新 EXE 启动后的行为，未进行单客户端和多客户端游戏实测。当前发现两个游戏客户端和正在运行的旧 XuanShu；未停止它们或注入额外测试钩子。

## 仍需游戏验收

使用独立测试成品，复测任务客户端补药返回后进入战斗、打手原地等待后的归队，以及打手独立补药。检查加载/成员变化时无重复施法；错误副本的手动和自动 XYZ 同步均被拒绝；近距离 Go To/Talk/Use 目标经过有限重新进场后推进，或安全停止重复 TP。

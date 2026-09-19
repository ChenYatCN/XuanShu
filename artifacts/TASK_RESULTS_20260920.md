# 本次任务修改结果（2026-09-20）

## 交付范围

源码修改及针对性测试已完成；按最后要求**不打包**，未覆盖现有 EXE，未修改游戏资源。以下“已实现”指源码，不代表已完成游戏内验收。工作区原有改动保留，未提交 Git。

## 逐项结果

| 项目 | 实现与主要文件（相对项目根目录） | 验证及限制 |
| --- | --- | --- |
| 1. 世界内主线编号 | `src/mainline_progress.py`、`src/questing.py`；读取真实任务 ID、主线标记、标题语言键，依次按 ID／语言键／标题及别名匹配，按任务 ID 去重；输出世界内序号／总数；不拿目标描述猜任务。 | `test_mainline_progress` 通过。使用已有 `src/data/mainline_quests.json`，未找到并重读原始 Excel。现有数据不具备完整 ID、语言键和中文映射；歧义或无匹配安全显示未匹配，完整中文覆盖尚未验证。 |
| 2. 三处卡点脱困及教学跳过 | `src/questing.py`、`src/script_popups.py`；仅指定地图且至少 3 次、至少 10 秒无进展时启用兜底；蟹王国等待交互提示后按 X；魔法轮教学限定地图、标题、按钮路径及确认时间窗口。 | `test_special_quest_exits`、`test_magic_wheel`、弹窗测试通过。读取游戏 `Root.wad` 核对教学窗口结构；未实测三个地图。 |
| 3. 连续采集 | `src/collecting.py`；不再固定回守首次锚点，优先扫描当前附近；交互前等待提示并重新读取实体位置、检查任务状态。 | `test_collecting` 通过；实际采集刷新、网络延迟需游戏内验收。 |
| 4. 战后移动 | `XuanShu.py`；自动任务客户端战后短按 S 后退，其他模式保留 A／D；加载或仍在战斗时不盲目移动。 | `test_scoped_runtime` 验证自动任务／非任务分支及战斗中不移动。实现的是“后退”方案，没有新增恢复球方向导航。 |
| 5. 生产线本轮与历史 | `src/ibao_runtime.py`、`src/gui/ibao_dialog.py`；清本轮将旧行归档，重置本轮次数、时间、显示重启次数，保留历史记录及连续重启安全计数。 | `test_ibao`、`test_ibao_ui` 通过。 |
| 6. 生产线停止及窗口状态 | 同上；取消勾选停止对应客户端；断开的进程终止工作，不作为可恢复挂钩错误无限重启；刷新列表恢复运行组勾选且不触发误停止。 | 对应运行时／界面测试通过；真实关闭游戏进程时序待验收。 |
| 7. 输入占用 | `src/ibao_runtime.py`、`src/ibao_core.py`；去掉生产线全会话鼠标上下文，按单次点击获取；长按切换拆为短按与等待，降低对话按键频率。钓鱼保留独立工作流程。 | 模拟鼠标上下文和钓鱼相关测试通过；不据此保证 Windows 前台所有输入冲突已消除，需手动操作时实测。 |
| 8. 生产线独立最小化 | `src/gui/ibao_dialog.py`；无父窗口的独立顶层窗口及最小化按钮，关闭窗口仍保留后台运行语义。 | 界面对象测试通过；主窗口最小化和任务栏行为未在真实桌面验收。 |
| 9. 九类快捷键分组 | `src/hotkey_groups.py`、`src/gui/tab_hotkeys.py`、`src/gui/commands.py`、`XuanShu.py`；命名组配置、每行目标选择、同功能不重叠组独立启停、掉线清理、停止等待子任务退出；原有目标路径保留；任务角色仅修改选中成员。 | `test_hotkey_groups`、`test_hotkey_group_ui`、`test_quest_task_lifecycle`、`test_scoped_runtime` 通过。九类为加速、战斗、对话、支线接受、传送阵、任务、宠物、药水、自由视角；真实多客户端并行需实测。 |
| 10. 脚本坐骑停止 | `src/mount_stop.py`、`src/gui/tab_actions.py`、`src/deimoslang/{tokenizer,types,parser,ir,vm}.py`、`XuanShu.py`；可选坐骑监测，识别背包／装备及系统坐骑掉落，命中后正常结束脚本组并清理；新增 `stopifmount` 指令。 | `test_mount_stop` 的解析、选择客户端、已有坐骑提前停止和正常停止测试通过；未实测真实坐骑对象读取。 |
| 11. 钓鱼坐骑停止 | `src/gui/tab_fishing.py`、`src/fishing_groups.py`、`src/auto_fish_original_adapter.py`；独立配置，按客户端监测，在后续能量动作前及收鱼阶段检查并正常结束。 | `test_mount_stop`、`test_fishing_energy`、`test_fishing_groups`、`test_fishing_group_ui` 通过；掉落出现到游戏状态可读取仍有时序限制，需实测。 |
| 12. 开始按钮 | `src/gui/tab_actions.py`、`src/gui/tab_fishing.py`；脚本和钓鱼开始按钮点击区域 44×44，图标 24×24；不改其他按钮布局。 | 相关界面测试通过；实际缩放、显示器观感待验收。 |
| 13. 漂浮大陆及半径回归 | `src/questing.py`、`src/collecting.py`；保留漂浮大陆至少 3 次且至少 10 秒的门槛和既有范围相关行为。 | `test_floating_land_exit`、`test_crystal_exit`、`test_collecting` 通过；不是游戏内成功脱困证明。 |

### 三处兜底坐标

- `Krokotopia/KI_Selenopolis/Interiors/KI_Z04101_BlendedGrove`：`(3124.726, -4718.231, 36.200)`。
- `Celestia/CL_Z09_Science_Center`：`(-1067.512, 343.759, -449.800)`。
- `Celestia/Interiors/CL_Z10i3_Kingdom_Of_The_Crabs`：`(3184.987, -9931.280, -1014.007)`。

## 使用方式

### 快捷键分组

在快捷键页编辑命名组，每行一个，例如：

```text
组1=p1,p2
组2=p3,p4
```

将对应功能的目标选为命名组，再使用该功能按钮或快捷键。相同组再次触发即停止；不同且不重叠的组可独立运行。目标客户端缺失不回退为全部客户端。选择“原有目标”沿用原来的操作范围。原有全局模式与同功能分组模式不能混用，须先停止原模式。

### 坐骑停止

脚本、钓鱼各自有独立开关与名称输入。填写名称时按完整名称、语言键或内部名精确匹配（忽略大小写），已有目标坐骑也会触发停止；留空时只监测本轮新增坐骑。读取背包和已装备物品，不读取银行或共享银行。

脚本语法示例：

```text
p1 stopifmount "骨龙(永久)"
```

该指令检查选定客户端，命中时正常停止脚本；没有命中则继续。界面勾选则在脚本执行期间持续监测。

## 验证记录

- 第一组针对性测试：75 项通过，覆盖生产线、分组、脱困、任务生命周期、坐骑、主线、采集、钓鱼和相关界面。
- 补充测试：`test_scoped_runtime`、`test_script_popups`、`test_pet_level_popup` 共 22 项通过。
- 合计 97 项通过；此前修改模块的编译检查通过。未运行全项目全量测试。
- `XuanShu.spec` 增加主线 JSON 资源声明，`packaging/verify_bundle.py` 增加对应检查要求。按用户最后要求未打包，因此**没有新 EXE，也没有本次二进制资源验证结果**。

## 尚未完成的验收

1. 原始 Excel 与现有任务索引逐条对照、完整 ID／中英文映射覆盖。
2. 三处地图及漂浮大陆实际脱困、教学确认、持续采集及战后后退效果。
3. 真实多客户端下九类分组隔离、输入占用、关闭客户端及桌面最小化行为。
4. 真实坐骑物品／掉落识别与停止时序，尤其钓鱼后续能量消耗。

这些属于数据完整性或真实游戏／桌面验收边界，不以模拟测试通过替代。

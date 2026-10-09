# 任务点附近尝试 X — 已按后续请求撤回

后续用户报告远处/击败任务也反复 X，并明确要求恢复原版传送与交互。附近盲按 X、对应入口参数和测试已撤除，下面内容仅为历史记录，不能当作当前实现。当前交付记录见 ORIGINAL_TP_COLLECT_DIALOGUE_20261008.md。

当前窗口：01a11b3d-a663-7980-9712-e14136cb0966。最新用户确认已切换 6.1 sol high，并补充两组入口/攀爬截图。原请求 01a11b21-d33c-7093-9ca0-64a51a1b150c：干脆弄成盲目 X；授权继续 01a11b26-cc83-7021-bafd-14192a64c76f；反馈 01a11b3d-a689-7f03-9252-3fdf64063b4f：现在都不按 X。具体目标不变：任务点附近不依赖提示文字或名称尝试 X，本组一起按，保留间隔、加载/战斗/暂停/占用检查和已有停滞恢复。没有授权打包或实时游戏操作。

发现：先前仅发出实施说明，代码未加入附近 X；原实现仍要求提示/标题匹配，组队机制要求两端提示完全一致。新日志 p3 距 HUD 目标约 178u 时反复 TP，无按键；截图 p1/p2 在洞穴入口，p3/p4 在绳索处均已出现交互。

完成：src/questing.py 新增 try_nearby_quest_x，在 auto_quest_solo 移动前和移动后接入。非零、有限 HUD 坐标，任务端距当前目标 <750u，任务 ID/Goal ID/区域/目标新鲜度检查。本组成员同区域、实际同实例、附近、空闲才并发 X；1.5 秒间隔存在真实客户端上，跨 worker 重建保留。操作占用直接等待；加载/战斗/补药/战后恢复/救援/停止不按键。NPC 仍用原事务且只操作任务端，识别出的拍照任务保留相机流程。可见 Team Up 才走现有地牢进入和区域确认；enter_party_dungeon 的 nearby_xyz 分支不读取/匹配标题与提示，旧调用的行为保留。继续使用既有停滞重选和人工等待流程。

修改文件：src/questing.py、tests/test_nearby_quest_x.py、tests/test_quest_party_shared_target.py、tests/test_party_dungeon_interaction.py。保留此前全部脏工作树修改。

验证完成：输出 db24e5，5 个直接相关离线模块共 119 项通过（3.751 秒）：test_nearby_quest_x、test_quest_interaction_priority、test_quest_party_dungeon、test_party_dungeon_interaction、test_quest_party_shared_target。涵盖空/未知提示、178u 距离、绳索、同组并发/另一组隔离、名称不可读的真实 Team Up 分支、按键间隔、目标改变、读取间加载、操作占用、取消释放、NPC/区域同步回归。首次联合测试发现 NPC 分支回归和旧单端断言，已修正并通过最终联合运行。沙盒异步测试无输出，已通过 Ctrl-C 终止，仅离线测试在沙盒外完成。

交付范围：源码修改及离线验证完成；未打包/安装/更新 EXE、未操控实时游戏。运行新版本后应出现“在当前任务点附近尝试 X：p3, p4”日志。最后一步：diff 检查后交付，无剩余实现步骤。

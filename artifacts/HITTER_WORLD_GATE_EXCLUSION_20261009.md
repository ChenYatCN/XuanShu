# 打手不交互世界门 — 完成

当前窗口：01a11b3d-a663-7980-9712-e14136cb0966。本轮用户消息 item ID 未提供。用户明确纠正：希望打手不要交互世界门，打开选世界界面会卡住。截图 p4 站在“世界之门”，提示按 X 或鼠标互动，作为打手跟随任务端。普通组队机关、任务端世界选择、玩家主动在前台按 X 不应被一并禁用。保留既有脏工作树及此前恢复普通 TP、食物采集、NPC 加速等修改。未授权打包或实时游戏输入。

确认入口：src/questing.py 的组队交互把世界门当普通非 NPC 机关，可能给整组发 X；旧跟随恢复会重放缓存 X；legacy 普通交互也广播 X，世界选择会给非任务端选择目的地。XuanShu.py 的手动 X 联动通过 mass_key_press 广播后台按键，不是自动任务唯一来源。

完成：
- handle_party_dungeon_interaction 以已有 portal_kind 识别世界门，返回普通任务端交互；不等待/操作打手。旧世界门交互恢复缓存清除且不重放打手 X。
- legacy 普通交互世界门只给任务端 X；标准世界选择仅调用任务端 spiral_door_with_quest，不给打手选择世界。特殊电梯/独立传送门既有行为保留。
- mass_key_press 的后台 X 过滤已分配给任务端的打手：世界门提示或选世界界面可见时不发 X；无法读取状态时跳过该打手，不影响其余客户端。其他按键、普通机关及明确的前台手动 X 保留。
- _follow_quester_session 对打手已有世界选择界面，复用 recovery 占用及现有 cancelButton 路径关闭，兼容完整/省略 WorldView 根的布局；加载、战斗、补药、已有恢复/同步占用时不操作。关闭后下一轮恢复原跟随，不操作任务端窗口。

修改：XuanShu.py、src/questing.py、tests/test_party_dungeon_interaction.py、tests/test_quest_party_shared_target.py、新增 tests/test_world_gate_background_x.py。tests/test_x_press_hotkey.py 临时放置新测试时插入在旧方法中，测试暴露该位置错误；已移到独立测试模块并恢复原文件，最终该旧文件没有内容变更。

验证完成：最终 exec 输出 09815e，6 个相关模块共 128 项全部通过（13.415 秒）：test_party_dungeon_interaction、test_quest_party_shared_target、test_quest_interaction_priority、test_world_gate_background_x、test_x_press_hotkey、test_hotkey_runtime_scope。包括中英文世界门只操作任务端、旧缓存不发打手 X、打手窗口关闭和占用/加载/战斗保护、后台 X 过滤、普通机关/其他客户端/其他按键/前台手动输入保持。前一轮 106 项通过后发现后台 X 路径并补充测试，不能相加。最终语法检查通过；git diff --check 无错误，仅 CRLF 转换提示。

最后停点：实现和必要离线验证完成，待交付。源码修改；未打包、安装、更新 EXE 或操控实时游戏。需更新后重新启动才生效，不能声称实际游戏内世界门已验证。

# 使用宠物模式寻找大黄蜂：实现与验证

## 续接更新：用户已授权完成后取消宠物模式

用户明确补充：到达指定点会完成该任务，随后需要再次点击取消扮演宠物再继续TP。已补充 _finish_bumbles_pet：读取到任务阶段实际变化且确认当前是宠物时，复用同一按钮执行一次取消；菜单最多额外打开一次，取消不重复点击（状态保留跨任务线程重建）。等待启动提示恢复并稳定1秒，才清除等待标记、重新进入原主线同步与普通任务TP。退出失败/忙碌/状态未知继续暂停，不盲目切换。若TP后任务已经推进且已为巫师，不再多余开启宠物模式。该更新替代下文“策略待选择”的旧记录。

本轮19项专项测试整体通过；追加TP立即推进测试修正测试段落放置后，该项和忙碌取消测试单独通过，共20项专项测试已通过。内存编译与定向diff检查通过；仍未打包或实机测试。

## 识别

仅处理 Lemuria/LM_Z07_Heap 内的当前主线。复用 _mainline_identity 和 _dungeon_quest_snapshot，从当前 Quest ID 查询对应 QuestData，再读当前 Goal ID 的 GoalData；不是对所有任务做目标文字模糊匹配。

- 主线标题：Nose for Clues / 嗅探线索，现有主线索引 Lemuria 第83项。
- 主线 Language Key：QuestTitle_17442D。
- 指定阶段 Language Key：WizQst17442D_00000005。
- 阶段文本精确匹配：使用宠物模式寻找大黄蜂 / Pet Mode to Find Bumbles；有地点文字时只允许废料堆 / Heap。
- 不同已读 Quest Key 或 Goal Key直接拒绝。Key缺失时只允许已匹配主线索引/标题及准确阶段文字的有限回退，目标 Goal 必须存在，并复查实时 ID一致。
- 实际数值 Quest ID / Goal ID：没有实机读取，因此未提供或硬编码数字。运行时读取并输出 Debug日志。绝不把17442D的语言表后缀当作数值 Quest ID。

英文阶段及宠物提示键来自本机 Root.wad 语言资料（artifacts/collect-catalog/root-language-records.json），主线标题来自现有 mainline_quests.json。图片作为参考，不是新实现的实机验证。

## 操作与状态确认

专用流程按当前执行任务的客户端操作，未写死p1。只触发一次指定 TP：XYZ(14017.3837890625,-563.506591796875,-2051.80712890625)。验证距目标小于100且移动小于2，连续稳定1秒，最多等待10秒。不到位不点按钮。

宠物菜单路径：['WorldView','windowHUD','PetSystemButton']。
扮演宠物按钮路径：['WorldView','windowHUD','PetSystemButton','PetButtonLayout','PlayAsPetButton']。

Root.wad 的 GUI/HUDWindow.gui 中已核对这些控件。已有宠物模块没有通用的菜单打开函数，因此复用 get_window_from_path / click_window_by_path；按钮已可见时不重复切换菜单。点击前检查存在、可见、is_control_grayed，并重新检查模式，避免第二次点击变成取消宠物模式。

模式判定使用 PlayAsPetButton.tip()：取消键 GUI2_00001155 / Cancel Play as Pet 表示宠物模式；启动键 GUI2_00001154（免费）或 GUI2_00001181（消耗快乐值）表示巫师模式。支持未解析字符串引用、英文及客户端语言缓存解析的本地化提示。未知/缺控件是未知，不算成功。点击后要求取消状态稳定1秒；仅按钮点击返回不算成功。

最多2次切换点击，每次等待5秒；整段最多30秒。一个阶段只做一次整段流程，状态保存在客户端，任务线程重建不重跑。失败 WARNING仅一次。已在宠物模式时不TP、不点击，只重新读任务进度。

## 兼容与仍待决定的后续策略

现有 Wizwalker Client.teleport 依赖 MovementTeleportHook，而 CurrentActorBody / client_object 的控制对象在宠物模式下是否可用于普通任务TP，没有现成保证或实机证据。脚本解释器甚至单独提醒宠物模式客户端会被作为实体处理。不能由按键或宠物形象推断普通TP兼容。

因此当前实现确认切换后继续读取任务状态，但同阶段不执行额外TP。当阶段推进到“收集钥匙”等下一阶段而宠物模式仍存在/状态未知时，保持宠物模式，暂停未确认兼容的普通TP，并只提示一次。确认已经恢复巫师模式且任务/区域确有变化后，解除暂停、重置主线同步状态并交还普通任务流程。

已向用户询问：保持宠物模式并暂停未验证TP，还是允许阶段完成后自动退出宠物模式再继续。尚无回复；未自动加入退出模式、宠物收集钥匙或新控制对象TP功能。故“完全无人值守推进下一阶段”尚未确认完成。

## Recovery 与多客户端

复用 claim_quest_recovery('bumbles_pet') 和 automation_owner。Loading、战斗、NPC对话、补药、打手探测/救援、主线续接及已有Recovery时不执行。主线同步屏障在实际操作前仍须通过。

模式处理/等待期间暂停任务TP、Lemuria导航回城、近点重进场、地牢刷新、主线Finder、回城校区/好友TP及120秒静止重启。打手只暂停跟随/战斗补位，不进入宠物模式。探测尚未完成时不设置宠物暂停标记，避免阻止打手探测导致死锁。阶段结束且恢复巫师状态后重置原有主线同步状态。

## 文件与验证

修改：src/questing.py、src/paths.py、XuanShu.py；新增 tests/test_bumbles_pet_mode.py。

17项专项测试通过，覆盖准确匹配、错误区域/Quest/Goal拒绝、英文阶段、不同数值ID、已是宠物、已开菜单、状态稳定、两次重试、未知模式、未到坐标、忙碌/屏障、纯打手、阶段推进但仍是宠物、读失败不清尝试记录、取消释放锁、普通TP及其他Recovery暂停、灰按钮不点击。

相关回归79项中76项通过；test_dungeon_quest_refresh中3项失败在HEAD原始Quester上也能复现（HEAD该套件另有2项既存失败），未顺带修复。所有改动源文件/测试的内存编译检查及定向diff检查通过。

未打包、未注入/控制正在运行的客户端、未使用computer-use。实机状态切换、实际数值Quest ID和后续宠物任务TP兼容性均未验证。

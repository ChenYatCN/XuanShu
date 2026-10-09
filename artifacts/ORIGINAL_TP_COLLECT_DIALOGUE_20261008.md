# 恢复普通任务传送、食物采集、NPC 提速 — 完成

窗口：01a11b3d-a663-7980-9712-e14136cb0966。本轮用户消息 item ID 未提供。用户要求合并处理：撤回附近盲按 X 和新增步行补路，普通任务恢复原版任务传送；修复 p1 寻找被偷走的食物，提供 AZ-FoodStores，XYZ(-5004.500, 4956.112, 25.736)，距离 132.5；保留传送未站稳而有效交互已经出现时的交互优先；接受/完成及下一步加快；分析 p4 不自动攻击。没有授权构建或实时游戏输入。保留既有组队、专用地图恢复和其余脏工作树变更。

完成修改：

- src/questing.py 移除 try_nearby_quest_x 和两个调用，撤回 enter_party_dungeon 的 nearby_xyz 分支与额外 ownership 导入。普通任务的 move_until_quest_interaction 改用原有手动任务 TP 使用的 navmap_tp；已有交互 watcher 与取消/排空机制仍保留，未增加落点站稳条件。新旧自动任务入口都移除通用 _maybe_reenter_quest_trigger 调用，专用地图恢复仍保留。navmap_tp 未修改，原有直接传送、导航/螺旋回退和其原有短距离 goto 保留；普通任务不再走新增 collision A* 补路及 30 秒近点恢复。
- src/collecting.py 候选移动/返回采集刷新点同样复用 navmap_tp。模板名称不足以匹配时，再读取 entity.object_name()，可识别用户开发者实体列表中的 AZ-FoodStores，正常已匹配实体不增加读取。
- src/collect_matching.py 关联已验证目标与实体标签：WizardGameObjects_00000553 Stolen Food/被偷走的食物，WizardGameObjects_00000549 Food Stores/食品店。来自现有 src/data/collect_names.json 的有限筛选与用户截图/实体证据，不硬编码坐标或模糊扩大其他食物匹配。运行时和缓存实例的 CollectNames 都获得这组关联。
- 接受/完成点击间隔从 1.5 到 .3 秒，接受最多 3 次仍保留 4.5 秒服务器响应窗口，避免快点击导致提前 ESC。正常任务快照稳定 .3 秒后继续，数据改变会重新计时。对话真实进展已确认时安静窗口缩短到 .3，优先直接检查进展，跳过正常成功路径的固定 1.25 秒延迟；未确认进展的旧等待/重试保留。auto_quest 的轮询从 1 到 .3 秒。
- 删除已撤回功能的 tests/test_nearby_quest_x.py，保留历史实现记录并标明已撤回。更新直接相关测试引用，加入食物已显示/名称不可读两种真实采集进度验证、加快接受但不提前退出、短稳定窗口遇数据变化重置、到任务点仍无交互时继续普通组队移动而不盲 X。

验证：工具输出 3d405c，9 个直接相关模块共 209 项通过（6.679 秒）：test_collect_matching、test_collecting、test_quest_interaction_priority、test_quest_party_shared_target、test_party_dungeon_interaction、test_quest_party_dungeon、test_npc_quest_accept、test_npc_interaction_retry、test_npc_quest_menu。之后仅移除旧 leader 自动任务路径的同一通用步行重进场调用并做最终语法/差异检查。首次运行 204 项发现 4 个 fixture/现有组队分类断言不符；隐藏入口 fixture 明确没有 hitters，当前工作树的新组队测试已对应真实单人/普通整组语义，最终 209 项通过。不要将测试数与其他历史轮次相加。仅离线模拟，未验证实时游戏。

p4 证据与限制：附件 9fb38592-c37a-4f41-bbad-0f276587ae63 日志中，21:53:46 p3/p4 自动战斗被停止并重新开启，21:53:47.723 p4 开始 handling combat；21:53:52.618/619 记录 p4 战斗掉落；21:53:54.771 为 clear_post_combat_phase 的战后移动完成，该函数不关闭自动战斗。21:54:01.613 战斗传送恢复；21:54:25..30 p4 跟随 p3 失败 No friends online，片段内没有后续 p4 入战记录。不能据此确定当前牌局出牌故障，不能声称已修复 p4 实时战斗。此次未改动战斗逻辑。

最后停点：本轮实现与必要离线验证完成，交付源码。未构建、打包、安装或更新 EXE；需新源码/重新打包运行才生效。无需重复已通过的测试或继续扩大探索。

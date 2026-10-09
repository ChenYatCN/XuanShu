# 红漆采集跨区续接记录

- 用户目标：在 Azteca/AZ_Z09_AltoAlto 接到红漆的收集胭脂甲虫阶段时，先 TP 到 XYZ(-4447.606, 5160.961, 493.799)，确认切到 Azteca/AZ_Z06_CloudburstIsland 后开始全图搜索。
- 限定条件：源区域、采集胭脂甲虫、地点暴雨森林、总数 4 且未完成；保留原有工作区修改，不打包、不操作游戏。
- 已修改：src/collecting.py 复用谷物袋的切区确认流程；src/questing.py 在普通任务移动前拦截；XuanShu.py 暂停相关打手跟随及超时恢复。切区连续确认 3 次，失败不搜索源地图，30 秒冷却重试，队伍到达后先请求区域确认。
- 相关测试：tests/test_azteca_beetle_entry.py；tests/test_quest_party_solo_follow.py 增加该恢复状态的跟随保护测试。
- 验证完成：新增 11 项切区测试和 2 项跟随测试全部通过；现有谷物袋与采集测试、队伍地牢交互与进入测试通过。共检查 201 项，综合各次结果 199 项通过，2 项原有断言失败；没有运行全量测试。
- 测试环境：受限环境创建 Windows asyncio socketpair 时阻塞；中止仅本次启动的两个测试进程，在获准的限制外环境运行离线测试。跟随测试替身补充 is_spiral_door_open=False，与既有世界门保护保持一致。
- 原有失败一：test_auto_quest_uses_ready_interaction_before_reentry_or_shared_movement 的 warning.assert_called_once() 未触发；将新增 _maybe_enter_azteca_beetle_map 替换为 False 后同样失败。
- 原有失败二：test_solo_has_priority_over_stale_group_marker 预期单人区域优先，实际为等待 Lemuria Dungeon 区域切换；在内存中移除新增 azteca_beetle_entry 跟随分支后同样失败。未修改这两项无关业务逻辑。
- 语法检查和受影响已跟踪文件的 git diff --check 通过。
- 最后停点：请求范围内实现与必要验证完成，可以交付。
- 下一步：若用户需要，可启动修改后的源码进行实际游戏验证；当前没有该验证结果。
- 未验证：实际游戏入口能否由该坐标触发、游戏中的切区与采集效果；EXE 未重新打包。

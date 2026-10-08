# 主线任务匹配批量对齐记录（2026-10-07）

## 结果

- 核对现有 2,094 条主线索引；2,093 条已有语言键，唯一未配项为 wizard city 第 1 条 Tutorial。
- 本轮补充 46 条记录：48 个语言键、19 个经人工核实的当前名称别名、25 项中文名。先前的大象游行修复保留。
- 保留原任务表世界、编号、总数、英文原名、全部已有任务 ID、旧键、旧别名和说明；未修改源 XLSX。
- 精确标题/已有唯一身份对齐；不自动使用近似匹配。19 项旧名/错拼同时核对本机当前英文资源与对应世界的公开任务线。
- 公共任务线版本的编号/总数与原表可能不同：本轮没有用网站编号替换原表编号。

## 截图里的棘手的任务

本机 QuestTitle_80B56 对应 Mission Impossible / 棘手的任务，索引中与 mirage 的同名任务共享候选，原全表匹配因此返回 None。
沿用 NPC 列表已有的世界上下文：任务 ID 始终优先，唯一身份仍可跨世界识别；仅对共享键/名称按可用世界消歧。
主线身份、进度日志、已接任务恢复、指定 NPC 任务选项和邀请的身份核对已接上；无上下文或同一世界仍有多个候选时保留歧义。
模拟当前 Zafaria 海滨任务返回第 106 条，不再进入 Finder；未从截图编造实际 Quest ID。

## 证据与边界

- 只读本机 D:/Steam/steamapps/common/Wizard101/Data/GameData/Root.wad 的 Locale/en-US/QuestTitle.lang，使用其当前英文列。
- 只读 Locale_en-US-root.wad.d 的对应 QuestTitle.lang，使用其中文显示列。
- 语言资源发现 21 个多世界同名候选；不凭英文名给它们重新分配所属世界。
- 112 项针对性测试通过，包括所有已配语言键按所属世界匹配、原编号/总数保护、唯一 ID 优先、未知上下文拒绝猜测、旧任务恢复和 NPC 流程。
- 修改后只读复核不再提出新增唯一匹配，git diff --check 通过。
- 现有 AsyncMock 测试夹具仍有一个非致命未 await 警告，未顺带修改无关运行逻辑。
- 未进行游戏实测、EXE 构建或安装验证；运行时索引有缓存，需要重启源码程序或重新打包旧 EXE。
- 先前 838u 找不到步行路线的问题不在本次对齐范围，仍不宣称已解决。

## 名称核对来源

- [Marleybone](https://finalbastion.com/wizard101-guides/w101-quest-guides/marleybone-main-quest-line-guide/)
- [Polaris](https://finalbastion.com/wizard101-guides/w101-quest-guides/polaris-main-quest-line-guide/)
- [Empyrea](https://finalbastion.com/wizard101-guides/w101-quest-guides/empyrea-main-quest-line/)
- [Mirage](https://finalbastion.com/wizard101-guides/w101-quest-guides/mirage-main-quest-line/)
- [Zafaria](https://finalbastion.com/wizard101-guides/w101-quest-guides/zafaria-main-quest-line-guide/)
- [Azteca](https://wizard101folio.com/azteca-quest-tree/)
- [Azteca names](https://finalbastion.com/wizard101-guides/w101-quest-guides/azteca-main-quest-line/)
- [Khrysalis](https://finalbastion.com/wizard101-guides/w101-quest-guides/khrysalis-main-quest-line-guide/)
- [Dragonspyre](https://walkthroughwizard.com/all-posts/mmorpgs/wizard101/wizard101-main-questline-guides/wizard101-all-main-quests-in-dragonspyre/)

## 本轮修改条目

| 世界 | 原序号 | 原英文名 | 新增语言键 | 新增别名 | 新增中文名 |
| --- | ---: | --- | --- | --- | --- |
| wallaru | 3 | Walla-Me-And-You | QuestTitle_18896C |  |  |
| marleybone | 7 | Springing the Stitch | QuestTitle_9B46 | Springing the Snitch | 释放告密者 |
| mooshu | 10 | Counting Sheeps | QuestTitle_12DED |  | 数羊 |
| empyrea | 10 | Spell-Castaway | QuestTitle_00001674 |  |  |
| Karamelle | 11 | Sweets-n-Stuff | QuestTitle_170A80 |  |  |
| novus | 11 | Ba-Zheng-A | QuestTitle_1797C8 |  |  |
| polaris | 16 | Storming the Bastille | QuestTitle_00001509 | Storming the Basstille | 攻占巴斯提尔 |
| azteca | 18 | The Black Sun's Tower | QuestTitle_9D0EF | The Black Sun's Wake | 黑日密室的觉醒 |
| Karamelle | 18 | Quaky-Quaky | QuestTitle_00001753 |  |  |
| polaris | 19 | Vive Le Penguinonia | QuestTitle_148A65 | Vive la Penguinonia | 维维拉 彭圭诺尼亚 |
| marleybone | 23 | Purloin the Plains | QuestTitle_9B5B | Purloin the Plans | 盗取计划 |
| mooshu | 23 | Key to Success | QuestTitle_9B8B |  |  |
| wallaru | 25 | Ridgy-Didge | QuestTitle_00001980 |  |  |
| Khrysalis | 30 | Z-Z-Zabotage! | QuestTitle_12922F |  |  |
| novus | 41 | Tung-Lashing | QuestTitle_00001907 |  |  |
| polaris | 46 | Nostradominus | QuestTitle_148AF2 | Nostradonimus | 诺斯特拉多尼穆斯 |
| empyrea | 51 | Tunnel of Visions | QuestTitle_153B78 | Tunnel Visions | 隧道幻影 |
| polaris | 54 | Cage-Free | QuestTitle_148AFD |  |  |
| novus | 54 | Dupli-Qhat | QuestTitle_173366 |  |  |
| mirage | 55 | The Purzzian Vassals | QuestTitle_1513CD | The Purrzian Vassals | 普尔齐安附庸 |
| polaris | 61 | Borealis Marjoris | QuestTitle_129595, QuestTitle_148B0B | Borealis Majoris | 博瑞利斯·马约里斯 |
| mirage | 66 | Into Instanboa | QuestTitle_155156 | Into Istanboa | 进入伊斯坦博尔 |
| empyrea | 66 | Re-Counciliation | QuestTitle_153BCC |  |  |
| Dragonspyre | 68 | The Den of Dean | QuestTitle_1ED48 | The Den of the Dean | 主任的巢穴 |
| empyrea | 70 | Athano-More | QuestTitle_158FF2 |  |  |
| Karamelle | 70 | The King is in the Building | QuestTitle_1A0372 |  |  |
| empyrea | 71 | Bat-tlefield | QuestTitle_15900A |  |  |
| empyrea | 79 | Oaky-Doke | QuestTitle_156160 |  |  |
| polaris | 83 | Back to Walkruskberg | QuestTitle_14DC0B | Back to Walruskberg | 回到瓦鲁斯克堡 |
| mirage | 87 | Aggrobah Alliance | QuestTitle_00001601 |  | 阿戈巴联盟 |
| empyrea | 91 | Empyre-B | QuestTitle_16435F |  |  |
| mirage | 99 | Djinn Conspiracy | QuestTitle_00001613 |  | 魔神阴谋 |
| empyrea | 107 | 'Til Wizard Voices Wake Us | QuestTitle_16434E | 'Till Wizard Voices Wake Us | “直到巫师的声音唤醒我们” |
| empyrea | 108 | Zana-Redo | QuestTitle_1624DE |  |  |
| mirage | 115 | The Sands of Time | QuestTitle_2A4B5 |  | 时之砂 |
| mirage | 117 | Granfather Spider | QuestTitle_00001611 | Grandfather Spider | 祖父蜘蛛 |
| mirage | 118 | Sands Restored | QuestTitle_00001607 |  | 沙漠重生 |
| zafariA | 130 | Knocking on Kallah's Door | QuestTitle_71D1F | Knockin on Kallah's Door | 进入卡拉汉·银背大猩猩的门 |
| azteca | 134 | Sign of Capactli | QuestTitle_00000997 | Sign of Cipactli | 希帕克特里的迹象 |
| azteca | 138 | Showing the Teeth | QuestTitle_A841F | Showing Teeth | 展示牙齿 |
| Khrysalis | 154 | Star-Dogged | QuestTitle_130FAE, QuestTitle_130FB0 |  |  |
| Khrysalis | 159 | The Glittering Eye | QuestTitle_130FF1 | Thy Glittering Eye | 你的闪耀之眼 |
| azteca | 182 | Strong Smooth Swords | QuestTitle_00001022 | Strong Smooth Words | 恭维话 |
| Khrysalis | 247 | Creatures of Shadow and Light | QuestTitle_00001370 |  | 阴影与光明的生物 |
| Khrysalis | 250 | Tri-Stat | QuestTitle_00001367 |  |  |
| Khrysalis | 277 | End Game | QuestTitle_162478 |  |  |

## 续接

Window 01a1160e-45fa-7dd0-8906-8852ea8f63d7；用户连续两次要求所有能对齐的任务匹配都对齐。本轮源码实现及必要验证完成。
相关文件：src/data/mainline_quests.json、src/mainline_progress.py、src/questing.py、tests/test_mainline_alignment.py。
只读核对脚本：artifacts/audit_mainline_alignment_20261007.py。没有 subagent、全量测试、打包、提交或清理其他改动。
若后续实机仍有未匹配，优先读具体 Quest ID / Language Key / 标题 / 世界，不把 game mainline=True 一概当作本索引主线，也不扩大无条件按键。

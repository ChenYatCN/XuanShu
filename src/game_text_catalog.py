"""Verified text IDs and bilingual values from Locale_en-US-root.wad.

Source: Locale/en-US/{GUI,GUI2,GUI3,PetGames,WizardQuestGoals}.lang.
Snapshot inspected 2026-09-04. No game installation is needed at runtime.
English source and translated display text are retained separately.
"""

# Verified PC NPCInteract task-action records from the bilingual archive
# snapshot inspected 2026-10-08. Match complete forms, never arbitrary verbs.
# Games, hatchery, bank, gardening/fishing and key-consumption services stay
# outside generic quest input; their specialized handlers retain control.
INTERACTION_RECORDS = {
    "GUI_00000001": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Use","按&Icons_XKey& 或&Icons_LeftMouseClick& 使用"],
    "GUI_00000002": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Collect","按&Icons_XKey& 或&Icons_LeftMouseClick& 收集"],
    "GUI_00000003": ["Press &InputBindings_NPCInteract& to Use","按&Icons_XKey& 使用"],
    "GUI_00000015": ["Press &InputBindings_NPCInteract& to Collect","按下 &Icons_XKey& 采集"],
    "GUI_00000017": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Spill Milk","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 泼牛奶"],
    "GUI_00000019": ["Press &InputBindings_NPCInteract& to Activate","按下 &Icons_XKey& 激活"],
    "GUI_00000021": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Heal","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 治疗"],
    "GUI_00000029": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Ride","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 骑乘"],
    "GUI_00000030": ["Press &InputBindings_NPCInteract& to Open","点击 &Icons_XKey& 打开"],
    "GUI_00000034": ["Press &InputBindings_NPCInteract& to Interact","按下 &Icons_XKey& 互动"],
    "GUI_00000035": ["Press &InputBindings_NPCInteract& to Interact","按下 &Icons_XKey& 互动"],
    "GUI_00000036": ["Press &InputBindings_NPCInteract& to Interact","按下 &Icons_XKey& 互动"],
    "GUI_00000039": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Activate","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 激活"],
    "GUI_00000040": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Destroy","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 摧毁"],
    "GUI_00000043": ["Press &InputBindings_NPCInteract& to Lock","点击 &Icons_XKey& 锁定"],
    "GUI_00000046": ["Press &InputBindings_NPCInteract& to Teleport","按下 &Icons_XKey& 传送"],
    "GUI_00000511": ["Press &InputBindings_NPCInteract& to Restore","按下 &Icons_XKey& 恢复"],
    "GUI_00001030": ["Press &InputBindings_NPCInteract& to start a fire","按下 &Icons_XKey& 点火"],
    "GUI_00001107": ["Press &InputBindings_NPCInteract& to Skip Ride","按下 &Icons_XKey& 跳过骑乘"],
    "GUI_00001241": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Teleport","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 传送"],
    "GUI_00003197": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Place","按&Icons_XKey&或&Icons_LeftMouseClick&放置"],
    "GUI_00004735": ["Press &InputBindings_NPCInteract&  to Triton Ave.","按下 &Icons_XKey& 返回海神大街."],
    "GUI_00004755": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Interact","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 互动"],
    "GUI_00004756": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Interact","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 互动"],
    "GUI_00004758": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Fix","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 修复"],
    "GUI_00004766": ["Press &InputBindings_NPCInteract& to Jump","按下 &Icons_XKey& 跳跃"],
    "GUI_00004767": ["Press &InputBindings_NPCInteract& to Dive Into Storm Lord's Temple","按下 &Icons_XKey& 潜入风暴领主神庙"],
    "GUI_00004768": ["Press &InputBindings_NPCInteract& to Dive In","按下 &Icons_XKey& 潜水"],
    "GUI_00004769": ["Press &InputBindings_NPCInteract& to Swim to Surface","按下 &Icons_XKey& 游到水面"],
    "GUI_00005412": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Turn","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 转向"],
    "GUI_00005414": ["Press &InputBindings_NPCInteract& to Climb Rope","按下 &Icons_XKey& 攀爬绳子"],
    "GUI_00005447": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Use","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 使用"],
    "GUI_00005454": ["Press &InputBindings_NPCInteract& to Carve","点击 &Icons_XKey& 雕刻"],
    "GUI_00005455": ["Press &InputBindings_NPCInteract& to Shatter","按下 &Icons_XKey& 碎裂"],
    "GUI_00005458": ["Press &InputBindings_NPCInteract& to Collect","按下 &Icons_XKey& 收集"],
    "GUI_00005464": ["Press &InputBindings_NPCInteract& to Deactivate","按下 &Icons_XKey& 解除"],
    "GUI_00005465": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Open","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 打开"],
    "GUI_00005466": ["Press &InputBindings_NPCInteract& to Use Desk","按下 &Icons_XKey& 使用桌子"],
    "GUI_00005468": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Sail","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 起航"],
    "GUI_00005471": ["Press &InputBindings_NPCInteract& to Use","按下 &Icons_XKey& 使用"],
    "GUI_00005472": ["Press &InputBindings_NPCInteract& to Clear","按下 &Icons_XKey& 清理"],
    "GUI_00005473": ["Press &InputBindings_NPCInteract& to Unlock","按下 &Icons_XKey& 解锁"],
    "GUI_00005554": ["Press &InputBindings_NPCInteract& to Melt","按下 &Icons_XKey& 融合"],
    "GUI_00005557": ["Press &InputBindings_NPCInteract& to Read","点击 &Icons_XKey& 阅读"],
    "GUI_00005558": ["Press &InputBindings_NPCInteract& to Repair","按下 &Icons_XKey& 修理"],
    "GUI_00005570": ["Press &InputBindings_NPCInteract& to Summon Bears","按下 &Icons_XKey& 召唤熊"],
    "GUI_00005571": ["Press &InputBindings_NPCInteract& to Summon Giants","按下 &Icons_XKey& 召唤巨人"],
    "GUI_00005578": ["Press &InputBindings_NPCInteract& to Teleport","按下 &Icons_XKey& 传送"],
    "GUI_00005580": ["Press &InputBindings_NPCInteract& to Teleport","按下 &Icons_XKey& 传送"],
    "GUI_00005803": ["Press &InputBindings_NPCInteract& to Trim","按下 &Icons_XKey& 修剪"],
    "GUI_00005804": ["Press &InputBindings_NPCInteract& to Trim","按下 &Icons_XKey& 修剪"],
    "GUI_00005806": ["Press &InputBindings_NPCInteract& to Clean","按下 &Icons_XKey& 清除"],
    "GUI_00005829": ["Press &InputBindings_NPCInteract& to Trim","按下 &Icons_XKey& 修剪"],
    "GUI_00005981": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Teleport","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 传送"],
    "GUI_00005982": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Teleport","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 传送"],
    "GUI_00005983": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Teleport","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 传送"],
    "GUI_00005988": ["Press &InputBindings_NPCInteract& to Ride","按下 &Icons_XKey& 骑乘"],
    "GUI_00005992": ["Press &InputBindings_NPCInteract& to Free","按下 &Icons_XKey& 释放"],
    "GUI_00006029": ["Press &InputBindings_NPCInteract& to Place","按下 &Icons_XKey& 放置"],
    "GUI_00006067": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Inspect","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 检查"],
    "GUI_00006070": ["Press &InputBindings_NPCInteract& to Extinguish","按下 &Icons_XKey& 熄灭"],
    "GUI_00006133": ["Press &InputBindings_NPCInteract& to Climb","按下 &Icons_XKey& 攀爬"],
    "GUI_00006134": ["Press &InputBindings_NPCInteract& to Cut","按下 &Icons_XKey& 切断"],
    "GUI_00006170": ["Press &InputBindings_NPCInteract& to Inspect","按下 &Icons_XKey& 检查"],
    "GUI_00006176": ["Press &InputBindings_NPCInteract& to Purify","按下 &Icons_XKey& 净化"],
    "GUI_00006199": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Repair","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 修复"],
    "GUI_00006257": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Eat","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 食用"],
    "GUI_00006277": ["Press &InputBindings_NPCInteract& to Bobble","按下 &Icons_XKey& 摇动"],
    "GUI_00006290": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Boil","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 煮沸"],
    "GUI_00006291": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Serve","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 服务"],
    "GUI_00006292": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Clean","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 清扫"],
    "GUI_00006293": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Trim","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 修剪"],
    "GUI_00006294": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Feed","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 喂食"],
    "GUI_00006314": ["Press &InputBindings_NPCInteract& to Throw","按下 &Icons_XKey& 投掷"],
    "GUI_00006315": ["Press &InputBindings_NPCInteract& to Douse","按下 &Icons_XKey& 浸泡"],
    "GUI_00006316": ["Press &InputBindings_NPCInteract& to Use Ladle","按下 &Icons_XKey& 使用勺子"],
    "GUI_00006317": ["Press &InputBindings_NPCInteract& to Meditate","按下 &Icons_XKey& 冥想"],
    "GUI_00006318": ["Press &InputBindings_NPCInteract& to Restore Shrine","按下 &Icons_XKey& 恢复圣地"],
    "GUI_00006319": ["Press &InputBindings_NPCInteract& to Cure","按下 &Icons_XKey& 治愈"],
    "GUI_00006332": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Mix","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 混合魔力泉水"],
    "GUI_00006340": ["Press &InputBindings_NPCInteract& to Fix","按下 &Icons_XKey& 修理"],
    "GUI_00006657": ["Press &InputBindings_NPCInteract& to Use Magic Raft","按下 &Icons_XKey& 使用魔法艇"],
    "GUI_00006660": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Smash","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 打击"],
    "GUI_00006667": ["Press &InputBindings_NPCInteract& to Burn Webs","按下 &Icons_XKey& 燃烧蛛网"],
    "GUI_00006671": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Climb","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 攀登"],
    "GUI_00006672": ["Press &InputBindings_NPCInteract& to Create Bridge","按下 &Icons_XKey& 建桥"],
    "GUI_00006674": ["Press &InputBindings_NPCInteract& to Search Nest","按下 &Icons_XKey& 寻找巢穴"],
    "GUI_00006675": ["Press &InputBindings_NPCInteract& to Use Forge","按下 &Icons_XKey& 使用熔炉"],
    "GUI_00006677": ["Press &InputBindings_NPCInteract& to Place Gemstone","按下 &Icons_XKey& 放置宝石"],
    "GUI_00006680": ["Press &InputBindings_NPCInteract& to Poke","按下 &Icons_XKey& 刺"],
    "GUI_00006681": ["Press &InputBindings_NPCInteract& to Remove Poison","按下 &Icons_XKey& to 施毒"],
    "GUI_00006682": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Chew","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 咀嚼"],
    "GUI_00006685": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Rescue","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 营救"],
    "GUI_00006686": ["Press &InputBindings_NPCInteract& to Gather","按下 &Icons_XKey& 收集"],
    "GUI_00006687": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Exit","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 离开"],
    "GUI_00006688": ["Press &InputBindings_NPCInteract& to Take","按下 &Icons_XKey& 拿走"],
    "GUI_00006689": ["Press &InputBindings_NPCInteract& to Cover","按下 &Icons_XKey& 覆盖"],
    "GUI_00006691": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Burn Away","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 烧毁"],
    "GUI_00006694": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Use Explosives","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 使用炸药"],
    "GUI_00006695": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Break Up","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 驱散"],
    "GUI_00006697": ["Press &InputBindings_NPCInteract& to Pop","按下 &Icons_XKey& 射击"],
    "GUI_00006698": ["Press &InputBindings_NPCInteract& to Paint","按下 &Icons_XKey& 绘画"],
    "GUI_00006700": ["Press &InputBindings_NPCInteract& to Slice","按下 &Icons_XKey& 割破"],
    "GUI_00006703": ["Press &InputBindings_NPCInteract& to Exit","按下 &Icons_XKey& 离开"],
    "GUI_00006706": ["Press &InputBindings_NPCInteract& to Teleport","按下 &Icons_XKey& 传送"],
    "GUI_00006707": ["Press &InputBindings_NPCInteract& to Teleport","按下 &Icons_XKey& 传送"],
    "GUI_00006708": ["Press &InputBindings_NPCInteract& to Teleport","按下 &Icons_XKey& 传送"],
    "GUI_00006723": ["Press &InputBindings_NPCInteract& to Tear Away","按下 &Icons_XKey& 疾驰"],
    "GUI_00006951": ["Press &InputBindings_NPCInteract& to Add to the Pot","按&Icons_XKey& 加入锅"],
    "GUI_00006987": ["Press &InputBindings_NPCInteract& to Examine","按&Icons_XKey& 查看"],
    "GUI_00006988": ["Press &InputBindings_NPCInteract& to Select","按&Icons_XKey& 选择"],
    "GUI_00007021": ["Press &InputBindings_NPCInteract& to Piece Together","按&Icons_XKey& 拼凑"],
    "GUI_00007023": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Place Orb","按&Icons_XKey& 或&Icons_LeftMouseClick& 放置球体"],
    "GUI_00007075": ["Press &InputBindings_NPCInteract& to Break","按&Icons_XKey& 打破"],
    "GUI_00007076": ["Press &InputBindings_NPCInteract& to Use Crystals","按&Icons_XKey& 使用水晶"],
    "GUI_00007077": ["Press &InputBindings_NPCInteract& to Extinguish","按&Icons_XKey& 熄灭"],
    "GUI_00007079": ["Press &InputBindings_NPCInteract& to Free Prisoner","按&Icons_XKey& 释放囚犯"],
    "GUI_00007080": ["Press &InputBindings_NPCInteract& to Plant","按&Icons_XKey& 来种植"],
    "GUI_00007103": ["Press &InputBindings_NPCInteract& to Collect","按&Icons_XKey&收集"],
    "GUI_00007106": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Cure","按&Icons_XKey& 或&Icons_LeftMouseClick& 治愈"],
    "GUI_00007117": ["Press &InputBindings_NPCInteract& to Search","按&Icons_XKey& 进行搜索"],
    "GUI_00007118": ["Press &InputBindings_NPCInteract& to Untie","按&Icons_XKey& 解开"],
    "GUI_00007218": ["Press &InputBindings_NPCInteract& to Burn Away","按&Icons_XKey& 烧掉"],
    "GUI_00007236": ["Press &InputBindings_NPCInteract& to Catch","按&Icons_XKey& 捕捉"],
    "GUI_00007239": ["Press &InputBindings_NPCInteract& to Descend","按&Icons_XKey& 下降"],
    "GUI_00007474": ["Press &InputBindings_NPCInteract& to Light","按&Icons_XKey& 点亮"],
    "GUI_00007477": ["Press &InputBindings_NPCInteract& to Restore","按&Icons_XKey& 恢复"],
    "GUI_00008161": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Paste","按&Icons_XKey& 或&Icons_LeftMouseClick& 粘贴"],
    "GUI_00008359": ["Press &InputBindings_NPCInteract& to Enter Boat","按&Icons_XKey& 进入船"],
    "GUI_00008360": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Pick Up","按&Icons_XKey& 或&Icons_LeftMouseClick& 拿起"],
    "GUI_00008363": ["Press &InputBindings_NPCInteract& to Dig","按&Icons_XKey& 进行挖掘"],
    "GUI_00008371": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Wear","按&Icons_XKey& 或&Icons_LeftMouseClick& 佩戴"],
    "GUI_00008373": ["Press &InputBindings_NPCInteract& to Cast","按&Icons_XKey& 进行投射"],
    "GUI_00008379": ["Press &InputBindings_NPCInteract&  to Travel","按&Icons_XKey& 去旅行"],
    "GUI_00008430": ["Press &InputBindings_NPCInteract& to Travel","按&Icons_XKey& 去旅行"],
    "GUI_00008933": ["Press &InputBindings_NPCInteract& to Remove","按&Icons_XKey& 删除"],
    "GUI_00008942": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Remove","按&Icons_XKey& 或&Icons_LeftMouseClick& 删除"],
    "GUI_00008943": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Enchant","按&Icons_XKey& 或&Icons_LeftMouseClick& 来附魔"],
    "GUI_00008944": ["Press &InputBindings_NPCInteract& to Return","按&Icons_XKey& 返回"],
    "GUI_00008952": ["Press &InputBindings_NPCInteract& to Awaken","按&Icons_XKey& 唤醒"],
    "GUI_00008957": ["Press &InputBindings_NPCInteract& to Dispel","按&Icons_XKey& 来驱散"],
    "GUI_00008988": ["Press &InputBindings_NPCInteract& to interact","按&Icons_XKey& 进行交互"],
    "GUI_00008994": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Bury","按&Icons_XKey& 或&Icons_LeftMouseClick& 埋葬"],
    "GUI_00009015": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Cast","按&Icons_XKey& 或&Icons_LeftMouseClick& 进行投射"],
    "GUI_00009035": ["Press &InputBindings_NPCInteract& to Destroy","按&Icons_XKey& 销毁"],
    "GUI_00009066": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Reverse Polarity","按&Icons_XKey& 或&Icons_LeftMouseClick& 反转极性"],
    "GUI_00009841": ["Press &InputBindings_NPCInteract&  to Place Sign","按&Icons_XKey& 放置标志"],
    "GUI_00009869": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to mine","按&Icons_XKey& 或&Icons_LeftMouseClick& 进行挖矿"],
    "GUI_00009908": ["Press &InputBindings_NPCInteract& to Thaw","按&Icons_XKey& 解冻"],
    "GUI_00009916": ["Press &InputBindings_NPCInteract& to Study","按&Icons_XKey& 学习"],
    "GUI_00009924": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Sit","按&Icons_XKey& 或&Icons_LeftMouseClick& 坐下"],
    "GUI_00010614": ["Press &InputBindings_NPCInteract& to Burn Nest","按下 &Icons_XKey& 来烧毁巢穴"],
    "GUI_00010668": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Rest","按&Icons_XKey&或&Icons_LeftMouseClick&来休息"],
    "GUI_00010670": ["Press &InputBindings_NPCInteract& to Rest","按&Icons_XKey&休息"],
    "GUI_00010697": ["Press &InputBindings_NPCInteract& to Ride to Outback","按&Icons_XKey&骑到内陆"],
    "GUI_00010699": ["Press &InputBindings_NPCInteract& to plant","按&Icons_XKey&或&Icons_LeftMouseClick&种下"],
    "GUI_00010700": ["Press &InputBindings_NPCInteract& to Install","按&Icons_XKey&安装"],
    "GUI_00010701": ["Press &InputBindings_NPCInteract& to Disable","按&Icons_XKey&禁用"],
    "GUI_00010702": ["Press &InputBindings_NPCInteract& Ride to Outback","按&Icons_XKey&骑到内陆"],
    "GUI_00010723": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Hide","按&Icons_XKey&或&Icons_LeftMouseClick&隐藏"],
    "GUI_00010724": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Start Pump","按&Icons_XKey&或&Icons_LeftMouseClick&启动泵"],
    "GUI_00010725": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to use Dream Water","按&Icons_XKey&或&Icons_LeftMouseClick&使用梦水"],
    "GUI_00010963": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to transfer","按 &InputBindings_NPCInteract& 或 &Icons_LeftMouseClick& 传送"],
    "GUI_00010964": ["Press &InputBindings_NPCInteract& to Freeze","按 &InputBindings_NPCInteract& 冻结"],
    "GUI_00010968": ["Press &InputBindings_NPCInteract& to Add Comb Folder","按 &InputBindings_NPCInteract& 添加梳子文件夹"],
    "GUI_00010970": ["Press &InputBindings_NPCInteract& to Add Offering","按 &InputBindings_NPCInteract& 添加供品"],
    "GUI_00010976": ["Press &InputBindings_NPCInteract& to Investigate","按 &InputBindings_NPCInteract& 调查"],
    "GUI_00010977": ["Press &InputBindings_NPCInteract& to Summon","按 &InputBindings_NPCInteract& 召唤"],
    "GUI_00010979": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Drink","按 &InputBindings_NPCInteract& 或 &Icons_LeftMouseClick& 饮用"],
    "GUI_00010980": ["Press &InputBindings_NPCInteract& to Transform","按 &InputBindings_NPCInteract& 变形"],
    "GUI_00010981": ["Press &InputBindings_NPCInteract& to Set Clock","按 &InputBindings_NPCInteract& 设置时钟"],
    "GUI_00010982": ["Press &InputBindings_NPCInteract& to Drop","按 &InputBindings_NPCInteract& 丢弃"],
    "GUI_00010983": ["Press &InputBindings_NPCInteract& to Signal","按 &InputBindings_NPCInteract& 发出信号"],
    "GUI_00010984": ["Press &InputBindings_NPCInteract& to take position","按 &InputBindings_NPCInteract& 就位"],
    "GUI_00010991": ["Press &InputBindings_NPCInteract& to Remember","按 &InputBindings_NPCInteract& 回忆"],
    "GUI_00010992": ["Press &InputBindings_NPCInteract& to Listen","按 &InputBindings_NPCInteract& 聆听"],
    "GUI_00010994": ["Press &InputBindings_NPCInteract& to Rest","按 &InputBindings_NPCInteract& 休息"],
    "GUI_00010995": ["Press &InputBindings_NPCInteract& to Tear Down","按 &InputBindings_NPCInteract& 拆除"],
    "GUI_00010997": ["Press &InputBindings_NPCInteract& to plant","按 &InputBindings_NPCInteract& 种植"],
    "GUI_00010998": ["Press &InputBindings_NPCInteract& to Transform","按 &InputBindings_NPCInteract& 变形"],
    "GUI_00011005": ["Press &InputBindings_NPCInteract& to Toss in","按 &InputBindings_NPCInteract& 投入"],
    "GUI_00011006": ["Press &InputBindings_NPCInteract& to Wait","按 &InputBindings_NPCInteract& 等待"],
    "GUI_00011007": ["Press &InputBindings_NPCInteract& to Accuse","按 &InputBindings_NPCInteract& 指控"],
    "GUI_00011009": ["Press &InputBindings_NPCInteract& to add sword","按 &InputBindings_NPCInteract& 添加剑"],
    "GUI_00011010": ["Press &InputBindings_NPCInteract& to Follow","按 &InputBindings_NPCInteract& 跟随"],
    "GUI_00011011": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Place/Collect","按 &InputBindings_NPCInteract& 或 &Icons_LeftMouseClick& 放置/收集"],
    "GUI_00011013": ["Press &InputBindings_NPCInteract& to Rest","按 &InputBindings_NPCInteract& 休息"],
    "GUI_BurnItem": ["Press &InputBindings_NPCInteract& to Burn","按下 &Icons_XKey& 点燃"],
    "GUI_ChestInteract": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Open","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 打开"],
    "GUI_CollectItem": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Collect","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 收集"],
    "GUI_EnterDoor": ["Press &InputBindings_NPCInteract& to Enter","按下 &Icons_XKey& 进入"],
    "GUI_EnterNoClick": ["Press &InputBindings_NPCInteract& to Enter","点击 &Icons_XKey& 进入"],
    "GUI_GUI_Light": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Light","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 点燃"],
    "GUI_InteractFire": ["Press &InputBindings_NPCInteract& to put out fire","按下 &Icons_XKey& 灭火"],
    "GUI_NPCInteractNoClick": ["Press &InputBindings_NPCInteract& to Talk","按下 &Icons_XKey& 交谈"],
    "GUI_NPCInteractText": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Talk","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 交谈"],
    "GUI_ObjectInteract": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Interact","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 互动"],
    "GUI_ObjectPull": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Pull","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 拖动"],
    "GUI_ObjectRead": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Read","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 阅读"],
    "GUI_SpillMilk": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Spill Milk","按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 泼牛奶"],
    "GUI2_00000075": ["Press &InputBindings_NPCInteract& to Dance","按&Icons_XKey&舞蹈"],
    "GUI2_00000077": ["Press &InputBindings_NPCInteract& to Take Costume","按&Icons_XKey& 获取服装"],
    "GUI2_00000078": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Teleport","按 &Icons_XKey& 或 &Icons_LeftMouseClick& 传送"],
    "GUI2_00000082": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Use","按 &Icons_XKey& 或 &Icons_LeftMouseClick& 使用"],
    "GUI2_00000083": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Collect","按 &Icons_XKey& 或 &Icons_LeftMouseClick& 收集"],
    "GUI2_00000084": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Fix","按 &Icons_XKey& 或 &Icons_LeftMouseClick& 修复"],
    "GUI2_00000401": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Remove","按 &Icons_XKey& 或 &Icons_LeftMouseClick& 删除"],
    "GUI2_00000728": ["Press &InputBindings_NPCInteract& to Exit","按&Icons_XKey&退出"],
    "GUI2_00000790": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Read","按 &Icons_XKey& 或 &Icons_LeftMouseClick& 阅读"],
    "GUI2_00000791": ["Press &InputBindings_NPCInteract& to Read","按&图标_XKey& 阅读"],
    "GUI2_00001561": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Open","按 &Icons_XKey& 或 &Icons_LeftMouseClick&打开"],
    "GUI2__AID": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Aid","按 &Icons_XKey& 或 &Icons_LeftMouseClick& 帮助"],
    "GUI2__COLLECT": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Collect","按 &Icons_XKey& 或 &Icons_LeftMouseClick& 收集"],
    "GUI2__USE": ["Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Use","按 &Icons_XKey& 或 &Icons_LeftMouseClick& 使用"],
}

# These tables share the exact English object name but differ in translation.
OBJECT_NAME_RECORDS = {
    "Items_00016767": ["Flag Control Lever", "标志控制杆"],
    "Items_00016769": ["Flag Control Lever", "标志控制杆"],
    "WizardGameObjects_00000754": ["Flag Control Lever", "旗杆操纵杆"],
}

# Additional exact-name records from GUI2, Items, WizardGameObjects,
# ZoneLocName, WizardZone, Zone and quest/dialogue tables in the same archive.
PORTAL_RECORDS = {
    # Tamed Demox and its area labels, verified in installed GUI2/WizardZone
    # language tables (English Root.wad + translated Locale_en-US-root.wad).
    "GUI2_00002095": ["<center>Tamed Demox</center>", "<center>Tamed Demox</center>"],
    "WizardZone_00001744": ["Graveholm", "墓都"],
    "WizardZone_00001745": ["Mortal Plain", "凡人原野"],
    "WizardZone_00001746": ["Outsiders Camp", "流亡者营地"],
    "WizardZone_00001748": ["Black Lagoon", "黑湖"],
    "WizardZone_00001751": ["Howling Lands", "啸狼荒原"],
    "WizardZone_00001761": ["Scholomance", "斯科洛曼斯"],
    "GUI2_00000392": [
        "Aeriel",
        "爱丽儿(Aeriel)"
    ],
    "GUI2_00000393": [
        "Zanadu",
        "赞那都(Zanadu)"
    ],
    "GUI2_00000395": [
        "Sepidious",
        "沉闷的鱿鱼(Sepidious)"
    ],
    "GUI2_00000452": [
        "Inner Athanor",
        "内阿塔诺(Inner Athanor)"
    ],
    "GUI2_00000453": [
        "Outer Athanor",
        "外阿塔诺(Outer Athanor)"
    ],
    "GUI2_00000807": [
        "Mandalla",
        "曼陀罗(Mandalla)"
    ],
    "GUI2_00000808": [
        "Chaos Jungle",
        "混沌丛林(Chaos Jungle)"
    ],
    "GUI2_00000809": [
        "Reverie",
        "遐想(Reverie)"
    ],
    "GUI2_00000810": [
        "Nimbus",
        "灵气城(Nimbus)"
    ],
    "GUI2_00000811": [
        "Port Aero",
        "航空港(Port Aero)"
    ],
    "GUI2_00000812": [
        "Husk",
        "果壳(Husk)"
    ],
    "Items_00021780": [
        "World Gate",
        "世界之门"
    ],
    "NPCs_01748774": [
        "Aeriel",
        "爱丽儿"
    ],
    "QuickChat_00001133": [
        "Zanadu",
        "赞那都(Zanadu)"
    ],
    "QuickChat_00001134": [
        "Outer Athanor",
        "阿萨诺尔外地"
    ],
    "QuickChat_00001135": [
        "Inner Athanor",
        "阿萨诺尔内地"
    ],
    "QuickChat_00001136": [
        "Sepidious",
        "塞皮迪厄斯"
    ],
    "QuickChat_00001159": [
        "Chaos Jungle",
        "混乱丛林"
    ],
    "QuickChat_00001166": [
        "Port Aero",
        "港口Aero"
    ],
    "QuickChat_00001356": [
        "Karamelle City",
        "焦糖城市"
    ],
    "QuickChat_00001364": [
        "Black Licorice Forest",
        "黑色甘草森林"
    ],
    "QuickChat_00001365": [
        "Candy Corn Farm",
        "糖果玉米农场"
    ],
    "WizQst155358_00000006": [
        "Aeriel",
        "艾瑞尔"
    ],
    "WizQst1553EA_00000011": [
        "Sepidious",
        "塞皮迪厄斯"
    ],
    "WizQst16158D_00000047": [
        "Port Aero",
        "港口Aero"
    ],
    "WizQst1624BC_00000014": [
        "Reverie",
        "幻想"
    ],
    "WizardActorDialogue_00000788": [
        "Port Aero",
        "青青草原港口(Port Aero)"
    ],
    "WizardActorDialogue_00000793": [
        "Chaos Jungle",
        "混乱丛林"
    ],
    "WizardActorDialogue_00000930": [
        "Karamelle City",
        "焦糖城市"
    ],
    "WizardActorDialogue_00000932": [
        "Black Licorice Forest",
        "黑色甘草森林"
    ],
    "WizardActorDialogue_00000933": [
        "Gutenstadt",
        "古藤镇"
    ],
    "WizardActorDialogue_00000941": [
        "Gobblerton",
        "戈布勒顿"
    ],
    "WizardActorDialogue_00000942": [
        "Candy Corn Farm",
        "糖果玉米农场"
    ],
    "WizardActorDialogue_00000943": [
        "Black Licorice Forest",
        "黑色甘草森林"
    ],
    "WizardGameObjects_00000070": [
        "World Gate",
        "世界之门"
    ],
    "WizardZone_00001363": [
        "Inner Athanor",
        "阿萨诺尔内地"
    ],
    "WizardZone_00001367": [
        "Outer Athanor",
        "阿萨诺尔外地"
    ],
    "WizardZone_00001369": [
        "Outer Athanor",
        "阿萨诺尔外地"
    ],
    "WizardZone_00001377": [
        "Outer Athanor",
        "外铸炼炉"
    ],
    "WizardZone_00001394": [
        "Inner Athanor",
        "内铸炼炉"
    ],
    "WizardZone_00001395": [
        "Outer Athanor",
        "外铸炼炉"
    ],
    "WizardZone_00001406": [
        "Chaos Jungle",
        "混沌丛林"
    ],
    "WizardZone_00001407": [
        "Reverie",
        "幻梦之境"
    ],
    "WizardZone_00001413": [
        "Husk",
        "空壳领域"
    ],
    "WizardZone_00001421": [
        "Port Aero",
        "浮空港"
    ],
    "WizardZone_00001438": [
        "Mandalla",
        "曼陀罗圣域"
    ],
    "WizardZone_00001494": [
        "Candy Corn Farm",
        "糖果玉米农场"
    ],
    "WizardZone_00001495": [
        "Karamelle City",
        "卡拉梅尔市"
    ],
    "WizardZone_00001499": [
        "Sweetzburg",
        "甜点堡"
    ],
    "WizardZone_00001500": [
        "Gutenstadt",
        "古滕施塔特"
    ],
    "WizardZone_00001508": [
        "Gobblerton",
        "饕餮镇"
    ],
    "WizardZone_00001513": [
        "Black Licorice Forest",
        "黑甘草密林"
    ],
    "WizardZone_00001514": [
        "Nibbleheim",
        "细啄乡"
    ],
    "Zone_00001500": [
        "Zanadu",
        "赞那都"
    ],
    "Zone_00001501": [
        "Outer Athanor",
        "外部阿萨诺"
    ],
    "Zone_00001502": [
        "Inner Athanor",
        "内部阿萨诺"
    ],
    "Zone_00001503": [
        "Sepidious",
        "大章鱼"
    ],
    "Zone_00001510": [
        "Port Aero",
        "航空港"
    ],
    "Zone_00001512": [
        "Mandalla",
        "曼达拉"
    ],
    "Zone_00001514": [
        "Chaos Jungle",
        "混乱丛林"
    ],
    "Zone_00001516": [
        "Husk",
        "外壳"
    ],
    "Zone_00001526": [
        "Karamelle City",
        "卡拉梅尔市"
    ],
    "Zone_00001530": [
        "Sweetzburg",
        "甜兹伯格"
    ],
    "Zone_00001531": [
        "Nibbleheim",
        "尼泊海姆"
    ],
    "Zone_00001533": [
        "Gutenstadt",
        "古藤市"
    ],
    "Zone_00001534": [
        "Black Licorice Forest",
        "黑甘草森林"
    ],
    "Zone_00001535": [
        "Candy Corn Farm",
        "玉米糖农场"
    ],
    "Zone_00001536": [
        "Gobblerton",
        "暴食鬼镇"
    ],
    "Zone_00001537": [
        "Gutenstadt",
        "古藤市"
    ],
    "ZoneLocName_00000255": [
        "World Gate",
        "世界之门"
    ]
}

# Locale/en-US/WorldNames.lang, same snapshot.
WORLD_RECORDS = {
    "WorldNames_AllWorlds": [
        "All Worlds",
        "所有世界"
    ],
    "WorldNames_Aquila": [
        "Aquila",
        "阿奎拉"
    ],
    "WorldNames_Arcanum": [
        "Arcanum",
        "奥创学院"
    ],
    "WorldNames_Avalon": [
        "Avalon",
        "阿瓦隆"
    ],
    "WorldNames_Azteca": [
        "Azteca",
        "阿兹特克"
    ],
    "WorldNames_Celestia": [
        "Celestia",
        "天国"
    ],
    "WorldNames_Crafting": [
        "Crafting",
        "打造"
    ],
    "WorldNames_Darkmoor": [
        "Darkmoor",
        "达克莫尔"
    ],
    "WorldNames_DragonSpire": [
        "Dragonspyre",
        "龙之谷"
    ],
    "WorldNames_Empyrea": [
        "Empyrea",
        "天城"
    ],
    "WorldNames_G14_DM": [
        "Castle Darkmoor",
        "暗夜沼泽城堡"
    ],
    "WorldNames_G14_SB": [
        "Kembaalung Village",
        "肯巴隆村"
    ],
    "WorldNames_Grizzleheim": [
        "Grizzleheim",
        "格林海姆"
    ],
    "WorldNames_Karamelle": [
        "Karamelle",
        "焦糖城"
    ],
    "WorldNames_Khrysalis": [
        "Khrysalis",
        "虫国"
    ],
    "WorldNames_Krokotopia": [
        "Krokotopia",
        "克洛克"
    ],
    "WorldNames_Lemuria": [
        "Lemuria",
        "利莫里亚"
    ],
    "WorldNames_Marleybone": [
        "Marleybone",
        "玛里伯恩"
    ],
    "WorldNames_Mirage": [
        "Mirage",
        "蜃楼"
    ],
    "WorldNames_MooShu": [
        "MooShu",
        "木须"
    ],
    "WorldNames_Novus": [
        "Novus",
        "诺沃斯"
    ],
    "WorldNames_Polaris": [
        "Polaris",
        "帕洛瑞斯"
    ],
    "WorldNames_Wallaru": [
        "Wallaru",
        "瓦拉鲁"
    ],
    "WorldNames_Wintertusk": [
        "Wintertusk",
        "冬牙城"
    ],
    "WorldNames_WizardCity": [
        "Wizard City",
        "魔法城"
    ],
    "WorldNames_Wysteria": [
        "Wysteria",
        "威斯特利亚"
    ],
    "WorldNames_Zafaria": [
        "Zafaria",
        "扎法丛林"
    ]
}

TEXT_RECORDS = {
    # Verified in installed Chat.lang (Root.wad and Locale_en-US-root.wad).
    "Chat_HeaderSay": ["Say:", "说:"],
    "GUI_00000002": [
        "Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Collect",
        "按&Icons_XKey& 或&Icons_LeftMouseClick& 收集"
    ],
    "GUI_00000015": [
        "Press &InputBindings_NPCInteract& to Collect",
        "按下 &Icons_XKey& 采集"
    ],
    "GUI_00000029": [
        "Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Ride",
        "按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 骑乘"
    ],
    "GUI_00000030": [
        "Press &InputBindings_NPCInteract& to Open",
        "点击 &Icons_XKey& 打开"
    ],
    "GUI_00000046": [
        "Press &InputBindings_NPCInteract& to Teleport",
        "按下 &Icons_XKey& 传送"
    ],
    "GUI_00000510": [
        "<center>Online Friends</center>",
        "<center>在线好友</center>"
    ],
    "GUI_00001241": [
        "Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Teleport",
        "按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 传送"
    ],
    "GUI_00001818": [
        "<center>Match This...",
        "<center>正在匹配..."
    ],
    "GUI_00002299": [
        "<center>Match This...",
        "<center>正在匹配..."
    ],
    "GUI_00002527": [
        "Dance Game",
        "宠物炫舞"
    ],
    "GUI_00005458": [
        "Press &InputBindings_NPCInteract& to Collect",
        "按下 &Icons_XKey& 收集"
    ],
    "GUI_00005465": [
        "Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Open",
        "按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 打开"
    ],
    "GUI_00005578": [
        "Press &InputBindings_NPCInteract& to Teleport",
        "按下 &Icons_XKey& 传送"
    ],
    "GUI_00005580": [
        "Press &InputBindings_NPCInteract& to Teleport",
        "按下 &Icons_XKey& 传送"
    ],
    "GUI_00005981": [
        "Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Teleport",
        "按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 传送"
    ],
    "GUI_00005982": [
        "Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Teleport",
        "按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 传送"
    ],
    "GUI_00005983": [
        "Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Teleport",
        "按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 传送"
    ],
    "GUI_00005988": [
        "Press &InputBindings_NPCInteract& to Ride",
        "按下 &Icons_XKey& 骑乘"
    ],
    "GUI_00006657": [
        "Press &InputBindings_NPCInteract& to Use Magic Raft",
        "按下 &Icons_XKey& 使用魔法艇"
    ],
    "GUI_00006706": [
        "Press &InputBindings_NPCInteract& to Teleport",
        "按下 &Icons_XKey& 传送"
    ],
    "GUI_00006707": [
        "Press &InputBindings_NPCInteract& to Teleport",
        "按下 &Icons_XKey& 传送"
    ],
    "GUI_00006708": [
        "Press &InputBindings_NPCInteract& to Teleport",
        "按下 &Icons_XKey& 传送"
    ],
    "GUI_00007103": [
        "Press &InputBindings_NPCInteract& to Collect",
        "按&Icons_XKey&收集"
    ],
    "GUI_00008359": [
        "Press &InputBindings_NPCInteract& to Enter Boat",
        "按&Icons_XKey& 进入船"
    ],
    "GUI_00010697": [
        "Press &InputBindings_NPCInteract& to Ride to Outback",
        "按&Icons_XKey&骑到内陆"
    ],
    "GUI_ChestInteract": [
        "Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Open",
        "按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 打开"
    ],
    "GUI_CollectItem": [
        "Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Collect",
        "按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 收集"
    ],
    "GUI_EnterDoor": [
        "Press &InputBindings_NPCInteract& to Enter",
        "按下 &Icons_XKey& 进入"
    ],
    "GUI_EnterNoClick": [
        "Press &InputBindings_NPCInteract& to Enter",
        "点击 &Icons_XKey& 进入"
    ],
    "GUI_FriendsOnline": [
        "Online Friends",
        "在线好友"
    ],
    "GUI_NPCInteractNoClick": [
        "Press &InputBindings_NPCInteract& to Talk",
        "按下 &Icons_XKey& 交谈"
    ],
    "GUI_NPCInteractText": [
        "Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Talk",
        "按下 &Icons_XKey& 或 &Icons_LeftMouseClick& 交谈"
    ],
    "GUI2_00000078": [
        "Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Teleport",
        "按 &Icons_XKey& 或 &Icons_LeftMouseClick& 传送"
    ],
    "GUI2_00000083": [
        "Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Collect",
        "按 &Icons_XKey& 或 &Icons_LeftMouseClick& 收集"
    ],
    "GUI2_00000398": [
        "Streamportal",
        "流式传送门(Streamportal)"
    ],
    "GUI2_00000399": [
        "<center>Streamportal</center>",
        "<center>流式传送门(Streamportal)</center>"
    ],
    "GUI2_00001319": [
        "Nanavator",
        "纳瓦托"
    ],
    "GUI2_00001320": [
        "<center>Nanavator</center>",
        "<center>导航器</center>"
    ],
    "GUI2_00001561": [
        "Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Open",
        "按 &Icons_XKey& 或 &Icons_LeftMouseClick&打开"
    ],
    "GUI2__COLLECT": [
        "Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Collect",
        "按 &Icons_XKey& 或 &Icons_LeftMouseClick& 收集"
    ],
    "PetGames_Action_Done": [
        "Done!",
        "完成！"
    ],
    "PetGames_Action_Go": [
        "Go!",
        "开始重复！"
    ],
    "PetGames_Action_Match": [
        "Match This...",
        "牢记以下顺序..."
    ],
    "PetGames_PetGameDance": [
        "<center>Dance Game",
        "<center>宠物炫舞"
    ],
    "WizardQuestGoals_00000018": [
        "Find",
        "寻找"
    ],
    "WizardQuestGoals_00000670": [
        "Press Z to Photomance a Photo of",
        "按下 Z 来拍照以取得这个目标的照片:"
    ],
    "WizardQuestGoals_00000671": [
        "Photomance a Photo of",
        "取得这个目标的照片："
    ],
    "WizardQuestGoals_00000672": [
        "Press Z to Photomance",
        "按下 Z 来拍照"
    ],
    "WizardQuestGoals_00000748": [
        "",
        "Photomance"
    ],
    "WizardQuestGoals_Explore": [
        "Go To",
        "前往"
    ],
    "WizardQuestGoals_Gather": [
        "Gather",
        "聚集"
    ],
    "WizardQuestGoals_Get": [
        "Get",
        "获得"
    ],
    "WizardQuestGoals_Kill": [
        "Defeat",
        "击败"
    ],
    "WizardQuestGoals_KillCollect": [
        "Defeat and Collect",
        "击败并收集"
    ],
    "WizardQuestGoals_TalkNPC": [
        "Talk To",
        "拜访"
    ],
    "WizardQuestGoals_UseItem": [
        "Use",
        "使用"
    ]
}

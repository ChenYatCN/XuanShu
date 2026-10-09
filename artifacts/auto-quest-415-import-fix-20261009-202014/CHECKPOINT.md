用户要求：只补齐主程序现有接口导入，核对移植外部名称，真实主程序命名空间验证独立/任务内对话及坐标/好友跟随，再独立打包、新文件名和哈希。
已保存 XuanShu.py 修改前字节和源文件哈希，已加一行现有 automation_owner 导入。尚未测试、尚未新打包，不改变任何任务流程或恢复分支。

## 续接 01a11fde-6c49-7af3-8298-6cca23b8e2c4 最新用户修复请求
用户实测旧候选立即NameError automation_owner；最新要求仅修主程序导入、核对其他移植外部名称，针对真实主程序命名解析验证独立对话/任务内对话/坐标跟随/好友传送，再独立新文件名打包及哈希。不能只AST抽函数手注依赖，不允许任何任务行为或恢复分支变化。用户已授权修复+测试+打包，无需确认。无item/history/notes工具提供id，用户文本即最新任务。

已做：snapshot.json记录当前HEAD/4个源hash，XuanShu.py.before原字节。生产源码唯一修改：在src.auto_pet import nomnom后一行加 from src.automation_ownership import automation_owner（保留CRLF）。尚未新增测试，尚未打新包。其余源码绝对不动。

真实主程序import已成功（不是AST片段）。为避免迁移/写用户设置，用当前快照/probe-appdata设置进程APPDATA，仍真实XuanShuSettings和所有真实模块导入。沙箱TempDirectory系统Temp写入/清理会PermissionError，不再使用系统TEMP或碰用户设置，测试临时目录用artifact根。实际async测试须require_escalated因为Windows sandbox asyncio socketpair卡住，先前授权范围允许直接执行。

main()被logger.catch装饰，必须inspect.unwrap(app.main)才能看到原co_consts。实际import后 initial-name-audit.json已有结果：
- dialogue_loop code path main/dialogue_loop，co_freevars=()；LOAD_GLOBAL唯一未初始化walker。
- run_questing_worker path main/questing_loop/run_questing_worker，freevars=(dialogue_loop,party_enabled,roster)；无未解析global。
- _follow_quester_session path main/questing_loop/_follow_quester_session，freevars=(change_party_equipment,members,prepare_quester_name,remove_party_status,restart_quest_worker_after_probe,update_party_status)；唯一未初始化global walker。
walker是在实际main启动时赋值的游戏ClientHandler，不是漏导入。新验证要确认该真实初始化存在，再用模拟walker游戏状态替代。不要往app.__dict__手动加入automation_owner。

选定新测试方案待实施：tests/test_quest_runtime_names.py，通过importlib真实import整个XuanShu（隔离APPDATA、不调用run/main，不操作实际游戏）。从真实加载的 main.__code__ 子code constants查找两个/三个生产入口，types.FunctionType(code, app.__dict__, closure=...)，函数__globals__必须is真实app.__dict__。只模拟实际游戏启动状态walker、上述合法闭包服务，不新建含手动接口依赖的ns、不AST重编函数。可patch已存在游戏IO helpers（Quester模拟读UI、is_visible/is_free等），但先审计真实bindings存在。automation_owner和gather_owned使用实际主程序导入，assert owner is src.automation_ownership.automation_owner。
- 独立dialogue_loop([client])：advance可见，decline不可见，实际ownership+gather+Keycode；send_key设置goal_id更新/event。用asyncio.wait(task,event.wait)FIRST_COMPLETED检测任务早期NameError， finally cancel+gather释放锁。
- 内部run_questing_worker(client)：real dialogue_loop闭包、party_enabled True/roster。仅模拟app.Quester IO，auto_quest等client.questing_statusFalse后结束；真实内部gather和dialogue入口发SPACE，fakekey令client停止，可 awaitwait_for parent 2sec。测试真实name解析，不启动游戏backend。
- real follower坐标：walker.clients=[q,h], members=[q,h]闭包，fakeclientloading/battlefalse/entityflagfalse、body XYZ0和2000、same-area True、is_free True、fakeQuester pendingmodalFalse。首tick0.5sec后真实owner坐标follow teleport触发event，停止hitter。
- friend：不同zone+same-areaFalse，quester.wizard_name Wizard，fakeQuester objective DefeatBoss，friend icon default{}，free true，UI窗False，close_friend_windows现有bindingmock。不虚构clock，真实稳定等待约2sec后生产friend分支进入actualownership，mock app.teleport_to_friend_from_list只模拟游戏IO，trigger_event后cancel/drain。App mouse_handler AsyncMock上下文。Timeout4sec。logger可以patch现有logger减少输出，但不得缺owner注入。
- 对移植58个旧Quester方法、相关新共享helper、teleport的collision/_walk、utils close_npc检查dis LOAD_GLOBAL names，实际模块dict+builtins解析所有；子code递归，类属性LOAD_ATTR不用误算。app三入口只允许合法启动state walker尚未init，验证main真实赋值。
- 负向防回归：patch.dict(app.__dict__) context中删除automation_owner，audit必须检测、独立dialogue入口要抛NameError（证明新增测试不能被手注依赖掩盖）；正常测试恢复dict原导入。好友分支吞Exception所以不要expect原NameError直抛，应由global审计捕获。

后续：写新tests，运行4入口+nameaudit+negative（直接模块runner .venv/Scripts/python.exe -X utf8 -m artifacts.run_auto_quest_targeted_checks tests.test_quest_runtime_names > currentfolder/runtime-entry-tests.log，用escalated）。无必要重跑其它任务行为测试，生产只import新增1行。新打包现有XuanShu.spec独立dist/build目录，不覆盖旧包；文件名建议XuanShu-autoquest-415-base-importfix-r1-20261009.exe/zip，旧文件hash70287493...绝不重新链接为新。新包CArchiveReader内root XuanShu实际code含导入（from src.automation_ownership automation_owner）；现有spec optimize=2，结构递归fingerprint代码字段(忽略co_filename)或compile当前source optimize2对比内嵌code，不能marshal.dumps直接比较(引用intern差异)。同check src.questing/utils/teleport hash未变。之前zip代码fingerprinting已有tool过程，上一包的packed-code-verification.json在auto-quest-415-base-20261009-163300可参照。

前任务已经完成无须重做：普通4.1.5基底、18核心checks、新版特殊链不接普通、snapshot91files，原候选在旧folder package。当前仅import error修复，不能继续改baseline行为。新报告承认根因和旧tests手注掩盖，明确四入口真实globals验证（游戏IO模拟）；新EXE、ZIP、SHA256+简洁链接交付，尚未实游戏。记忆本轮读MEMORY.md3-4用于dirtyworkspace/验证边界，final一块memory引用最后。

## 本轮导入修复完成 01a1209e-f262-7b23-b085-773381b169f6
生产唯一修改已验证精确为 XuanShu.py 第33行新增 existing automation_owner import；questing/utils/teleport 哈希与本轮快照完全相同。没有修改任务行为或恢复分支。
新增 tests/test_quest_runtime_names.py，真实import整个app(隔离APPDATA)，使用已加载生产code objects和真实app.__dict__，不AST重编、不手加automation_owner/gather_owned；模拟walker启动state、闭包服务、游戏IO。四入口(独立对话/任务内对话/坐标跟随/好友跟随)、58基底方法+5直接helper+3其它helper global审计、负向删除import验证合计8测试全通过。初次审计test误写不存在_maybe_log_mainline_progress，修成现有_run_mainline_finder后通过，仅test错误无生产变更。
独立使用原spec打包成功(100sec)，build.log；没有启动新EXE或操作游戏。
verify_and_package.py检查生产精确diff、3模块原hash、6个内嵌code与当前source optimize2结构一致、主程序import/from/store均真实存在，旧EXE hash不变。ZIP testzip和ZIP内exe hash通过。
新EXE package/XuanShu-autoquest-415-base-importfix-r1-20261009.exe，SHA256 ff8dc0176df599f8688f471288809ea087ed277f4e93fb274b99cf813e67ee1c。
新ZIP XuanShu-autoquest-415-base-importfix-r1-20261009.zip，SHA256 26b166b1ec42a537a87875cd7b65cea34e22702b050afcd0bff0fc640c7fc323。
delivery-manifest.json保存路径和hash；package/修复说明.md承认旧片段测试手工补名称掩盖漏导入，列出真实namespace验证边界。旧候选保留，未游戏实测。用户范围全部完成，下一步仅最终交付新EXE/ZIP及哈希、测试结果和未游戏验证声明。

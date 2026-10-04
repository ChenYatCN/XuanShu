# Current pending reply: why automatic Quest Finder stopped

Window 01a0f583-70c8-73f0-b3a9-40ab06be3079. User item IDs unavailable. Latest user requests explanation only: '这里怎么不会自动任务搜寻了？' with log C:/Users/ChenYat/.codex/attachments/1eb719eb-ae72-4759-af54-65c860046ebd/已粘贴的文本.txt. Do not implement a fix unless subsequently authorized. Already sent commentary: inspect logs to distinguish known mainline / guard / failure.

Diagnosis ready for concise final, no further search needed:
- Log line793 at17:32:56 p1 novus mainline88/88.
- At17:48:13 Quest ID142707813505353448, language key QuestTitle_71CCD, English Of Unknown Origin, game mainlineTrue -> unmatched. Scoped rg in src/data/mainline_quests.json found no entry for that exact title/key/Chinese 未知起源.
- At17:48:21 explicit warning '当前任务主线身份暂未确认：Of Unknown Origin；保留追踪并暂停任务搜寻。' This is our recent safeguard treating unindexed mainline as uncertain, preserving tracking.
- At17:48:47 switches to Quest Finder ID60216001, languagekey任务搜寻, flagFalse. Log doesn't prove who switched; don't assert user/manual source.
- At17:48:51 finder starts but _restore_owned_mainline aborts with '已接任务存在未能确认的主线身份；保留当前追踪，暂不执行任务搜寻'. Guard scans ALL owned quests before UI; any rowNone and await quest.mainline() !=False raises. Thus unindexed Of Unknown Origin still owned blocks Finder even when tracking Finder.
- At17:48:59 and17:49:02 worker crashes _maybe_recover_lemuria_navigation src/questing.py358 because zone_name() returnsNone and code calls zone.startswith('Lemuria/'). Retry loop recreates worker. This is separate additional bug; explain not main original guard cause.
- At17:49:16/31 Of Unknown Origin tracked again remains unmatched. Restart won't fix source guard.

Source inspected sufficient: src/questing.py _maybe_recover_mainline around2245: if identity[0] and identity[3] isNone and identity[4] isnotFalse -> warnonce then returnTrue noFinder. _restore_owned_mainline around2430 raises on any unindexed game-mainline. _maybe_recover_lemuria_navigation reads zone then not zone.startswith unguarded. No mutations for current diagnosis.

Suggested final explain concrete timeline, acknowledge overly broad protection added for Monkey Business now suppresses valid Finder due to unrelated owned quest. Need change protection to check actual target mainline instead of treating any unindexed mainline as perpetual block, and guard missingzone. Do not fabricate classification of Of Unknown Origin (flagTrue but absent index); no claim definitively sidequest.

Earlier edits already delivered and must preserve: chat busyoverride and tests; Monkey Business Novus ID/key correction; starredLeftMainline/RightMainline-only Q card candidate filtering; mainline UI combined cyan status, current_quest_party identityresolution, confirmed progress retained through read/unmatched gaps (11tests passed lastturn); 3dialogs customsharedtitlebar and DWMrounded preference; combatstyle logs recognize groupedtask; partyjoin7sec flagcleanup and failedsameinstance rescue (45distinct tests passed; one dungeon gate baselinefail independently reproduced on committed HEAD). Recent task UI/progress/entry checkpoints in artifacts. Dirty repo preserve all. No builds/livegame checks or agents. Latest sourcecwd D:/XuanShuTool/XuanShu-Project/XuanShu (Git root here per blankprefix). Memory searches done earlier not relied on for this diagnosis; don't cite unrelated memory.

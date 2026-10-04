# Outback story recovery — completed source implementation

Window: 01a0f6e5-3278-7991-a7b3-22c0f000a86e. User item IDs unavailable.
Scope: Wallaru/WL_Z02_Outback, Stop and Goanna footprint goal without effective quest navigation. Preserve all prior dirty changes. No agents, packaging or live-game control.

Confirmed from mainline index and installed Locale_en-US-root.wad:
- Quest Language Key: QuestTitle_17D895; indexed Wallaru 15/83; English Stop and Goanna, Chinese 停下和戈安娜.
- Goal Language Key: WizQst17D895_00000007; Dundara's Tracks / 邓达拉的足迹.
- Actual numeric Quest ID is not available from screenshots/catalog; never derived from language suffix. Runtime logs actual Quest ID and Goal ID.
- Catalog dialogue includes routing Goannas in this same quest. Therefore recovery permits a stable, indexed new goal within the same quest, or a stable next indexed mainline, after dialogue ends.

Implemented in src/questing.py and XuanShu.py:
- Exact zone, indexed quest/key and exact goal-key gates. Three missing-guide observations over >=1.5 seconds. A valid target is respected; a visibly present HUD with no arrow/distance can invalidate a stale XYZ. A normal arrow+distance blocks recovery.
- Respect existing first-entry/solo probe before claiming outback_story. Assigned hitters excluded.
- Fixed TP XYZ(-16788.000,-6939.999,119.999), at most two attempts per saved quest stage; retry only if still away from the trigger. Arrival log requires <=100 distance.
- Existing recovery lock plus short automation input ownership. No X/Q/END. Existing shared dialogue step runs under story owner at its existing pacing; external dialogue worker yields while owner active.
- Once dialogue appears or readable QuestID changes, never TP again. Do not rematch quest during dialogue. Require dialogue/loading/battle quiet and new indexed quest or genuine new goal stable for three seconds before clearing pending state and resetting group-mainline sync.
- Bounded initial transaction <=150 seconds. Trigger wait 10 seconds per TP, then bounded dialogue/update wait. Failure warns once and preserves no-repeat hold on client across Quester recreation; late dialogue/progress can still finish the hold. Finally releases recovery lock. Input cancellation cannot rearm TP.
- Solo/group entry and ordinary TP priority; mainline Finder, group-mainline sync, friend TP, follower rescue, and no-progress watchdog respect story hold. Existing solo follower policy remains first.

Verification:
- 22 distinct Outback-specific tests passed (21-test run plus newly added stale-XYZ test); additional focused checks passed after final navigation changes.
- 118 related regression tests passed across Finder, owned tracking, mainline group sync, NPC acceptance, solo followers, Bumbles pet and Lemuria navigation.
- Two new follower tests passed: pending story after lock release and existing solo-policy priority. Total 142 distinct passing tests.
- Syntax and scoped diff checks passed.
- Test additions: tests/test_outback_story.py and tests/test_quest_party_solo_follow.py.
- No build or actual game testing performed; cannot claim deployed EXE behavior.

All implementation/verification work complete. Last user steering requests Astra Low. Available tools cannot change this chat's model; conveyed model-selector action. No need to spawn a new chat/agent. Ready to deliver implementation, identifiers, file links, and live-test limitation.

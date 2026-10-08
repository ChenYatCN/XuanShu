# Completed — target NPC dialogue priority, 2026-10-08

Window 01a11998-73a7-7eb0-89ac-23f164c50b6f. User message IDs unavailable. Repo D:/XuanShuTool/XuanShu-Project/XuanShu, dirty nested Git checkout; preserved existing changes. No agents used.

User explicitly requested 更改 of the agreed priority: opened formal dialogue stops movement/reentry and advances; valid nearby target NPC talk prompt stops movement and sends X; otherwise normal approach/reentry continues. Only current client, no unrelated group changes. Friend-list Esc/name-matching diagnosis is separate and was NOT authorized for implementation in this request.

Source changes only src/questing.py:
- auto_quest_solo rechecks existing _quest_dialogue_blocks_movement before trigger reentry, then checks existing quest_interaction_ready + talk prompt to call existing handle_npc_talking_quests(client, [client]) before any ordinary quest movement. Clears only own trigger-reentry state, does not turn off questing. Rechecks formal dialogue immediately after movement completes, before zone/interaction follow-up.
- move_until_quest_interaction reuses is_free_leader_questing in the initial check and watcher. Opened dialogue/forced narration/loading/battle ends movement; existing local movement+watcher cancellation/drain unchanged. Matching prompt still uses existing name/location/distance checks.
- _reenter_quest_trigger stop callback stops and clears own state when nearby target prompt matches and interaction_kind is talk. Preserves use/non-dialogue prompt final-walk behaviour and all existing task/zone/refill/battle/recovery guards. Existing owner release finally preserved.

Tests updated: tests/test_quest_interaction_priority.py, test_quest_trigger_reentry.py, test_party_dungeon_interaction.py. Existing helper fixture refactored only for worker priority tests; old forced-final-walk talk test updated to new requested policy, non-dialogue test preserves old behaviour.

Verification: offline unittest 5 relevant suites (interaction priority, trigger reentry, party dungeon interaction, NPC retry, interaction prompts) first batch 70 tests: 69 pass, 1 new-test assertion failed because existing party movement correctly invokes both p1 and p2, not only one call. Corrected test assertion to require p1's own move call, source unchanged; reran failed test + new formal-dialogue-during-reentry test: both pass. Thus 71 distinct targeted tests passed across batches, not a fresh single full-suite pass. Scoped git diff --check passed (only LF/CRLF notices). No EXE build/install/live game verification. No memory facts used for this change. Source-only handoff ready; no required work remains.

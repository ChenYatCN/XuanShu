# Elephant March mainline recognition / ride prompt

Window 01a1160e-45fa-7dd0-8906-8852ea8f63d7. User asks why the Stone Town boat
ride prompt is not pressed, then explicitly asks to fix repeated searching for
the starred 大象游行 quest. Source/log diagnosis and scoped index fix complete.

Evidence: 67a0c770 and 54b0eb1f logs show The Silver Queen (QuestTitle_80B3D)
recognized at quest 83, then Elephant March with ID 121315715316472848,
key QuestTitle_80B3E, game mainline=True, rejected as unmatched. Finder then
switches to Bad Vacation, pauses or restarts recovery. Screenshot confirms
starred 大象游行, Talk to Rakstede Steelhorn in Stone Town.
Index quest 84 had workbook label Elephant Queen and empty ID/key/Chinese/aliases.

Verified quest-order source: Final Bastion Zafaria guide search result explicitly
lists 83 The Silver Queen / 84 Elephant March / 85 Orders from the Queen:
https://finalbastion.com/wizard101-guides/w101-quest-guides/zafaria-main-quest-line-guide/
Direct fetch returned 403; indexed search returned the numbered list. No other
facts inferred from failed wiki fetches or irrelevant search results.

Changed ONE row of src/data/mainline_quests.json: preserved workbook label and
148 total, added verified ID/key/Chinese plus Elephant March alias and provenance.
No broader flag-based bypass; unindexed/side quests retain existing protections.
Boat ride branch already sends X for a visible in-range prompt (<750u); no new
unconditional X action was added. Auto-dialogue alone does not initiate boat rides.

Tests: 42 mainline progress/finder checks passed, including actual identity lookup,
no Finder for Elephant March, cooldown reset, and isolated solo ride branch X at114u.
An existing AsyncMock-only fixture emitted a nonfatal unawaited-coroutine warning;
all tests succeeded. git diff --check passed and index diff is exactly one row.
No game, installed EXE or build verified. Restart/rebuild to reload cached index.
Prior 838u no-path issue remains separate, unresolved, and not claimed fixed here.

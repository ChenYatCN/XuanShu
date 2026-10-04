# All quests prerequisite completed

User request: before restoring an owned mainline or selecting Quest Finder, click the screenshot's All Quests filter. Current window 01a0fb5c-d6ba-7861-945c-fadcaacc5b87; user item 01a0fb5b-f130-73a3-b59d-46cb2d245c48.

Implemented in src/questing.py: both _restore_owned_mainline and _select_quest_finder wait for the questbook and existing all_quests_sort_button_path (QuestLogAllButton), click it with the existing click_window_by_path, then wait 0.3 seconds before scanning cards. Missing button times out before scanning; existing finally closes the book. Preserve all unrelated dirty changes.

Validation: tests.test_owned_mainline_tracking and tests.test_mainline_finder: 37 tests passed. Tests assert the filter click precedes card scanning, including an already open book, and cover missing-button failure. Scoped git diff --check passed. No EXE build or live-game verification. No work pending within this request.

# Mainline status UI completed

User requested current world-local mainline xx/xx progress in the hotkeys tab, combined with the existing cyan automatic-quest party status box, with text contained inside the frame. Current window 01a0f583-70c8-73f0-b3a9-40ab06be3079; user item IDs unavailable.

Implemented: mainline_progress.py caches concise confirmed progress per client using the existing index and world totals. Clears stale values and identity cache on read failure/invalid ID. XuanShu.py publishes changed progress for active automatic-quest clients through existing UpdateWindow. tab_hotkeys.py combines progress above party status in the existing 230px cyan QLabel, wraps text, computes minimum height using heightForWidth, preserves each independently updated section. main.py routes both tags to the existing hotkeys exports. No extra progress frame.

Validation: 18 existing targeted UI/progress tests passed; 2 targeted tests then passed after adding combined-section/height verification and cached-progress assertion. In-memory syntax checks of all 4 modified source files passed. Scoped diff check passed. Qt widget preview rendered with Microsoft YaHei and inspected: two cyan lines contained in existing rounded frame, saved artifacts/MAINLINE_STATUS_UI_20261001.png (test fixture, not running app). No build or live-game check.

Previous mainline fix remains: screenshot-confirmed Novus Monkey Business ID/key index correction, preserve unresolved tracked mainline before Finder, visible LeftMainline/RightMainline candidate filtering, stop on target hit. Preserve unrelated chat edits. No additional work pending for current UI request.

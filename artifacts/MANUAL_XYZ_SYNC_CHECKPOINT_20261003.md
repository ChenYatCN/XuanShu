# Daily manual XYZ sync gate removal COMPLETE

User screenshot4243dccd requests removing the manual XYZ missing-same-instance gate; then explicitly confirms automatic questing must remain unchanged, this is for daily manual use. Current window01a0fce3-40e8-7d23-9841-ade35e4b9d7c, user item IDs unavailable.

Only task logic change: XuanShu.py xyz_sync no longer calls clients_share_live_area or compares destination zone to source. Copies source XYZ/yaw directly for selected background peers; no global-ID/entity/duel/potion-return proof required. Existing actual refill/recovery-owner guards, loading guards and source's own zone-transition-during-read guard retained. Same A/D post-sync and source fallback when no foreground retained; no inputs to the source itself.

Automatic helpers/clients_share_live_area, follower battle sync/instance entry/rescue, potion returns and Callisto adaptation are untouched in this task. Previous shared-battle helper remains active for automatic operations. No zone/realm/instance switching is performed by copying coordinates; manual caller is responsible for appropriate destination map.

Updated prior manual-gate test and added tests for absent proof, differing target zone, no target entity/duel reads, source transition still stopping, no-foreground peer selection and A/D. 40 tests across tests.test_party_area_proof + test_hotkey_runtime_scope + test_quest_party_battle_sync PASS. Scoped git diff --check PASS with only LF/CRLF notice. No further tests needed. No build/live-game input performed; current executable is not modified by this request. Preserve all other dirty work.

NEXT: final acknowledge manual-only scope, unchanged automatic behavior, verification and rebuild limit. Registry quickpass had no relevant XYZ/manual-sync entries; no memory facts used or updates made.

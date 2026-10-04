# Auto pet energy exhaustion stop COMPLETE

Current window 01a0fdbb-555f-7e80-8802-54d5dd893538. User item IDs unavailable. User explicitly clarified: exit pet minigame and stop only that client's auto pet, not Wizard101 process. No packaging or live-game control requested.

src/auto_pet.py: reuse existing per-round picker energy/cost read and btnBack exit. On energy < cost set client.auto_pet_status=False and log current energy/cost. Existing feeding-status and owned dance-hook cleanup retained. auto_pet questing entry skips disabled clients so another client's level-up cannot navigate a stopped client back and leave it waiting for a stopped worker.

XuanShu.py: per-client auto_pet_loop ends when its flag is false; clears feeding status even on a legacy restart. Peers remain alive under existing gather_owned/scoped worker management. Legacy global enabled state resets only when all workers end; legacy toggle uses task.done() and consistent per-client assignment so a completed run can be manually restarted and mixed flags can be stopped. Scoped group cleanup preserves an exhaustion-disabled flag rather than restoring an old True value.

tests/test_auto_pet_energy_stop.py covers low energy in skip/dance modes, exact/above-cost energy, next-iteration stop/restart, disabled legacy restart, legacy/scoped peer isolation, scoped flag/group cleanup and re-enable, completed legacy toggle, and questing entry refusing a stopped client.

25 tests across energy_stop + auto_pet_localization + hotkey_runtime_scope passed in first run. After feeding-status exit addition, disabled legacy restart and new legacy peer case passed (2 tests, one repeated); after questing entry guard the new guard test passed. 27 distinct related tests passed in total; scoped git diff --check passed (LF/CRLF notices only). No build or live game verified. Two multi-file apply_patch calls reported context failures after their first source file had applied; current source was inspected and remaining test patches applied separately. Preserve unrelated dirty work.

NEXT: concise final delivery; no further verification required without new evidence.

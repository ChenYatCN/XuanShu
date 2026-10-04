# X hotkey input conflict - completed 2026-10-02

## Superseding follow-up: same plain X passes through - COMPLETE

User explicitly rejected combination keys and stated no chat-state gating is needed: Enter activates foreground chat; background clients may still receive ordinary X. Current window 01a0f8a7-08fe-7722-8bc6-36087ba991b3, latest request item ID unavailable. Earlier Alt+X implementation below is superseded.

- Default/reset/INI binding restored to bare X; forced Alt migration and GUI rebind substitution removed. Existing customized bindings and unbound state remain intact.
- XuanShu.py _is_passthrough_x identifies only x_press with bare X. enable/disable/rebind store this binding without RegisterHotKey/UnregisterHotKey; other keys/modifiers retain existing listener behavior.
- Added always-on passthrough_x_loop under all_tasks lifecycle, reading GetAsyncKeyState high bit every 10 ms and forwarding once per physical press edge. No input interception, WM_CHAR generation, Enter handling or chat gating. Initially-held key ignored; disabled/unbound states ignored; errors do not terminate the loop.
- x_press_hotkey(passthrough=True) gets the actual foreground hooked client (never the stale fallback), lets the physical event reach it naturally, and calls existing mass_key_press with foreground=None and only background peers. GUI button/custom global bindings retain existing all-client sending behavior. Passive loop has existing cancellation lifecycle.
- 21 affected tests PASS: 11 X-specific tests plus existing direct multi-client callback and hotkey-group UI. Tested settings/default/rebind preservation, no global plain-X registration, held/repeated presses, focus/disabled/unbound, forwarding failure recovery, custom modifier behavior, rebind transitions. Targeted diff check PASS.
- No EXE build or actual game input test performed; source request complete. Existing saved custom Alt+X is not forcibly overwritten; bare X can be selected/reset in GUI. User's screenshot had bare X, which now remains bare X. All unrelated edits preserved.

Current window 01a0f8a7-08fe-7722-8bc6-36087ba991b3, user request item ID unavailable. User reported ordinary X interaction and typing X unavailable in hooked game clients (screenshot a4d45649).

Cause verified from current implementation: default x_press was bare X, enable_hotkeys/rebind uses wizwalker.HotkeyListener, whose _register_hotkey calls Windows RegisterHotKey (installed .venv/hotkey.py:435). X is consumed as a global hotkey. Mass key forwarding sends game key events and does not preserve ordinary text input.

Completed scoped changes:
- src/settings_manager.py default/reset x_press -> Alt+X. Load migrates existing bare-X x_press binding only, preserving custom modified/different keys and unbound states. Legacy INI x_press=X import uses Alt+X.
- src/gui/actions.py do_rebind ensures bare X for this action becomes Alt+X; saved binding, displayed label and backend command match. Other actions and custom modified keys unchanged. Multi-client X callback stays unchanged.
- tests/test_x_press_hotkey.py covers defaults/reset, existing migration and unrelated settings preservation, customized/unbound preservation, INI, GUI persistence/dispatch, actual backend register/unregister functions with mocks (no real global registrations).
- 16 targeted tests PASS: new six tests, directly affected existing multi-client callback test, hotkey-group UI tests. Targeted diff check PASS.

No game interaction, settings mutation outside temporary test files, live hook validation or EXE build. Current source repair complete; EXE needs rebuild and restart for migrated bindings to load. All unrelated dirty work preserved.

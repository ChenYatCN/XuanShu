# Window Theme and Status Overview Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Preserve the current dirty checkout; do not commit or copy HEAD over existing work.

**Goal:** Match application-owned windows to XuanShu's existing theme and make the read-only status overview readable and identity-safe.

**Architecture:** Reuse compute_styles and the existing six theme colors. Theme native dialog captions centrally using Windows caption attributes, retaining native resize, drag, close, modal and keyboard behavior; preserve existing custom titlebars. Add a read-only overview table with a shared-model frozen feature column and two-line headers. Read existing account_nick/stable_id metadata, and accompany the unchanged state booleans with identities so p-number reuse cannot apply an old client's state to another.

**Tech Stack:** Current Python / PyQt6, existing Win32 DWM integration, unittest and native Windows test windows. No dependency changes.

**Spec:** User request of 2026-10-25 in this chat. Preserve state acquisition/control logic and refresh cadence; modify only presentation and necessary identity metadata. No game launch, game window, resolution, DPI or account-data changes.

## Review Focus

- Same title reused for another stable identity, client reordering or disconnect, and stale status snapshots must not produce another client's states.
- Missing, null or malformed state is unknown; disconnected clients are labelled explicitly while the overview remains open.
- No new toggles or editable feature names; preserve all existing control callbacks.
- Long names, one versus many clients, small windows, 150%/200% display scaling, and keyboard focus remain usable.
- Standard dialogs and messages retain native operations; native OS file/credential pickers are listed separately, not replaced.

## Task 1: Shared theme coverage

- [x] Add failing tests for default buttons/inputs/menus, focus/disabled states, caption theme events and preserved flags/modality.
- [x] Extend compute_styles; add a shared window caption theme controller and install/update it in main/theme. No duplicate per-dialog color schemes.
- [x] Audit local styles that override the shared theme, and patch only necessary theme connections.
- [x] Run focused theme/dialog tests; open representative application dialogs and record titlebar and focus behavior.

## Task 2: Identity-safe readable status overview

- [x] Add failing tests for account headers, bool/unknown/disconnected text, same-title identity replacement, reordering, read-only semantics, widths and frozen feature column.
- [x] Add a focused status overview table module and connect it to the existing hotkey UI. Keep selector/control code and row badges unchanged.
- [x] Pass existing hooked metadata and identity information through current GUI messages without changing state calculation or timers.
- [x] Update old overview assertions to the requested text and theme behavior. Run related regressions and static scope checks.

## Task 3: Native display verification and review

- [x] Open affected source UI windows with temporary settings and fake clients at current 150% and higher 200% scaling. Exercise hover, pressed, selected, disabled, focus, Escape, close and resize.
- [x] Inspect actual Qt content screenshots for overview, manager and representative dialogs; native caption screenshot check is permission-limited and explicitly recorded in VERIFICATION.md.
- [x] Verify multi-client updates and exits, long names, no control commands on table interaction, and preserve all non-UI source behavior.
- [x] Obtain a read-only whole-change review using the executing-plans review gate, fix supported findings, and report limitations without claiming every UI was visually verified.

## Decisions and execution record

- Ruling: Native caption colors use existing titlebar_bg/text_color and keep native flags rather than replace resizable dialogs with a second custom window framework. Existing frameless titlebars remain intact. This avoids losing original resize or keyboard operations.
- Ruling: Disconnected columns are retained only for the currently open overview session, and disappear on a fresh opening. They never become controls.
- Ruling: Existing stable_id association is authoritative. No account inference by list order. Missing/ambiguous identity falls back to the client label and unknown state rather than guessing.
- Baseline saved under Back/window-theme-overview-20261025/before. No commits, pushes, packaging or system-setting changes in this task.

## Final execution record

- Shared theme and integrated overview implemented; related suite 109/109 PASS. Independent final review found two Important issues (frozen-column keyboard navigation and ordinary checkbox visibility); both reproduced RED, fixed GREEN, then the whole related suite passed.
- Native Windows Qt content verified at DPR1.5 and DPR2.0, 35 screenshots each; errors empty and no LaunchInstance commands. Original exec/show entry points were exercised and Escape used to close dialogs.
- Ruling: native captions retain original window frames. DWM setters succeeded; Computer Use denied access to Python windows and getter35/36 returned E_INVALIDARG, so native-caption visual proof remains unavailable and is reported, never substituted with a content-only grab.
- Ruling: no commit/push/package requested; preserve dirty checkout and isolated before/ verification artifacts rather than create a separate checkout that lacks current user work.
- Ruling: two existing preference subtest errors reproduced against before-task source; leave unrelated initializer fixture untouched. Other concurrent ibao_core/test_ibao_character_selection changes are not part of this task.
- Full change list, reviewer dispositions, actual entries and limits: Back/window-theme-overview-20261025/VERIFICATION.md.

# ControlFreeChat offset repair - COMPLETE

Window 01a10301-61d9-7932-b08c-107aeaafe5c4. User reported 还是不行 with Oct5 00:36 log and screenshot C:/Users/ChenYat/AppData/Local/Temp/codex-clipboard-9563570c-0374-41da-8922-851c2bc513e7.png. User item ID unavailable. Continues prior authorized chat repair, no computer-use/build/live input/agents/commit.

New log establishes failure specifically in read_control_text on ControlFreeChat, not a generic dependency fallback: old +736 text header has pointer=7, length=256, capacity=2160565320096. A +712 string places capacity at +736; the old reader was treating that capacity as pointer. Original log does NOT capture actual +728 length. Previous Oct4 fix guarded invalid pointers and removed maybe_text fallback, but did not fix FreeChat type-specific layout. Do not claim previous guard resolved this live failure.

Changes: src/window_text.py now includes ControlFreeChat alongside ControlText/ControlList for existing +712 text layout. Other types retain +736; all prior inline/heap validation and no-fallback behavior retained. src/chat_translation.py only clarifies hidden chatEditContainer/chatEdit prompt to expand game chat INPUT, not merely chat log; no automatic Enter/opening or visibility bypass.

tests/test_chat_control_text.py fixture now uses +712 for FreeChat independently of source; one new logged-values test reproduces exact reported exception before source fix (same text address, length256, capacity2160565320096, pointer7). Correct empty draft length is synthetic, explicitly not claimed captured by log. Also covers unchanged +736 ControlButton heap layout. One new hidden-input/visible-log test ensures no clicking or sending. All existing real-reader empty->heap once-send, draft-preservation, pointer-invalid no-input, offline/UI/readback tests preserved.

Verification:47 tests passed across chat_control_text, popup_text, chat_manual_send, chat_translation, chat_translation_ui after source fix; then additional hidden-input test passed independently. Total48 distinct relevant methods. Source imported/compiled in test run; scoped git diff --check PASS, LF/CRLF notice only. No required further source work/verification; ready for concise Chinese final: specific FreeChat offset corrected, 48 tests passed, need own rebuilt EXE and expanded game input for live retest; no live success claimed.

Preserved all unrelated dirty work. No memory lookup/facts used this self-contained follow-up. Earlier Oct4 checkpoint is historical; this file supersedes its ready/fix claims for this failure.

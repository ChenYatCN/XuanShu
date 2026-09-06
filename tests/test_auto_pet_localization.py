import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode, XYZ
from src.paths import pet_feed_window_visible_path

from src.auto_pet import (
    _first_number,
    _is_dance_game_title,
    _is_dance_go_prompt,
    _open_pet_game_window,
    dancedance,
)


class AutoPetLocalizationTests(unittest.TestCase):
    def test_dance_game_title_accepts_english_and_chinese(self):
        for value in (
            "Dance Game",
            "宠物炫舞",
            "寵物炫舞",
            "舞蹈游戏",
            "舞蹈遊戲",
        ):
            self.assertTrue(_is_dance_game_title(value))

    def test_go_prompt_accepts_markup_and_chinese_punctuation(self):
        for value in (
            "<center>Go!", "<center>开始！", "開始!",
            "<center><color;00ff00>开始重复！</color></center>",
            "開始重複！",
        ):
            self.assertTrue(_is_dance_go_prompt(value))

    def test_watch_and_done_prompts_do_not_trigger_input(self):
        for value in ("正在匹配...", "完成！", "Watch!", "Done!", None):
            self.assertFalse(_is_dance_go_prompt(value))

    def test_rounds_submit_once_for_each_input_prompt(self):
        import asyncio

        async def exercise(go_prompt, already_go):
            client = AsyncMock()
            action_window = AsyncMock()
            action_window.is_visible.return_value = True
            prompts = [] if already_go else ["正在匹配..."]
            prompts.extend([go_prompt, go_prompt])
            for _ in range(4):
                # Previous input prompt lingers, then the next demonstration.
                prompts.extend([go_prompt, "完成！", "正在匹配...", go_prompt, go_prompt])
            action_window.maybe_text.side_effect = prompts
            moves = ["W", "WD", "WDS", "WDSA", "WDSAW"]
            client.hook_handler.read_current_dance_game_moves.side_effect = moves
            with (
                patch("src.auto_pet.is_visible_by_path", new=AsyncMock(return_value=True)),
                patch("src.auto_pet.get_window_from_path", new=AsyncMock(return_value=action_window)),
                patch("src.auto_pet.asyncio.sleep", new=AsyncMock()),
                patch("src.auto_pet.post_keys", new=AsyncMock()) as send,
            ):
                await dancedance(client)
            self.assertEqual([call.args[1] for call in send.await_args_list], moves)
            self.assertEqual(action_window.maybe_text.await_count, len(prompts))

        for prompt in ("Go!", "开始重复！", "開始重複！"):
            for already_go in (False, True):
                with self.subTest(prompt=prompt, already_go=already_go):
                    asyncio.run(exercise(prompt, already_go))

    def test_energy_parser_does_not_depend_on_label_language(self):
        self.assertEqual(_first_number("<center>8"), 8)
        self.assertEqual(_first_number("能量：42/100"), 42)

    def test_pet_game_entry_uses_window_state_not_popup_text(self):
        async def exercise():
            client = AsyncMock()
            client.is_loading.return_value = False
            client.zone_name.return_value = 'WizardCity/WC_Streets/Interiors/WC_PET_Park'
            client.body.position.return_value = XYZ(-4449.3667, -992.9968, 0)
            picker_states = iter([False, False, True])
            async def visible(_client, path):
                return next(picker_states) if path == pet_feed_window_visible_path else True
            with (
                patch("src.auto_pet._pet_picker_visible", new=AsyncMock(side_effect=visible)),
                patch("src.auto_pet.is_visible_by_path", new=AsyncMock(return_value=True)),
            ):
                self.assertTrue(await _open_pet_game_window(client))
            self.assertEqual(client.send_key.await_count, 2)
            client.send_key.assert_awaited_with(Keycode.X, 0.1)

        import asyncio

        asyncio.run(exercise())


class AutoPetSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_entry_does_not_press_x_elsewhere(self):
        client = AsyncMock()
        client.is_loading.return_value = False
        client.zone_name.return_value = "WizardCity/WC_Hub"
        with patch("src.auto_pet._pet_picker_visible", new=AsyncMock(return_value=False)):
            self.assertFalse(await _open_pet_game_window(client))
        client.send_key.assert_not_awaited()

    async def test_entry_does_not_press_x_far_from_dance_sigil(self):
        client = AsyncMock()
        client.is_loading.return_value = False
        client.zone_name.return_value = 'WizardCity/WC_Streets/Interiors/WC_PET_Park'
        client.body.position.return_value = XYZ(0, 0, 0)
        with patch("src.auto_pet._pet_picker_visible", new=AsyncMock(return_value=False)):
            self.assertFalse(await _open_pet_game_window(client))
        client.send_key.assert_not_awaited()

    async def test_expired_input_prompt_never_posts_keys(self):
        client, action = AsyncMock(), AsyncMock()
        action.is_visible.return_value = True
        action.maybe_text.side_effect = ["开始重复！", "完成！"]
        with (
            patch("src.auto_pet.is_visible_by_path", new=AsyncMock(return_value=True)),
            patch("src.auto_pet.get_window_from_path", new=AsyncMock(return_value=action)),
            patch("src.auto_pet.asyncio.sleep", new=AsyncMock()),
            patch("src.auto_pet.post_keys", new=AsyncMock()) as send,
        ):
            with self.assertRaisesRegex(RuntimeError, "回合已经结束"):
                await dancedance(client)
        send.assert_not_awaited()

    async def test_nomnom_cleans_up_on_error_and_cancellation(self):
        import asyncio
        from src.auto_pet import nomnom
        for error in (RuntimeError("hook failure"), asyncio.CancelledError()):
            client = AsyncMock()
            client.feeding_pet_status = True
            async def fail(_client, _ignore, _play, state):
                state["owned"] = True
                raise error
            with (
                patch("src.auto_pet._nomnom", new=AsyncMock(side_effect=fail)),
                patch("src.auto_pet.attempt_deactivate_dance_hook", new=AsyncMock()) as cleanup,
            ):
                with self.assertRaises(type(error)):
                    await nomnom(client, False, True)
            self.assertFalse(client.feeding_pet_status)
            cleanup.assert_awaited_once_with(client)

    async def test_idle_pet_worker_leaves_other_dance_hook_alone(self):
        from src.auto_pet import nomnom
        client = AsyncMock()
        with (
            patch("src.auto_pet._nomnom", new=AsyncMock()),
            patch("src.auto_pet.attempt_deactivate_dance_hook", new=AsyncMock()) as cleanup,
        ):
            await nomnom(client, False, True)
        cleanup.assert_not_awaited()


class PetPickerLayoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_localized_container_fallback_is_scoped_to_picker(self):
        from src.auto_pet import _pet_picker_control
        from src.paths import play_dance_game_button_path
        client, hidden, picker, button = [AsyncMock() for _ in range(4)]
        hidden.is_visible.return_value = False
        picker.is_visible.return_value = True
        button.is_visible.return_value = True
        client.root_window.get_windows_with_name.return_value = [hidden, picker]
        picker.get_windows_with_name.return_value = [button]
        with patch("src.auto_pet.get_window_from_path", new=AsyncMock(return_value=False)):
            self.assertIs(await _pet_picker_control(client, play_dance_game_button_path), button)
        client.root_window.get_windows_with_name.assert_awaited_once_with("PetGameTracks")
        picker.get_windows_with_name.assert_awaited_once_with("btnNext")
        hidden.get_windows_with_name.assert_not_awaited()

    async def test_picker_start_clicks_level_and_play_without_x(self):
        from src.auto_pet import _start_pet_dance_game
        from src.paths import wizard_city_dance_game_path, play_dance_game_button_path
        client = AsyncMock()
        with (
            patch("src.auto_pet._pet_picker_visible", new=AsyncMock(side_effect=[True, False])),
            patch("src.auto_pet._click_pet_picker", new=AsyncMock()) as click,
            patch("src.auto_pet.asyncio.sleep", new=AsyncMock()),
        ):
            await _start_pet_dance_game(client)
        self.assertEqual([c.args[1] for c in click.await_args_list],
                         [wizard_city_dance_game_path, play_dance_game_button_path])
        client.send_key.assert_not_awaited()

    async def test_picker_that_does_not_close_times_out(self):
        from src.auto_pet import _start_pet_dance_game
        with (
            patch("src.auto_pet._pet_picker_visible", new=AsyncMock(return_value=True)),
            patch("src.auto_pet.time.monotonic", side_effect=[0, 16]),
            patch("src.auto_pet._click_pet_picker", new=AsyncMock()) as click,
        ):
            with self.assertRaisesRegex(TimeoutError, "选择界面仍未关闭"):
                await _start_pet_dance_game(AsyncMock())
        click.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

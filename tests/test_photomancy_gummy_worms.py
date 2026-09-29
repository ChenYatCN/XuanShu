import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode
from src.questing import Quester


class GummyWormPhotoTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.quester = object.__new__(Quester)
        self.camera = SimpleNamespace(update_orientation=AsyncMock())
        self.client = SimpleNamespace(
            zone_name=AsyncMock(return_value=Quester.GUMMY_WORMS_PHOTO_ZONE),
            teleport=AsyncMock(), send_key=AsyncMock(),
            body=SimpleNamespace(write_orientation=AsyncMock()),
            game_client=SimpleNamespace(selected_camera_controller=AsyncMock(return_value=self.camera)),
        )

    async def test_target_photo_uses_position_and_orientation_before_z(self):
        objective = '按下 Z 来拍照 讨厌的虫子'
        with patch('src.questing.get_quest_name', AsyncMock(return_value=objective)), patch(
            'src.questing.asyncio.sleep', new=AsyncMock()
        ):
            await self.quester.take_photomancy_photo(self.client, objective)
        self.client.teleport.assert_awaited_once_with(Quester.GUMMY_WORMS_PHOTO_POSITION)
        self.client.body.write_orientation.assert_awaited_once_with(Quester.GUMMY_WORMS_PHOTO_ORIENTATION)
        self.camera.update_orientation.assert_awaited_once_with(Quester.GUMMY_WORMS_PHOTO_ORIENTATION)
        self.assertEqual([call.args[0] for call in self.client.send_key.await_args_list],
                         [Keycode.A, Keycode.Z, Keycode.Z])

    async def test_other_photo_quest_keeps_existing_z_behavior(self):
        await self.quester.take_photomancy_photo(self.client, '按下 Z 来拍照 高塔')
        self.client.teleport.assert_not_awaited()
        self.client.body.write_orientation.assert_not_awaited()
        self.assertEqual([call.args[0] for call in self.client.send_key.await_args_list],
                         [Keycode.Z, Keycode.Z])


class GiantVatPhotoTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.quester = object.__new__(Quester)
        self.prompt = '按下 Z 来拍照 巨美大桶'
        self.camera = SimpleNamespace(update_orientation=AsyncMock())
        self.client = SimpleNamespace(
            questing_status=True,
            zone_name=AsyncMock(return_value='Karamelle/Interiors/KM_Z09_HotHouse'),
            teleport=AsyncMock(), send_key=AsyncMock(),
            is_loading=AsyncMock(return_value=False),
            in_battle=AsyncMock(return_value=False),
            body=SimpleNamespace(write_orientation=AsyncMock()),
            game_client=SimpleNamespace(selected_camera_controller=AsyncMock(return_value=self.camera)),
        )
        self.quester.read_popup = AsyncMock(return_value=self.prompt)
        self.before = (123, 4, '按下 Z 来拍照 巨美大桶')

    async def test_first_photo_progress_skips_second_orientation(self):
        self.quester._dungeon_quest_snapshot = AsyncMock(
            side_effect=[self.before, (123, 5, '下一个目标')]
        )
        with patch('src.questing.asyncio.sleep', new=AsyncMock()):
            await self.quester.take_photomancy_photo(self.client, self.prompt)
        self.client.teleport.assert_awaited_once_with(Quester.GIANT_VAT_PHOTO_POSITION)
        self.client.body.write_orientation.assert_awaited_once_with(
            Quester.GIANT_VAT_PHOTO_ORIENTATIONS[0]
        )
        self.camera.update_orientation.assert_awaited_once_with(
            Quester.GIANT_VAT_PHOTO_ORIENTATIONS[0]
        )
        self.assertEqual([call.args[0] for call in self.client.send_key.await_args_list],
                         [Keycode.A, Keycode.Z, Keycode.Z])

    async def test_no_progress_tries_second_orientation_once(self):
        self.quester._dungeon_quest_snapshot = AsyncMock(
            side_effect=[self.before] * 12
        )
        with patch('src.questing.asyncio.sleep', new=AsyncMock()):
            await self.quester.take_photomancy_photo(self.client, self.prompt)
            await self.quester.take_photomancy_photo(self.client, self.prompt)
        self.assertEqual(
            [call.args[0] for call in self.client.body.write_orientation.await_args_list],
            list(Quester.GIANT_VAT_PHOTO_ORIENTATIONS),
        )
        self.assertEqual(
            [call.args[0] for call in self.camera.update_orientation.await_args_list],
            list(Quester.GIANT_VAT_PHOTO_ORIENTATIONS),
        )
        self.assertEqual([call.args[0] for call in self.client.send_key.await_args_list],
                         [Keycode.A, Keycode.Z, Keycode.Z, Keycode.Z, Keycode.Z])
        self.client.teleport.assert_awaited_once_with(Quester.GIANT_VAT_PHOTO_POSITION)

    async def test_prompt_triggers_even_if_tracked_objective_differs(self):
        self.quester.take_photomancy_photo = AsyncMock()
        with patch('src.questing.is_visible_by_path', new=AsyncMock(return_value=True)):
            self.assertTrue(await self.quester._maybe_photo_giant_vat(self.client))
        self.quester.take_photomancy_photo.assert_awaited_once_with(self.client, self.prompt)

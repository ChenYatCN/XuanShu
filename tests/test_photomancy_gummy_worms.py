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

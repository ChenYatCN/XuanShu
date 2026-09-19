import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.world_to_screen import get_camera_state, project_point


class ProjectionAxesTests(unittest.IsolatedAsyncioTestCase):
    async def test_camera_forward_ray_stays_at_screen_center_when_pitched(self):
        view = SimpleNamespace(**{name: AsyncMock(return_value=value) for name, value in {
            'viewport_left': -1, 'viewport_right': 1,
            'viewport_top': 1, 'viewport_bottom': -1,
            'screenport_left': 0, 'screenport_right': 1,
            'screenport_top': 1, 'screenport_bottom': 0,
        }.items()})
        camera = SimpleNamespace(
            position=AsyncMock(return_value=SimpleNamespace(x=0, y=0, z=0)),
            yaw=AsyncMock(return_value=0.7), pitch=AsyncMock(return_value=0.4),
            gamebryo_camera=AsyncMock(return_value=SimpleNamespace(cam_view=AsyncMock(return_value=view))),
        )
        client = SimpleNamespace(window_handle=0, game_client=SimpleNamespace(
            selected_camera_controller=AsyncMock(return_value=camera)))
        with patch('src.world_to_screen.user32.GetClientRect'):
            cam = await get_camera_state(client)
        cam.update(client_w=1000, client_h=800)
        point = [cam['fwd_' + axis] * 100 for axis in 'xyz']
        sx, sy = project_point(cam, *point)
        self.assertAlmostEqual(sx, 500, delta=1)
        self.assertAlmostEqual(sy, 400, delta=1)

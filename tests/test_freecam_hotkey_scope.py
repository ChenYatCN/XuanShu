import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock


class FreecamHotkeyScopeTests(unittest.IsolatedAsyncioTestCase):
    def runtime(self, clients):
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        names = {'stop_freecam_player_lock', 'toggle_freecam_hotkey'}
        functions = [node for node in ast.walk(tree)
                     if isinstance(node, ast.AsyncFunctionDef) and node.name in names]

        async def player_lock(*_):
            await asyncio.Event().wait()

        namespace = {
            'asyncio': asyncio,
            'walker': SimpleNamespace(clients=clients),
            'foreground_client': clients[0],
            'freecam_status': False,
            'freecam_player_lock_task': None,
            'legacy_freecam_ids': set(),
            'is_free': AsyncMock(return_value=True),
            'sync_camera': AsyncMock(),
            'lock_freecam_player': player_lock,
            'logger': Mock(),
            'gui_send_queue': Mock(),
            'bool_to_string': lambda state: 'Enabled' if state else 'Disabled',
            'xuanshu_gui': SimpleNamespace(
                GUICommand=lambda kind, data: (kind, data),
                GUICommandType=SimpleNamespace(UpdateWindow='update'),
            ),
        }
        module = ast.Module(body=functions, type_ignores=[])
        exec(compile('from __future__ import annotations\n' + ast.unparse(module),
                     'XuanShu.py', 'exec'), namespace)
        return namespace

    async def test_only_operated_client_enters_freecam_and_previous_exits(self):
        def client(title):
            state = SimpleNamespace(enabled=False)
            async def enable():
                state.enabled = True
            async def disable():
                state.enabled = False
            return SimpleNamespace(
                title=title, state=state,
                game_client=SimpleNamespace(is_freecam=AsyncMock(
                    side_effect=lambda: state.enabled)),
                body=SimpleNamespace(position=AsyncMock(return_value=(1, 2, 3)),
                                     orientation=AsyncMock(return_value=(0, 0, 0))),
                camera_freecam=AsyncMock(side_effect=enable),
                camera_elastic=AsyncMock(side_effect=disable),
                _unpatch_movement_update=AsyncMock(),
            )

        p1, p2, p3 = [client(title) for title in ('p1', 'p2', 'p3')]
        runtime = self.runtime([p1, p2, p3])
        toggle = runtime['toggle_freecam_hotkey']
        try:
            await toggle()
            self.assertEqual([c.state.enabled for c in (p1, p2, p3)],
                             [True, False, False])
            self.assertEqual(runtime['legacy_freecam_ids'], {id(p1)})

            runtime['foreground_client'] = p2
            await toggle()
            self.assertEqual([c.state.enabled for c in (p1, p2, p3)],
                             [False, True, False])
            p1.camera_elastic.assert_awaited_once()
            p3.camera_freecam.assert_not_awaited()
            self.assertEqual(runtime['legacy_freecam_ids'], {id(p2)})

            await toggle()
            self.assertEqual([c.state.enabled for c in (p1, p2, p3)],
                             [False, False, False])
            self.assertFalse(runtime['freecam_status'])
        finally:
            await runtime['stop_freecam_player_lock']()

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock


def load_function(name, namespace):
    tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
    function = next(node for node in ast.walk(tree)
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name)
    exec(compile(ast.Module(body=[function], type_ignores=[]), 'XuanShu.py', 'exec'), namespace)
    return namespace[name]


class ScopedRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_post_battle_backward_only_for_questing(self):
        move = load_function('clear_post_combat_phase', {
            'asyncio': asyncio, 'Client': object, 'logger': Mock(),
            'Keycode': SimpleNamespace(S='S', A='A', D='D')})
        for questing, expected in ((True, ['S']), (False, ['A', 'D'])):
            client = SimpleNamespace(title='p1', questing_status=questing,
                is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
                send_key=AsyncMock())
            await move(client)
            self.assertEqual([call.kwargs['key'] for call in client.send_key.await_args_list], expected)
            self.assertFalse(client.post_combat_movement_active)

    async def test_no_post_battle_movement_during_battle(self):
        move = load_function('clear_post_combat_phase', {
            'asyncio': asyncio, 'Client': object, 'logger': Mock()})
        client = SimpleNamespace(is_loading=AsyncMock(return_value=False),
            in_battle=AsyncMock(return_value=True), send_key=AsyncMock())
        await move(client)
        client.send_key.assert_not_awaited()
        self.assertFalse(client.post_combat_movement_active)

    def test_scoped_quest_roles_never_mutate_other_clients(self):
        selected = SimpleNamespace(questing_status=False)
        other = SimpleNamespace(questing_status=True, quest_party_hitters=['untouched'])
        party = SimpleNamespace(questers=[selected], hitters=[], hitter_assignments=[])
        resolve = Mock(return_value=party)
        apply = load_function('apply_questing_roles', {
            'current_quest_party': resolve, 'walker': SimpleNamespace(clients=[selected, other])})
        apply(True, [selected])
        self.assertTrue(selected.questing_status)
        apply(False, [selected])
        self.assertFalse(selected.questing_status)
        self.assertTrue(other.questing_status)
        self.assertEqual(other.quest_party_hitters, ['untouched'])

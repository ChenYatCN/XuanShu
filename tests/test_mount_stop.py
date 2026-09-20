"""Regression checks for removal of the retired mount-stop feature."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from src.deimoslang.vm import VM
from src.settings_manager import XuanShuSettings
from src.auto_fish_original_adapter import check_fishing_energy


class RetiredMountStopTests(unittest.IsolatedAsyncioTestCase):
    def test_retired_command_rejected_and_normal_commands_parse(self):
        VM([]).load_from_text('sleep 1\nkill\n')
        for text in ('p1 stopifmount "骨龙"\n', 'p1 getmounts kill\n'):
            with self.assertRaises(Exception):
                VM([]).load_from_text(text)

    async def test_fishing_ignores_retired_monitor(self):
        monitor = SimpleNamespace(check=AsyncMock(side_effect=AssertionError('retired feature called')))
        client = SimpleNamespace(is_fishing=True, current_energy=AsyncMock(return_value=20),
                                 fishing_mount_monitor=monitor)
        await check_fishing_energy(client)
        monitor.check.assert_not_awaited()
        client.current_energy.assert_awaited_once()

    def test_old_settings_are_removed_without_affecting_other_values(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            path.write_text(json.dumps({'settings': {
                'fish_stop_on_mount': True, 'fish_mount_name': '骨龙',
                'stop_on_mount': True, 'mount_name': '骨龙',
                'hotkey_client_groups': {'group': ['p1']},
                'hotkey_group_targets': {'toggle_combat': 'group'},
                'fish_school': 'Death', 'speed_multiplier': 8,
            }}), encoding='utf-8')
            settings = XuanShuSettings(str(path))
            self.assertEqual(settings.get_setting('fish_school'), 'Death')
            self.assertEqual(settings.get_setting('speed_multiplier'), 8)
            retired = ('fish_stop_on_mount', 'fish_mount_name', 'stop_on_mount', 'mount_name',
                       'hotkey_client_groups', 'hotkey_group_targets')
            self.assertFalse(set(retired) & settings.get_settings().keys())
            settings.set_settings({'fish_stop_on_mount': True})
            self.assertFalse(set(retired) & json.loads(path.read_text())['settings'].keys())

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from src.mount_stop import MountMonitor, mount_receipts, run_until_mount
from src.deimoslang.vm import VM
from src.auto_fish_original_adapter import check_fishing_energy, FishingMountObtained


class MountStopTests(unittest.IsolatedAsyncioTestCase):
    def test_only_typed_system_mount_receipts(self):
        line = '<color;00FF00><image;Art/Art_Chat_System.dds;24;24;FFFFFFFF> <image;Mount> 骨龙(永久)</color>'
        self.assertEqual(mount_receipts(line, '骨龙(永久)'), ['骨龙(永久)'])
        self.assertEqual(mount_receipts(line.replace(';Mount>', ';Pet>')), [])
        self.assertEqual(mount_receipts('玩家说：我获得了骨龙(永久)'), [])
        self.assertEqual(mount_receipts(line, '其他坐骑'), [])

    async def test_named_existing_mount_and_new_mount_baseline(self):
        with patch('src.mount_stop.owned_mounts', AsyncMock(return_value={1: ('骨龙', 'Mount_1', 'BoneDragon')})), \
             patch('src.mount_stop.get_chat', AsyncMock(return_value='')):
            self.assertEqual(await MountMonitor('骨龙').check(object()), '骨龙')
            monitor = MountMonitor()
            self.assertIsNone(await monitor.check(object()))
            monitor.next_scan = 0
            with patch('src.mount_stop.owned_mounts', AsyncMock(return_value={1: ('骨龙', '', ''), 2: ('新坐骑', '', '')})):
                self.assertEqual(await monitor.check(object()), '新坐骑')

    async def test_already_owned_stops_before_script_starts(self):
        run = AsyncMock()
        with patch('src.mount_stop.owned_mounts', AsyncMock(return_value={1: ('骨龙', '', '')})):
            await run_until_mount(run, [SimpleNamespace(title='p1')], '骨龙')
        run.assert_not_awaited()

    async def test_fishing_checks_mount_before_consuming_energy(self):
        client = SimpleNamespace(title='p1', is_fishing=True,
            current_energy=AsyncMock(return_value=100),
            fishing_mount_monitor=SimpleNamespace(check=AsyncMock(return_value='骨龙')))
        with self.assertRaises(FishingMountObtained):
            await check_fishing_energy(client)
        client.current_energy.assert_not_awaited()
        client.fishing_mount_monitor = None
        await check_fishing_energy(client)
        client.current_energy.assert_awaited_once()

    def test_stopifmount_parses_with_client_selector(self):
        machine = VM([])
        machine.load_from_text('p1 stopifmount "骨龙(永久)"\n')

    async def test_stopifmount_is_normal_stop_and_only_selected_client(self):
        from src.deimoslang.ir import Instruction, InstructionKind
        client = SimpleNamespace(title='p2')
        machine = object.__new__(VM)
        machine._select_action_players = lambda _: [client]
        machine._constants = {}
        machine.running = True
        machine.killed = False
        with patch('src.mount_stop.has_mount', AsyncMock(return_value='骨龙')) as check:
            await machine.exec_deimos_call(Instruction(InstructionKind.deimos_call, [None, 'stop_if_mount', ['骨龙']]))
        check.assert_awaited_once_with(client, '骨龙')
        self.assertTrue(machine.killed)
        self.assertFalse(machine.running)

"""Simulate entity movement and run the real command/parser/VM paths."""
import asyncio
import math
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from wizwalker import Keycode, MemoryReadError, XYZ
from wizwalker.utils import timed_send_key

from src import command_parser as commands
from src.automation_ownership import get_client_automation_ownership
from src.deimoslang.parser import Parser
from src.deimoslang.tokenizer import Tokenizer
from src.deimoslang.vm import VM


REAL_SLEEP = asyncio.sleep
TARGET_NAME = 'GR_AZ_Parasaur_Undead_B_01'


class Entity:
    def __init__(self, address, positions):
        self.address = address
        self.positions = iter(positions)
        self.last = XYZ(100, 0, 0)
        self.reads = 0

    async def read_base_address(self):
        return self.address

    async def global_id_full(self):
        return self.address + 1000

    async def location(self):
        self.reads += 1
        value = next(self.positions, self.last)
        if isinstance(value, Exception):
            raise value
        self.last = value
        return value


class EntityFollowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 0.0
        self.position = XYZ(0, 0, 0)
        self.battle = self.loading = False
        self.moving = True
        self.yaws = []
        self.steps = []
        self.lookups = []
        self.target = Entity(1, [XYZ(100, 0, 0), XYZ(10, 100, 0)])
        self.entities = [self.target]
        self.on_step = lambda: None
        self.pet_mode = False
        self.pet_position = XYZ(1000, 1000, 0)
        self.pet_yaws = []
        self.pet_address = 200
        self.pet_available = self.camera_selected = True
        self.pet_body = SimpleNamespace(
            position=self.read_pet_position, write_yaw=self.write_pet_yaw,
            read_base_address=self.read_pet_address,
        )
        self.pet_object = SimpleNamespace(object_name=self.object_name, actor_body=self.actor_body)
        self.camera = SimpleNamespace(
            read_base_address=self.camera_address, attached_client_object=self.attached_object,
        )
        self.client = SimpleNamespace(
            title='p1', body=SimpleNamespace(position=self.read_position, write_yaw=self.write_yaw,
                                            read_base_address=self.player_address),
            game_client=SimpleNamespace(elastic_camera_controller=self.elastic_camera,
                                        selected_camera_controller=self.selected_camera),
            in_battle=self.in_battle, is_loading=self.is_loading,
            get_base_entity_list=self.entity_list, send_key=self.send_key,
        )
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(commands.SprintyClient, 'find_closest_by_vague_name', self.find))

    async def read_position(self):
        return self.position

    async def player_address(self):
        return 100

    async def read_pet_address(self):
        return self.pet_address

    async def read_pet_position(self):
        return self.pet_position

    async def write_pet_yaw(self, yaw):
        self.pet_yaws.append(yaw)

    async def object_name(self):
        return 'PetObject' if self.pet_mode else 'Player Object'

    async def actor_body(self):
        return self.pet_body if self.pet_available else None

    async def attached_object(self):
        return self.pet_object

    async def elastic_camera(self):
        return self.camera

    async def selected_camera(self):
        return self.camera if self.camera_selected else None

    async def camera_address(self):
        return 300

    async def write_yaw(self, yaw):
        self.yaws.append(yaw)

    async def in_battle(self):
        return self.battle

    async def is_loading(self):
        return self.loading

    async def entity_list(self):
        return self.entities

    async def find(self, name):
        self.lookups.append(name)
        return self.target

    async def send_key(self, key, duration):
        self.assertEqual(key, Keycode.W)
        self.assertGreater(duration, 0)
        self.assertLessEqual(duration, .1)
        self.steps.append(duration)
        self.now += duration
        if self.moving:
            if self.pet_mode:
                self.pet_position = XYZ(self.pet_position.x + 10, self.pet_position.y, 0)
            else:
                self.position = XYZ(self.position.x + 10, self.position.y, 0)
        self.on_step()
        await REAL_SLEEP(0)

    async def sleep(self, duration):
        self.now += duration
        await REAL_SLEEP(0)

    def fake_clock(self):
        self.stack.enter_context(patch.object(commands, 'monotonic', lambda: self.now, create=True))
        self.stack.enter_context(patch.object(commands.asyncio, 'sleep', self.sleep))

    async def run_command(self, source=None):
        await commands.parse_command([self.client], source or f'p1 entityfollow {TARGET_NAME}')

    async def test_reaims_at_live_position_and_keeps_original_target_until_battle(self):
        self.on_step = lambda: setattr(self, 'battle', len(self.steps) == 2)
        await self.run_command()
        self.assertEqual(self.lookups, [TARGET_NAME])
        self.assertEqual(self.target.reads, 2)
        self.assertEqual(len(self.steps), 2)
        self.assertAlmostEqual(math.sin(self.yaws[0]), -1)
        self.assertAlmostEqual(math.cos(self.yaws[1]), -1)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_at_target_still_walks_until_battle(self):
        self.target = Entity(1, [XYZ(0, 0, 0), XYZ(10, 0, 0)])
        self.entities = [self.target]
        self.on_step = lambda: setattr(self, 'battle', len(self.steps) == 2)
        await self.run_command()
        self.assertEqual(len(self.steps), 2)

    async def test_disappearing_target_is_replaced_even_if_old_memory_remains_readable(self):
        original = self.target
        replacement = Entity(2, [XYZ(100, 100, 0)])
        def move_target():
            if len(self.steps) == 1:
                self.target = replacement
                self.entities = [replacement]
            else:
                self.battle = True
        self.on_step = move_target
        await self.run_command()
        self.assertEqual(self.lookups, [TARGET_NAME, TARGET_NAME])
        self.assertEqual(original.reads, 1)
        self.assertEqual(replacement.reads, 1)

    async def test_invalid_target_read_reacquires(self):
        original = Entity(1, [ValueError('invalid entity')])
        replacement = self.target
        self.entities = [original, replacement]
        choices = iter([original, replacement])
        async def find(_sprinty, name):
            self.lookups.append(name)
            return next(choices)
        self.stack.enter_context(patch.object(commands.SprintyClient, 'find_closest_by_vague_name', find))
        self.on_step = lambda: setattr(self, 'battle', True)
        await self.run_command()
        self.assertEqual(len(self.lookups), 2)
        self.assertEqual(len(self.steps), 1)

    async def test_no_target_returns_without_movement(self):
        self.fake_clock()
        self.target = None
        await self.run_command()
        self.assertLessEqual(self.now, 4)
        self.assertFalse(self.steps)

    async def test_stuck_returns_without_reselecting_a_valid_target(self):
        self.fake_clock()
        self.moving = False
        await self.run_command()
        self.assertLessEqual(self.now, 4)
        self.assertGreater(len(self.steps), 1)
        self.assertEqual(self.lookups, [TARGET_NAME])

    async def test_moving_without_battle_is_bounded(self):
        self.fake_clock()
        await self.run_command()
        self.assertGreater(self.now, 4)
        self.assertLessEqual(self.now, 31)

    async def test_battle_or_loading_does_not_send_movement(self):
        for attr in ('battle', 'loading'):
            with self.subTest(attr=attr):
                setattr(self, attr, True)
                await self.run_command()
                setattr(self, attr, False)
        self.assertFalse(self.steps)

    async def test_failed_entity_memory_read_reacquires(self):
        original = Entity(1, [MemoryReadError(1)])
        replacement = self.target
        self.entities = [original, replacement]
        choices = iter([original, replacement])
        async def find(_sprinty, name):
            self.lookups.append(name)
            return next(choices)
        self.stack.enter_context(patch.object(commands.SprintyClient, 'find_closest_by_vague_name', find))
        self.on_step = lambda: setattr(self, 'battle', True)
        await self.run_command()
        self.assertEqual(len(self.lookups), 2)
        self.assertEqual(len(self.steps), 1)

    async def test_stalled_entity_lookup_has_a_timeout(self):
        real_timeout = asyncio.timeout
        async def find(_sprinty, name):
            await asyncio.Event().wait()
        self.stack.enter_context(patch.object(commands.SprintyClient, 'find_closest_by_vague_name', find))
        self.stack.enter_context(patch.object(commands.asyncio, 'timeout', lambda _: real_timeout(.02)))
        await self.run_command()
        self.assertFalse(self.steps)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_cancel_drains_actual_timed_key_and_releases_owner(self):
        started = asyncio.Event()
        released = asyncio.Event()
        messages = []
        def send_message(handle, message, key, flags):
            messages.append(message)
            (started if message == 0x100 else released).set()
        async def send_key(key, duration):
            await timed_send_key(0, key, duration)
        self.client.send_key = send_key
        self.stack.enter_context(patch('wizwalker.utils.user32.SendMessageW', send_message))
        task = asyncio.create_task(self.run_command())
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        await REAL_SLEEP(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(released.is_set())
        self.assertEqual(messages[-1], 0x101)
        count = len(messages)
        await REAL_SLEEP(.06)
        self.assertEqual(len(messages), count)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_vm_compiles_and_executes_selected_client_then_continues(self):
        self.on_step = lambda: setattr(self, 'battle', True)
        with patch('src.deimoslang.vm.upgrade_clients', lambda clients: clients):
            vm = VM([self.client, SimpleNamespace(title='p2')])
        vm.load_from_text(f'p1 entityfollow {TARGET_NAME}\nkill\n')
        vm.running = True
        while vm.running:
            await vm.step()
        self.assertTrue(vm.killed)
        self.assertEqual(len(self.steps), 1)

    def test_parser_accepts_literal_name_and_rejects_missing_argument(self):
        stmt = Parser(Tokenizer().tokenize('p1 entityfollow "Undead Parasaur"\n')).parse()[0]
        self.assertEqual(stmt.command.data, ['Undead Parasaur'])
        with self.assertRaises(Exception):
            Parser(Tokenizer().tokenize('p1 entityfollow\n')).parse()

    async def test_pet_reaims_using_pet_position_and_keeps_target_until_battle(self):
        self.pet_mode = True
        self.on_step = lambda: setattr(self, 'battle', len(self.steps) == 2)
        await self.run_command()
        self.assertFalse(self.yaws)
        self.assertEqual(len(self.pet_yaws), 2)
        self.assertNotAlmostEqual(self.pet_yaws[0], self.pet_yaws[1])
        expected = commands.calculate_perfect_yaw(XYZ(1000, 1000, 0), XYZ(100, 0, 0))
        self.assertAlmostEqual(self.pet_yaws[0], expected)
        self.assertEqual(self.lookups, [TARGET_NAME])

    async def test_pet_movement_does_not_use_stationary_player_for_stall(self):
        self.pet_mode = True
        self.fake_clock()
        self.on_step = lambda: setattr(self, 'battle', self.now >= 4)
        await self.run_command()
        self.assertTrue(self.battle)
        self.assertGreaterEqual(self.now, 4)
        self.assertEqual((self.position.x, self.position.y, self.position.z), (0, 0, 0))
        self.assertFalse(self.yaws)

    async def test_mode_switches_refresh_body_without_reselecting_target(self):
        def switch():
            if len(self.steps) == 1:
                self.pet_mode = True
            elif len(self.steps) == 2:
                self.pet_mode = False
            else:
                self.battle = True
        self.on_step = switch
        await self.run_command()
        self.assertEqual(len(self.pet_yaws), 1)
        self.assertEqual(len(self.yaws), 2)
        self.assertEqual(self.lookups, [TARGET_NAME])

    async def test_pet_mode_changes_after_yaw_do_not_walk_with_stale_body(self):
        self.pet_mode = True
        async def write(yaw):
            self.pet_yaws.append(yaw)
            self.pet_mode = False
        self.pet_body.write_yaw = write
        self.on_step = lambda: setattr(self, 'battle', True)
        await self.run_command()
        self.assertEqual(len(self.yaws), 1)
        self.assertEqual(len(self.steps), 1)

    async def test_pet_missing_body_or_inactive_camera_does_not_fall_back_to_player(self):
        self.pet_mode = True
        for attr in ('pet_available', 'camera_selected'):
            with self.subTest(attr=attr):
                setattr(self, attr, False)
                await self.run_command()
                setattr(self, attr, True)
        self.assertFalse(self.steps)
        self.assertFalse(self.yaws)

    async def test_pet_stuck_is_bounded(self):
        self.pet_mode = True
        self.fake_clock()
        self.moving = False
        await self.run_command()
        self.assertLessEqual(self.now, 4)
        self.assertTrue(self.pet_yaws)
        self.assertEqual(self.lookups, [TARGET_NAME])

    async def test_pet_target_disappearance_stops_after_bounded_relookup(self):
        self.pet_mode = True
        self.fake_clock()
        def disappear():
            self.target = None
            self.entities = []
        self.on_step = disappear
        await self.run_command()
        self.assertEqual(len(self.steps), 1)
        self.assertLessEqual(self.now, 4)
        self.assertGreater(len(self.lookups), 1)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_pet_body_replacement_resets_stall_timer(self):
        self.pet_mode = True
        self.fake_clock()
        self.moving = False
        def replace():
            if self.now >= 2.5 and self.pet_address == 200:
                self.pet_address = 201
        self.on_step = replace
        await self.run_command()
        self.assertGreaterEqual(self.now, 5.5)
        self.assertLessEqual(self.now, 6)
        self.assertEqual(self.lookups, [TARGET_NAME])

    async def test_pet_battle_or_loading_does_not_send_movement(self):
        self.pet_mode = True
        await self.test_battle_or_loading_does_not_send_movement()

    async def test_pet_cancel_drains_key_and_releases_owner(self):
        self.pet_mode = True
        await self.test_cancel_drains_actual_timed_key_and_releases_owner()


if __name__ == '__main__':
    unittest.main()

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from src.deimoslang.parser import Parser, ParserError
from src.deimoslang.tokenizer import Tokenizer
from src.deimoslang.types import WaitforKind, StringExpression
from src.deimoslang.ir import Instruction, InstructionKind
from src.deimoslang.vm import VM

ZONE = 'Celestia/CL_Z12_Test_of_the_Spheres/StarRoom'

def parse(source):
    return Parser(Tokenizer().tokenize(source)).parse()[0].command

class WaitForZoneParserTests(unittest.TestCase):
    def test_legacy_and_target_forms(self):
        self.assertEqual(parse('mass waitforzonechange').data, [WaitforKind.zonechange, False])
        self.assertEqual(parse('mass waitforzonechange completion').data, [WaitforKind.zonechange, True])
        for value in (ZONE, '"' + ZONE + '"'):
            for suffix in ('', ' completion'):
                command = parse('mass waitforzonechange to ' + value + suffix)
                self.assertTrue(command.player_selector.mass)
                self.assertEqual(command.data[1].string, ZONE)
                self.assertEqual(command.data[-1], bool(suffix))

    def test_missing_target_rejected(self):
        with self.assertRaises(ParserError):
            parse('mass waitforzonechange to')

class WaitForZoneRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def client(self, zone):
        return SimpleNamespace(zone_name=AsyncMock(return_value=zone), is_loading=AsyncMock(return_value=False))

    async def execute(self, command, clients):
        vm = object.__new__(VM)
        vm._constants = {}
        vm._select_action_players = lambda _: clients
        vm.eval = AsyncMock(side_effect=lambda expression, client: expression.string)
        instruction = Instruction(InstructionKind.deimos_call, [command.player_selector, 'waitfor', command.data])
        await vm.exec_deimos_call(instruction)

    async def test_waits_for_every_client_and_ignores_wrong_zone(self):
        a, b = self.client('wrong'), self.client(ZONE.lower())
        task = asyncio.create_task(self.execute(parse('mass waitforzonechange to ' + ZONE), [a, b]))
        await asyncio.sleep(.3)
        self.assertFalse(task.done())
        a.zone_name.return_value = ZONE
        await asyncio.wait_for(task, 1)

    async def test_already_in_target_returns(self):
        await asyncio.wait_for(self.execute(parse('mass waitforzonechange to ' + ZONE), [self.client(ZONE)]), .2)

    async def test_completion_waits_for_loading(self):
        client = self.client(ZONE)
        client.is_loading.return_value = True
        task = asyncio.create_task(self.execute(parse('mass waitforzonechange to ' + ZONE + ' completion'), [client]))
        await asyncio.sleep(.3)
        self.assertFalse(task.done())
        client.is_loading.return_value = False
        await asyncio.wait_for(task, 1)

    async def test_legacy_wait_checks_each_clients_starting_zone(self):
        a, b = self.client('one'), self.client('two')
        task = asyncio.create_task(self.execute(parse('mass waitforzonechange'), [a, b]))
        await asyncio.sleep(.05)
        b.zone_name.return_value = 'three'
        await asyncio.sleep(.3)
        self.assertFalse(task.done())
        a.zone_name.return_value = 'four'
        await asyncio.wait_for(task, 1)

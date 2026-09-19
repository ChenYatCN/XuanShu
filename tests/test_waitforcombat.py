import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode

from src.automation_ownership import automation_owner
from src.deimoslang.ir import Instruction, InstructionKind
from src.deimoslang.types import WaitforKind
from src.deimoslang.vm import VM


class WaitForCombatRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def make_vm(self, clients):
        vm = object.__new__(VM)
        vm._constants = {}
        vm._select_action_players = lambda _: clients
        vm.eval = AsyncMock()
        return vm

    @staticmethod
    def instruction(completion):
        return Instruction(
            InstructionKind.deimos_call,
            [SimpleNamespace(), "waitfor", [WaitforKind.battle, completion]],
        )

    async def test_waitforcombat_completion_observes_entry_then_exit(self):
        client = SimpleNamespace(title="p4", battle=False)
        vm = self.make_vm([client])

        with patch(
            "src.deimoslang.vm.Client.in_battle",
            new=AsyncMock(side_effect=lambda current: current.battle),
        ):
            task = asyncio.create_task(
                vm.exec_deimos_call(self.instruction(completion=True))
            )
            await asyncio.sleep(0.05)
            self.assertFalse(task.done(), "out-of-combat must not satisfy completion")

            client.battle = True
            await asyncio.sleep(0.30)
            self.assertFalse(task.done(), "entry alone must not satisfy completion")

            client.battle = False
            await asyncio.wait_for(task, 1)

    async def test_multi_client_wait_binds_each_client(self):
        first = SimpleNamespace(title="p1", battle=False)
        last = SimpleNamespace(title="p4", battle=True)
        vm = self.make_vm([first, last])

        with patch(
            "src.deimoslang.vm.Client.in_battle",
            new=AsyncMock(side_effect=lambda current: current.battle),
        ) as in_battle:
            task = asyncio.create_task(
                vm.exec_deimos_call(self.instruction(completion=False))
            )
            await asyncio.sleep(0.05)
            self.assertFalse(task.done())
            checked_clients = [call.args[0] for call in in_battle.await_args_list]
            self.assertTrue(any(current is first for current in checked_clients))
            self.assertTrue(any(current is last for current in checked_clients))

            first.battle = True
            await asyncio.wait_for(task, 1)


class ScriptOwnershipRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_input_command_waits_for_combat_owner(self):
        client = SimpleNamespace(title="p1", send_key=AsyncMock())
        vm = object.__new__(VM)
        vm._constants = {}
        vm._select_action_players = lambda _: [client]
        vm.eval = AsyncMock()
        instruction = Instruction(
            InstructionKind.deimos_call,
            [SimpleNamespace(), "sendkey", [Keycode.X, None]],
        )

        async with automation_owner(client, "auto-combat-round"):
            script_task = asyncio.create_task(vm.exec_deimos_call(instruction))
            done, _ = await asyncio.wait({script_task}, timeout=0.05)
            self.assertFalse(done)
            client.send_key.assert_not_awaited()

        await asyncio.wait_for(script_task, 1)
        client.send_key.assert_awaited_once_with(Keycode.X, 0.1)

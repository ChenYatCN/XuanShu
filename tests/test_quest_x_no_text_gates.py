import math
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode
from src import questing as q
from src.paths import npc_range_path
from src.automation_ownership import get_client_automation_ownership, automation_owner
from tests import test_quest_x_turn_recovery as local
from tests import test_party_dungeon_interaction as party
from tests import test_panopticon_book as book
from tests import test_mainline_chain_handoff as chain
from tests import test_no_blood_hideout as hideout


class LocalNoTextTests(unittest.IsolatedAsyncioTestCase):
    setUp = local.QuestXTurnRecoveryTests.setUp
    asyncSetUp = local.QuestXTurnRecoveryTests.asyncSetUp

    async def test_different_titles_unknown_and_empty_wording_send_actual_x(self):
        for title, prompt in (('馆长', '按X 未知动作'), ('另一对象', '解除'), ('', '')):
            with self.subTest(title=title, prompt=prompt):
                self.client.quest_interaction_attempt = None
                self.keys.clear()
                self.text, self.prompt = '拜访梅萝莉', prompt
                with (patch.object(q, 'get_popup_title', AsyncMock(return_value=title)),
                      patch.object(q, 'get_quest_name', AsyncMock(side_effect=AssertionError('objective gate'))),
                      patch.object(q, 'quest_interaction_matches', side_effect=AssertionError('matching gate'))):
                    self.assertTrue(await self.quester.handle_quest_interaction(self.client, self.target))
                self.assertEqual([code for code, _ in self.keys], [Keycode.X])
                self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_npc_dispatch_sends_first_x_before_existing_handler(self):
        self.prompt = 'Press X to Talk'
        self.text = 'Use Other Object'
        with patch.object(q, 'get_popup_title', AsyncMock(return_value='Wrong NPC')):
            await self.quester.handle_quest_interaction(self.client, self.target)
        self.assertEqual([code for code, _ in self.keys], [Keycode.X])
        self.quester.handle_npc_talking_quests.assert_awaited_once_with(self.client, [self.client])

    async def test_unknown_wording_and_different_title_continue_full_measured_search(self):
        self.prompt, self.text = '解除 未分类措辞', '拜访梅萝莉'
        with patch.object(q, 'get_popup_title', AsyncMock(return_value='馆长')):
            await self.quester.handle_quest_interaction(self.client, self.target)
            state = self.client.quest_interaction_attempt
            self.now = 4
            await self.quester.handle_quest_interaction(self.client, self.target)
        self.assertGreaterEqual(abs(state['turn_angle']), math.tau)
        self.assertGreater(state['turn_x_count'], 0)
        self.assertIsNone(self.client.quest_interaction_attempt)

    async def test_world_gate_title_during_search_is_not_a_text_rejection(self):
        await self.quester.handle_quest_interaction(self.client, self.target)
        state = self.client.quest_interaction_attempt
        self.now = 4
        with patch.object(q, 'get_popup_title', AsyncMock(return_value='World Gate')):
            await self.quester.handle_quest_interaction(self.client, self.target)
        self.assertGreaterEqual(abs(state['turn_angle']), math.tau)
        self.assertGreater(state['turn_x_count'], 0)

    async def test_visible_unknown_x_stops_current_movement(self):
        self.prompt, self.text = '未分类的可见X', 'Defeat Another Target'
        with patch.object(q, 'get_popup_title', AsyncMock(return_value='Different Object')):
            self.assertTrue(await self.quester._quest_local_interaction_ready(self.client, self.target))
            self.assertTrue(await self.quester.teleport_to_quest_target(self.client, self.target))
        self.client.teleport.assert_not_awaited()


class SharedNoTextTests(unittest.IsolatedAsyncioTestCase):
    setUp = party.PartyDungeonInteractionTests.setUp
    member = party.PartyDungeonInteractionTests.member
    token = party.PartyDungeonInteractionTests.token

    async def asyncSetUp(self):
        import asyncio
        asyncio.get_running_loop().set_debug(False)

    def configure(self, hitter_prompt=True):
        self.prompt, self.title = '按X或图标 解除', '闪电笼'
        self.visible.side_effect = lambda member, path: path == npc_range_path and (member is self.p1 or hitter_prompt)
        self.quester.party_dungeon_entry_visible = AsyncMock(return_value=False)
        self.quester._maybe_photo_giant_vat = AsyncMock(return_value=False)
        self.quester._advance_npc_dialogue = AsyncMock(return_value=False)
        self.yaw = 6.1
        self.p1.body.yaw = AsyncMock(side_effect=lambda: self.yaw % math.tau)
        async def key(code, seconds, member):
            self.events.append((member.title, code))
            if code == Keycode.A:
                self.yaw += math.pi / 4
        for member in (self.p1, self.p2):
            async def member_key(code, seconds, member=member):
                await key(code, seconds, member)
            member.send_key.side_effect = member_key
        for name in ('is_free_leader_questing', 'is_spiral_door_open'):
            item = patch.object(q, name, AsyncMock(return_value=name == 'is_free_leader_questing'))
            item.start()
            self.addCleanup(item.stop)

    async def test_log_cage_quester_x_is_not_blocked_by_absent_hitter_prompt(self):
        self.configure(hitter_prompt=False)
        self.assertTrue(await self.quester.handle_party_dungeon_interaction(self.target))
        self.assertEqual(self.events, [('p1', Keycode.X)])
        self.p2.send_key.assert_not_awaited()
        self.assertEqual(self.p1.quest_party_dungeon_interaction['attempts'].get(id(self.p2), 0), 0)

    async def test_shared_unknown_wording_and_different_member_titles_send_x(self):
        self.configure()
        self.title_reader.side_effect = lambda member: '闪电笼' if member is self.p1 else '其他对象'
        with patch.object(q, 'quest_interaction_matches', side_effect=AssertionError('matching gate')):
            await self.quester.handle_party_dungeon_interaction(self.target)
        self.assertCountEqual(self.events, [('p1', Keycode.X), ('p2', Keycode.X)])

    async def test_shared_no_progress_reuses_measured_circle_and_finite_cooldown(self):
        self.configure(hitter_prompt=False)
        await self.quester.handle_party_dungeon_interaction(self.target)
        state = self.p1.quest_interaction_attempt
        self.now = 4
        await self.quester.handle_party_dungeon_interaction(self.target)
        self.assertGreaterEqual(abs(state['turn_angle']), math.tau)
        count = len(self.events)
        await self.quester.handle_party_dungeon_interaction(self.target)
        self.assertEqual(len(self.events), count)
        self.now += 31
        await self.quester.handle_party_dungeon_interaction(self.target)
        self.assertEqual(len(self.events), count + 1)
        self.p2.send_key.assert_not_awaited()

    async def test_real_shared_entry_still_requires_each_member_ready(self):
        self.configure(hitter_prompt=False)
        self.quester.party_dungeon_entry_visible.return_value = True
        await self.quester.handle_party_dungeon_interaction(self.target)
        self.assertEqual(self.events, [])

    async def test_shared_input_still_requires_same_instance(self):
        self.configure(hitter_prompt=False)
        self.proof.return_value = False
        await self.quester.handle_party_dungeon_interaction(self.target)
        self.assertEqual(self.events, [])

    async def test_hitter_world_gate_never_receives_shared_x(self):
        self.configure()
        self.title_reader.side_effect = lambda member: '闪电笼' if member is self.p1 else 'World Gate'
        await self.quester.handle_party_dungeon_interaction(self.target)
        self.assertEqual(self.events, [('p1', Keycode.X)])

    async def test_world_gate_uses_quester_only_even_with_entry_action_wording(self):
        self.configure()
        self.prompt, self.title = 'Press X to Enter', 'World Gate'
        self.assertFalse(await self.quester.handle_party_dungeon_interaction(self.target))
        await self.quester.handle_quest_interaction(self.p1, self.target)
        self.assertEqual(self.events, [('p1', Keycode.X)])
        self.quester.prepare_party_dungeon_entry.assert_not_awaited()

    async def test_hitter_without_x_keeps_its_foreign_input_claim(self):
        self.configure(hitter_prompt=False)
        acquired, release = asyncio.Event(), asyncio.Event()
        async def other_input():
            async with automation_owner(self.p2, 'foreign-input'):
                acquired.set()
                await release.wait()
        other = asyncio.create_task(other_input())
        await acquired.wait()
        try:
            await self.quester.handle_party_dungeon_interaction(self.target)
            self.assertEqual(self.events, [('p1', Keycode.X)])
            self.assertEqual(get_client_automation_ownership(self.p2).owner_label, 'foreign-input')
        finally:
            release.set()
            await other


class SpecialNoTextTests(unittest.IsolatedAsyncioTestCase):
    setUp = book.PanopticonBookTests.setUp
    handle = book.PanopticonBookTests.handle
    assert_released = book.PanopticonBookTests.assert_released

    async def test_book_and_npc_different_popup_titles_both_send_x(self):
        self.popup_title.side_effect = lambda client: '不同标题'
        await self.handle()
        self.assertEqual(self.client.send_key.await_count, 2)
        self.assert_released()


class HandoffNoTextTests(unittest.IsolatedAsyncioTestCase):
    setUp = chain.MainlineChainHandoffTests.setUp

    async def test_handoff_different_npc_title_and_unknown_action_send_x(self):
        self.quester.read_popup.return_value = '未知动作措辞'
        self.client.quest_id.side_effect = lambda: 11 if self.client.send_key.await_count else 0
        with patch.object(q, 'get_popup_title', AsyncMock(return_value='Other NPC')):
            await self.quester._continue_mainline_chain(self.client, self.snapshot)
        self.client.send_key.assert_awaited_once_with(Keycode.X, .15)


class HideoutNoTextTests(unittest.IsolatedAsyncioTestCase):
    setUp = hideout.NoBloodHideoutTests.setUp
    make_client = hideout.NoBloodHideoutTests.make_client
    assert_released = hideout.NoBloodHideoutTests.assert_released
    tick = hideout.NoBloodHideoutTests.tick

    async def test_unknown_entrance_wording_reaches_existing_entry_state_guards(self):
        await self.tick()
        self.popup = '陌生动作措辞'
        self.client.quest_position.position.return_value = q.Quester.NO_BLOOD_HIDEOUT_POSITION
        entry = AsyncMock(wraps=self.quester.enter_party_dungeon)
        self.quester.enter_party_dungeon = entry
        with (patch.object(q, 'get_popup_title', AsyncMock(return_value='不同标题')),
              patch.object(q, 'clients_share_live_area', AsyncMock(return_value=True))):
            await self.tick()
        entry.assert_awaited_once()
        self.hitter.teleport.assert_awaited_once_with(q.Quester.NO_BLOOD_HIDEOUT_POSITION)
        # This legacy caller's readiness callback rejects its active recovery
        # record after the entry helper sets it; popup wording no longer rejects
        # the call before grouping. Preserve that existing state protection.
        self.client.send_key.assert_not_awaited()
        self.hitter.send_key.assert_not_awaited()
        self.assert_released()


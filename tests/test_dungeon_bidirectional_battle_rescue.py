"""Dungeon-only reverse rescue through actual app functions; all inputs mocked."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from wizwalker.memory.memory_objects.enums import DuelPhase
from src import quest_party
from src.task_lifecycle import gather_owned
from src.automation_ownership import get_client_automation_ownership
from tests.test_scoped_runtime import load_function
from tests.test_party_battle_rescue import BattleRescueTests as Base


class BidirectionalBattleTests(unittest.IsolatedAsyncioTestCase):
    base_setup = Base.setUp
    messages = Base.messages
    entry = Base.entry

    def setUp(self):
        self.base_setup()
        for c in self.clients:
            c.duel_value = 10+c.instance
            c.duel_circle_joinable = True
            c.running = True
            c.is_running = lambda c=c:c.running
            c.quest_id = AsyncMock(return_value=42)
            c.goal_id = AsyncMock(return_value=7)
            c.duel.duel_id_full = AsyncMock(side_effect=lambda c=c:c.duel_value)
            c.duel.duel_phase = AsyncMock(return_value=DuelPhase.planning)
            async def teleport(point,c=c):
                self.events.append((c.title,'tp',tuple(point)))
                c.position = point
                self.on_input(c,'tp')
                peers = self.clients[:2] if c in self.clients[:2] else self.clients[2:]
                for reference in peers:
                    if reference is not c and reference.battle and tuple(point)==tuple(reference.position) and self.join:
                        c.battle = True
                        c.duel_value = reference.duel_value
            c.teleport = teleport
        self.ns.update(dungeon_session_for=quest_party.dungeon_session_for,
            dungeon_member_inside=quest_party.dungeon_member_inside, DuelPhase=DuelPhase,
            _movement_position=lambda c:c.body.position(),
            _movement_control_changed=AsyncMock(return_value=False), gather_owned=gather_owned)

    def dungeon(self,q,h,*,confirmed=True):
        session=quest_party.DungeonSession(q,(q,h),{id(q):'Outside',id(h):'Outside'},
            entry_evidence=True,confirmed=confirmed)
        q.quest_party_dungeon=h.quest_party_dungeon=session
        return session

    async def test_dungeon_hitter_first_rescues_missing_quester(self):
        self.dungeon(self.q1,self.h1)
        self.q1.battle,self.h1.battle=False,True
        await self.rescue(self.q1)
        self.assertTrue(self.q1.battle)
        self.assertEqual({title for title,_,_ in self.events},{'p1'})
        self.assertTrue(any('joined' in m for m in self.messages()))

    async def test_outside_hitter_first_does_not_pull_quester(self):
        self.q1.battle,self.h1.battle=False,True
        await self.rescue(self.q1)
        self.assertFalse(self.events)
        self.assertFalse(self.q1.battle)

    async def test_hitter_without_tracked_quest_keeps_forward_and_reverse_support(self):
        self.h1.quest_id.return_value=0
        await self.rescue(self.q1)
        self.assertTrue(self.h1.battle)
        self.dungeon(self.q1,self.h1)
        self.q1.battle=False
        self.clock+=8
        await self.rescue(self.q1)
        self.assertTrue(self.q1.battle)

    async def test_unconfirmed_session_and_interiors_name_do_not_enable_reverse(self):
        self.dungeon(self.q1,self.h1,confirmed=False)
        self.q1.zone=self.h1.zone='World/Interiors/Dungeon'
        self.q1.battle,self.h1.battle=False,True
        await self.rescue(self.q1)
        self.assertFalse(self.events)

    async def test_entering_another_duel_is_not_reported_as_joined(self):
        self.dungeon(self.q1,self.h1)
        def wrong_duel(c,kind):
            if kind=='tp' and tuple(c.position)==tuple(self.q1.position):
                self.join=False
                c.battle,c.duel_value=True,999
        self.on_input=wrong_duel
        await self.rescue(self.q1)
        self.assertFalse(any('reason=joined ' in m for m in self.messages()))
        self.assertTrue(any('different-battle' in m for m in self.messages()))

    async def test_forward_and_reverse_groups_remain_isolated(self):
        self.dungeon(self.q1,self.h1)
        self.dungeon(self.q2,self.h2)
        self.q2.battle,self.h2.battle=False,True
        origins={c.title:tuple(c.position) for c in self.clients}
        await asyncio.gather(self.rescue(self.q1),self.rescue(self.q2))
        self.assertTrue(self.h1.battle and self.q2.battle)
        destinations={title:point for title,kind,point in self.events if kind=='tp' and point[2]!=-3000}
        self.assertEqual(destinations,{'p2':origins['p1'],'p3':origins['p4']})
        self.assertEqual(tuple(self.ns['original_client_locations'][self.q2.process_id]),origins['p3'])

    async def test_reverse_safety_gates_release_without_spending_budget(self):
        self.dungeon(self.q1,self.h1)
        self.q1.battle,self.h1.battle=False,True
        for client,flag,value in ((self.q1,'loading',True),(self.q1,'in_solo_zone',True),
                (self.q1,'invincible_combat_timer',True),(self.q1,'quest_party_probe_pending',True),
                (self.h1,'duel_circle_joinable',False),(self.q1,'instance',99)):
            previous=getattr(client,flag)
            setattr(client,flag,value)
            await self.rescue(self.q1)
            self.assertFalse(self.events,(flag,self.events))
            setattr(client,flag,previous)
        self.h1.duel.duel_phase.return_value=DuelPhase.ended
        await self.rescue(self.q1)
        self.assertFalse(self.events)
        self.h1.duel.duel_phase.return_value=DuelPhase.planning
        await self.rescue(self.q1)
        self.clock+=4
        await self.rescue(self.q1)
        self.assertTrue(self.q1.battle)

    async def test_reverse_session_invalidation_stops_before_circle_teleport(self):
        session=self.dungeon(self.q1,self.h1)
        self.q1.battle,self.h1.battle=False,True
        origin=tuple(self.q1.position)
        def invalidate(c,kind):
            if kind=='key':session.confirmed=False
        self.on_input=invalidate
        await self.rescue(self.q1)
        self.assertFalse(self.q1.battle)
        self.assertEqual([p for _,kind,p in self.events if kind=='tp'],[(0,0,-3000),origin])
        self.assertFalse(self.q1.quest_party_battle_rescue_active)

    async def test_task_change_on_actual_join_is_not_reported_as_success(self):
        self.dungeon(self.q1,self.h1)
        def changed(c,kind):
            if kind=='tp' and tuple(c.position)==tuple(self.q1.position):
                self.h1.goal_id.return_value=8
        self.on_input=changed
        await self.rescue(self.q1)
        self.assertTrue(self.h1.battle)
        self.assertFalse(any('reason=joined ' in m for m in self.messages()))
        self.assertTrue(any('task-changed' in m for m in self.messages()))

    async def test_control_change_mid_reverse_never_restores_old_object(self):
        self.dungeon(self.q1,self.h1)
        self.q1.battle,self.h1.battle=False,True
        def changed(c,kind):
            if kind=='key':self.ns['_movement_control_changed'].return_value=True
        self.on_input=changed
        await self.rescue(self.q1)
        self.assertEqual(sum(kind=='tp' for _,kind,_ in self.events),1)
        self.assertFalse(self.q1.quest_party_battle_rescue_active)

    async def test_group_owner_prevents_concurrent_reverse_inputs(self):
        self.dungeon(self.q1,self.h1)
        self.q1.battle,self.h1.battle=False,True
        await asyncio.gather(self.rescue(self.q1),self.rescue(self.q1))
        self.assertTrue(self.q1.battle)
        self.assertEqual(sum(kind=='key' for _,kind,_ in self.events),2)
        self.assertTrue(any('group-rescue-active' in m for m in self.messages()))
        self.assertIsNone(self.q1._quest_party_battle_rescue_owner)

    async def test_reverse_waits_for_existing_movement_without_cancelling_worker(self):
        self.dungeon(self.q1,self.h1)
        self.q1.battle,self.h1.battle=False,True
        released=asyncio.Event()
        drained=[]
        async def movement():
            try:await released.wait()
            finally:drained.append(True)
        task=asyncio.create_task(movement())
        self.q1._quest_active_movement=task
        await asyncio.sleep(0)
        await self.rescue(self.q1)
        try:
            self.assertFalse(task.done())
            self.assertFalse(self.events)
            self.assertTrue(any('movement-active' in m for m in self.messages()))
        finally:
            released.set()
            await asyncio.gather(task,return_exceptions=True)
        self.assertFalse(task.cancelled())
        self.assertEqual(drained,[True])
        await self.rescue(self.q1)
        self.assertTrue(self.q1.battle)

    async def test_reverse_cancel_restores_own_origin_and_releases_owners(self):
        self.dungeon(self.q1,self.h1)
        self.q1.battle,self.h1.battle=False,True
        origin=tuple(self.q1.position)
        def cancel(c,kind):
            if kind=='key':asyncio.current_task().cancel()
        self.on_input=cancel
        task=asyncio.create_task(self.rescue(self.q1))
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(tuple(self.q1.position),origin)
        self.assertFalse(get_client_automation_ownership(self.q1).locked)
        self.assertFalse(self.q1.quest_party_battle_rescue_active)
        self.assertIsNone(self.q1._quest_party_battle_rescue_owner)
        self.assertNotIn(self.q1.process_id,self.ns['original_client_locations'])

    async def test_budget_survives_anchor_switch_and_function_reload(self):
        q,h,other=self.q1,self.h1,self.h2
        for c in (q,h,other):c.hotkey_quest_clients=[q,h,other]
        q.quest_party_hitters=[h,other]
        other.instance=q.instance
        session=self.dungeon(q,h)
        session.members=(q,h,other)
        other.quest_party_dungeon=session
        session.source_zones[id(other)]='Outside'
        self.join=False
        await self.rescue(q)
        self.assertEqual(q._quest_party_battle_rescue_state['attempts'][other.process_id],1)
        q.battle,h.battle=False,True
        self.clock+=10
        self.rescue=load_function('rescue_missing_party_hitters',self.ns)
        await self.rescue(q)
        self.assertEqual(q._quest_party_battle_rescue_state['attempts'][other.process_id],2)
        self.assertTrue(any('anchor=p2' in m or "'anchor': 'p2'" in m for m in self.messages()))


del Base  # The fixture's original tests are run separately, once.

if __name__=='__main__':
    unittest.main()

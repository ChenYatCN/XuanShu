"""Transient task TP frames cannot cancel or bypass the existing landing check."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from wizwalker import XYZ, MemoryReadError
from src import teleport_math as movement
from src.questing import Quester
from src.paths import npc_range_path
import tests.test_walk_navmap_fallback as fixtures

REAL_SLEEP=asyncio.sleep


class LandingTests(unittest.IsolatedAsyncioTestCase):
    setUp_base=fixtures.WalkNavmapFallbackTests.setUp
    solve=fixtures.WalkNavmapFallbackTests.solve
    plan=fixtures.WalkNavmapFallbackTests.plan
    goto=fixtures.WalkNavmapFallbackTests.goto

    def setUp(self):
        self.setUp_base()
        self.position=XYZ(-1136,-144,128)
        self.origin=self.position
        self.target=self.quest_target=self.safe=XYZ(515.261,699.536,197.025)
        self.first_solve=self.target
        self.path=None
        self.started,self.release=asyncio.Event(),asyncio.Event()
        self.bounce=True
        self.range_visible=True
        self.validations=0
        self.client.questing_status=True
        self.reader=Quester(self.client,[self.client],None)
        self.reader._npc_dialogue_visible=AsyncMock(return_value=False)
        self.reader._quest_interaction_signature=AsyncMock(return_value=('Streamportal','Interact'))
        self.stack.enter_context(patch('src.questing.collision_tp',movement.collision_tp))
        visible=AsyncMock(side_effect=lambda _,p:p==npc_range_path and self.range_visible)
        self.stack.enter_context(patch.object(movement,'is_visible_by_path',visible))
        self.stack.enter_context(patch('src.questing.is_visible_by_path',visible))
        self.stack.enter_context(patch.object(movement,'_retreat_toward',AsyncMock(return_value=False)))

    async def teleport(self,dest):
        self.events.append(('tp',dest))
        self.position=dest  # The local actor briefly reports the requested location.
        self.range_visible=True

    async def sleep(self,delay):
        record=getattr(self.client,'_quest_tp_rejected',None)
        if delay==1.0 and isinstance(record,dict) and record.get('pending'):
            self.validations+=1
            self.started.set()
            await self.release.wait()
            if self.bounce:
                self.position=self.origin
        await REAL_SLEEP(0)

    async def begin(self):
        task=asyncio.create_task(self.reader.move_until_quest_interaction(self.client,self.target))
        await asyncio.wait_for(self.started.wait(),1)
        for _ in range(5):
            await REAL_SLEEP(0)
        self.assertFalse(task.done())
        self.assertFalse(await self.reader.quest_interaction_ready(self.client,self.target))
        self.addAsyncCleanup(self.cleanup_task,task)
        return task

    async def cleanup_task(self,task):
        self.release.set()
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)

    async def test_instant_arrival_then_bounce_never_grants_handoff_or_cancels_validation(self):
        task=await self.begin()
        self.release.set()
        self.assertEqual(await asyncio.wait_for(task,1),'failed')
        self.assertEqual(self.validations,1)
        self.assertFalse(await self.reader.quest_interaction_ready(self.client,self.target))
        self.assertEqual(len([e for e in self.events if e[0]=='tp']),1)

    async def test_stable_landing_grants_ordinary_handoff_only_after_existing_validator(self):
        self.bounce=False
        task=await self.begin()
        self.release.set()
        self.assertEqual(await asyncio.wait_for(task,1),'ready')
        self.assertTrue(await self.reader.quest_interaction_ready(self.client,self.target))
        self.assertIsNone(self.client._quest_tp_rejected)

    async def test_cancelled_pending_landing_cannot_use_already_there_shortcut(self):
        task=await self.begin()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(self.client._quest_tp_rejected['pending'])
        self.assertFalse(await self.reader.quest_interaction_ready(self.client,self.target))
        self.release.set()
        self.assertEqual(await self.reader.move_until_quest_interaction(self.client,self.target),'failed')
        self.assertEqual(self.validations,2)
        self.assertFalse(await self.reader.quest_interaction_ready(self.client,self.target))
        self.assertEqual(len(self.events),1)

    async def test_cancelled_pending_but_stable_landing_can_reuse_existing_validator(self):
        self.bounce=False
        task=await self.begin()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.release.set()
        self.assertEqual(await self.reader.move_until_quest_interaction(self.client,self.target),'ready')
        self.assertEqual(self.validations,2)
        self.assertEqual(len(self.events),1)

    async def test_task_or_zone_change_during_validation_ends_old_permission(self):
        for cause in ('task','zone'):
            with self.subTest(cause=cause):
                self.started.clear(); self.release.clear()
                self.position=self.origin; self.client._quest_tp_rejected=None
                task=await self.begin()
                if cause=='task': self.goal_id+=1
                else: self.zone+='Next'
                self.assertEqual(await asyncio.wait_for(task,1),'changed')
                self.assertEqual(len([e for e in self.events if e[0]=='walk']),0)

    async def test_memory_read_failure_is_not_reported_as_confirmed_landing(self):
        self.release.set()
        self.bounce=False
        state=dict(rejected=False,pending=(self.target,self.origin,self.zone))
        with patch.object(self.client.body,'position',AsyncMock(side_effect=MemoryReadError(0x3f800000))):
            self.assertFalse(await movement._arrived(self.client,self.target,self.origin,self.zone,
                                                    validation_state=state))
        self.assertTrue(state['rejected'])

    async def test_watch_uses_moving_clients_zone_instead_of_leaders_zone(self):
        self.bounce=False
        leader=SimpleNamespace(quest_id=AsyncMock(return_value=self.quest_id),
            goal_id=AsyncMock(return_value=self.goal_id),zone_name=AsyncMock(return_value='Leader/OtherRoom'),
            quest_position=self.client.quest_position)
        task=asyncio.create_task(self.reader.move_until_quest_interaction(self.client,self.target,leader))
        self.addAsyncCleanup(self.cleanup_task,task)
        await asyncio.wait_for(self.started.wait(),1)
        for _ in range(5): await REAL_SLEEP(0)
        self.assertFalse(task.done())
        self.release.set()
        self.assertEqual(await asyncio.wait_for(task,1),'ready')

    async def test_bounce_after_validator_before_x_revokes_permission_even_inside_750(self):
        self.bounce=False
        self.position=self.origin=XYZ(self.target.x-182,self.target.y,self.target.z)
        self.range_visible=False  # No prompt before this genuine TP operation.
        task=await self.begin()
        self.release.set()
        self.assertEqual(await asyncio.wait_for(task,1),'ready')
        self.position=self.origin
        self.assertFalse(await self.reader.quest_interaction_ready(self.client,self.target))
        self.assertTrue(self.client._quest_tp_rejected['rejected'])

    async def test_new_goal_is_not_blocked_by_old_landing_license(self):
        self.bounce=False
        self.position=self.origin=XYZ(self.target.x-182,self.target.y,self.target.z)
        self.range_visible=False
        task=await self.begin()
        self.release.set()
        self.assertEqual(await asyncio.wait_for(task,1),'ready')
        self.position=self.origin
        self.goal_id+=1
        self.assertFalse(await self.reader.quest_interaction_ready(self.client,self.target))
        self.assertEqual(await self.reader.move_until_quest_interaction(self.client,self.target),'ready')
        self.assertEqual(len([e for e in self.events if e[0]=='tp']),2)
        self.assertTrue(await self.reader.quest_interaction_ready(self.client,self.target))


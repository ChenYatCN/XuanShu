"""Mainline recovery through a simulated, dynamically enumerated quest book."""
import asyncio
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode, XYZ
from src.mainline_progress import log_mainline_progress
from src.automation_ownership import get_client_automation_ownership

REAL_SLEEP = asyncio.sleep
ROWS = [
    dict(world='Mirage', number=5, total=10, english='Previous Quest', chinese=['原主线'],
         keys=['Quest_prev'], quest_ids=[42], aliases=[]),
    dict(world='Mirage', number=6, total=10, english='Next Quest', chinese=['下一主线'],
         keys=['Quest_next'], quest_ids=[43], aliases=[]),
    dict(world='Other', number=1, total=10, english='Other Mainline', chinese=[],
         keys=['Quest_wrong'], quest_ids=[77], aliases=[]),
]


class Window:
    def __init__(self, name, client, text='', children=None, action=None):
        self.label, self.client, self.text = name, client, text
        self.nodes, self.action = children or [], action

    async def name(self):
        return self.label

    async def is_visible(self):
        return self.label in ('root', 'WorldView') or self.client.book_open

    async def children(self):
        if self.label == 'wndQuestList':
            return self.client.page_nodes() + self.nodes
        return self.nodes

    async def maybe_text(self):
        if self.label == 'txtQuestTitle':
            self.client.events.append(('read_title', self.text))
        return self.text

    async def is_control_grayed(self):
        return False


class Mouse:
    def __init__(self, client):
        self.client, self.active = client, False

    async def __aenter__(self):
        self.active = True
        return self

    async def __aexit__(self, *args):
        self.active = False

    async def click_window(self, window, **kwargs):
        self.client.events.append(('click', window.label))
        if window.action:
            window.action()


class MainlineClient:
    def __init__(self, title='p1'):
        self.title = title
        self.tracked = 42
        self.questing_status = self.mainline_finder_enabled = self._mainline_recovery_quester = True
        self.loading = self.battle = self.refilling_potions = False
        self.quest_recovery_owner = None
        self.book_open = False
        self.page = 0
        self.pages = [['原主线'], ['任务搜寻']]
        self.page_click_works = True
        self.events = []
        self.mouse_handler = Mouse(self)
        self.quests = {qid: SimpleNamespace(name_lang_key=AsyncMock(return_value=key),
                      mainline=AsyncMock(return_value=True))
                       for qid, key in [(42,'Quest_prev'), (43,'Quest_next'), (77,'Quest_wrong'), (999,'Quest Finder')]}
        self.quest_manager = AsyncMock(return_value=SimpleNamespace(quest_data=AsyncMock(side_effect=lambda: self.quests)))
        self.cache_handler = SimpleNamespace(get_langcode_name=AsyncMock(side_effect=lambda key:
            dict(Quest_prev='原主线', Quest_next='下一主线', Quest_wrong='Other Mainline').get(key,key)))
        self.quest_id = AsyncMock(side_effect=lambda: self.tracked)
        self.goal_id = AsyncMock(return_value=7)
        self.zone_name = AsyncMock(return_value='Mirage/Area')
        self.is_loading = AsyncMock(side_effect=lambda: self.loading)
        self.in_battle = AsyncMock(side_effect=lambda: self.battle)
        self.quest_position = SimpleNamespace(position=AsyncMock(side_effect=lambda: XYZ(self.tracked,100,0)))
        self.body = SimpleNamespace(position=AsyncMock(return_value=XYZ(0,0,0)))
        all_button = Window('QuestLogAllButton', self, action=lambda: self.events.append(('all',)))
        def previous():
            if self.page_click_works:
                self.page = (self.page+1) % len(self.pages)
        left = Window('btnPrevPage', self, action=previous)
        # The arrow is outside wndQuestList, inside a sibling panel. Enumerating
        # the actual DeckConfiguration hierarchy must find it without coordinates.
        self.menu = Window('wndQuestList', self, children=[all_button])
        deck = Window('DeckConfiguration', self, children=[self.menu, Window('Pagination',self,children=[left])])
        self.root_window = Window('root', self, children=[Window('WorldView',self,children=[deck])])

    def page_nodes(self):
        cards = []
        for i, title in enumerate(self.pages[self.page]):
            qid = {'原主线':42,'下一主线':43,'Other Mainline':77,'任务搜寻':999}.get(title,77)
            goal = Window('txtGoal',self,text='Talk to NPC',action=lambda qid=qid:setattr(self,'tracked',qid))
            cards.append(Window(f'wndQuestInfo{i}',self,children=[Window('questInfoWindow',self,children=[
                Window('wndQuestInfo',self,children=[Window('txtQuestTitle',self,text=title),goal])])]))
        return cards

    async def send_key(self, key, seconds=0):
        self.events.append(('key',key))
        if key == Keycode.Q:
            self.book_open = not self.book_open
        await REAL_SLEEP(0)


class MainlineRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = MainlineClient()
        self.clock = 0
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('src.mainline_progress.quest_rows',return_value=ROWS))
        self.stack.enter_context(patch('src.mainline_progress.installed_catalog',return_value=None))

    async def initialize(self):
        await log_mainline_progress(self.client)

    async def tick(self, seconds):
        self.clock += seconds
        await REAL_SLEEP(0)

    def runtime(self):
        from src import mainline_recovery as recovery
        if not getattr(self,'runtime_loaded',False):
            self.runtime_loaded = True
            self.stack.enter_context(patch.object(recovery,'monotonic',lambda:self.clock))
            self.stack.enter_context(patch.object(recovery,'sleep',self.tick))
            self.stack.enter_context(patch.object(recovery,'read_control_text',lambda window:window.maybe_text()))
            self.stack.enter_context(patch.object(recovery,'is_free',AsyncMock(return_value=True)))
        return recovery

    async def recover(self, client=None, times=3):
        recovery = self.runtime()
        result = None
        for _ in range(times):
            self.clock += 2
            result = await recovery.maybe_recover_mainline(client or self.client)
        return result

    async def test_last_success_survives_mismatch_and_read_failure(self):
        await self.initialize()
        last = getattr(self.client,'_xuanshu_mainline_last_success',None)
        self.assertIsNotNone(last)
        self.assertEqual((last['world'],last['number'],last['title']),('Mirage',5,'原主线'))
        self.client.tracked = 77  # Also game-mainline, but the old mainline is still owned.
        await log_mainline_progress(self.client)
        self.assertEqual(self.client._xuanshu_mainline_last_success,last)
        self.assertNotEqual(self.client._xuanshu_mainline_progress,'p1 · Mirage 主线 5/10')
        self.client.quest_id.side_effect = RuntimeError('transient read')
        await log_mainline_progress(self.client)
        self.assertEqual(self.client._xuanshu_mainline_last_success,last)

    async def test_restore_previous_title_after_all_quests(self):
        await self.initialize()
        self.client.tracked = 77
        await self.recover()
        self.assertEqual(self.client.tracked,42)
        self.assertFalse(self.client.book_open)
        self.assertLess(self.client.events.index(('all',)),self.client.events.index(('read_title','原主线')))

    async def test_restore_next_number_title(self):
        await self.initialize()
        self.client.tracked = 77
        self.client.pages = [['下一主线']]
        await self.recover()
        self.assertEqual(self.client.tracked,43)
        self.assertEqual(self.client._xuanshu_mainline_last_success['number'],6)

    async def test_left_page_then_finder_keeps_auto_quest_paused_until_match(self):
        await self.initialize()
        self.client.tracked = 77
        self.client.pages = [['Other Mainline'],['任务搜寻']]
        await self.recover(times=2)
        self.assertEqual(self.client.tracked,999)
        self.assertIn(('click','btnPrevPage'),self.client.events)
        self.assertFalse(self.client.book_open)
        opened = sum(e==('key',Keycode.Q) for e in self.client.events)
        for _ in range(3):
            self.assertTrue(await self.recover(times=1))
        self.assertEqual(sum(e==('key',Keycode.Q) for e in self.client.events),opened)
        self.assertEqual(self.client._xuanshu_mainline_last_success['number'],5)

    async def test_page_click_without_change_is_bounded(self):
        await self.initialize()
        self.client.tracked = 77
        self.client.pages = [['Other Mainline'],['任务搜寻']]
        self.client.page_click_works = False
        await self.recover()
        self.assertLessEqual(sum(e==('click','btnPrevPage') for e in self.client.events),3)
        self.assertEqual(self.client.tracked,77)
        self.assertFalse(self.client.book_open)

    async def test_acceptance_rematches_before_updating_last_record(self):
        await self.initialize()
        self.client.tracked = 77
        self.client.pages = [['任务搜寻']]
        await self.recover(times=2)
        self.client.tracked = 43
        await log_mainline_progress(self.client)
        self.assertEqual(self.client._xuanshu_mainline_last_success['number'],5)
        await self.recover()
        self.assertEqual(self.client._xuanshu_mainline_last_success['number'],6)

    async def test_missing_record_and_hitter_do_not_open_book(self):
        self.client.tracked = 123
        await self.recover()
        self.assertFalse(self.client.events)
        self.assertIsNone(getattr(self.client,'_xuanshu_mainline_last_success',None))
        self.client.tracked = 42
        await self.initialize()
        self.client._mainline_recovery_quester = False
        await self.recover()
        self.assertFalse(self.client.events)

    async def test_refill_or_control_owner_defers_recovery(self):
        await self.initialize()
        self.client.tracked = 77
        for attr,value in [('refilling_potions',True),('quest_recovery_owner','potion')]:
            setattr(self.client,attr,value)
            await self.recover()
            setattr(self.client,attr,False if isinstance(value,bool) else None)
        self.assertFalse(self.client.events)
        await self.recover()
        self.assertEqual(self.client.tracked,42)

    async def test_clients_keep_separate_last_records_and_navigation_state(self):
        await self.initialize()
        other = MainlineClient('p2'); other.tracked=43
        await log_mainline_progress(other)
        self.client.tracked=77; self.client.pages=[['任务搜寻']]
        await self.recover(times=2)
        self.assertEqual(other._xuanshu_mainline_last_success['number'],6)
        self.assertFalse(other.events)
        self.assertEqual(self.client._xuanshu_mainline_last_success['number'],5)

    async def test_disable_and_cancel_release_ui_and_ownership(self):
        await self.initialize()
        self.client.tracked=77
        self.client.pages=[['Other Mainline'],['任务搜寻']]
        recovery=self.runtime()
        original=self.client.mouse_handler.click_window
        started, release=asyncio.Event(), asyncio.Event()
        async def click(window,**kwargs):
            if window.label=='btnPrevPage':
                started.set()
                await release.wait()
            await original(window,**kwargs)
        self.client.mouse_handler.click_window=click
        await self.recover(times=1)
        task=asyncio.create_task(self.recover(times=1))
        await asyncio.wait_for(started.wait(),1)
        task.cancel()
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.client.book_open)
        self.assertFalse(self.client.mouse_handler.active)
        self.assertFalse(get_client_automation_ownership(self.client).locked)
        self.client.mainline_finder_enabled=False
        recovery.cancel_mainline_recovery(self.client)
        self.assertFalse(await self.recover(times=1))

    async def test_wrong_accepted_mainline_never_overwrites_anchor_and_retries_are_finite(self):
        await self.initialize()
        self.client.tracked=77; self.client.pages=[['任务搜寻']]
        await self.recover(times=2)
        self.client.quests.pop(42)  # Even if the original quest vanished on turn-in.
        self.client.tracked=77
        self.assertTrue(await self.recover(times=1))
        self.assertTrue(await self.recover(times=1))
        await log_mainline_progress(self.client)
        self.assertEqual(self.client._xuanshu_mainline_last_success['number'],5)
        self.assertIn('未匹配',self.client._xuanshu_mainline_progress)
        for _ in range(6):
            self.clock+=61
            await self.recover(times=1)
            if self.client.tracked==999:
                self.client.tracked=77
                await self.recover(times=2)
        self.assertLessEqual(sum(e==('all',) for e in self.client.events),3)
        self.assertEqual(self.client._xuanshu_mainline_last_success['world'],'Mirage')

    async def test_disable_mid_book_cancels_only_ui_pass(self):
        await self.initialize()
        self.client.tracked=77; self.client.pages=[['Other Mainline'],['任务搜寻']]
        recovery=self.runtime()
        original=self.client.mouse_handler.click_window
        started,release=asyncio.Event(),asyncio.Event()
        async def click(window,**kwargs):
            if window.label=='btnPrevPage':
                started.set(); await release.wait()
            await original(window,**kwargs)
        self.client.mouse_handler.click_window=click
        await self.recover(times=1)
        task=asyncio.create_task(self.recover(times=1))
        await asyncio.wait_for(started.wait(),1)
        self.client.mainline_finder_enabled=False
        recovery.cancel_mainline_recovery(self.client)
        release.set()
        self.assertFalse(await task)
        self.assertTrue(self.client.questing_status)
        self.assertFalse(self.client.book_open)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_cancel_during_q_press_still_closes_opened_book(self):
        await self.initialize()
        self.client.tracked=77
        self.runtime()
        original=self.client.send_key
        started,release=asyncio.Event(),asyncio.Event()
        first=[True]
        async def send_key(key,seconds=0):
            await original(key,seconds)
            if key==Keycode.Q and first[0]:
                first[0]=False
                started.set(); await release.wait()
        self.client.send_key=send_key
        await self.recover(times=1)
        task=asyncio.create_task(self.recover(times=1))
        await asyncio.wait_for(started.wait(),1)
        task.cancel(); release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.client.book_open)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_stopping_auto_quest_clears_navigation_but_keeps_last_success(self):
        await self.initialize()
        self.client.tracked=77; self.client.pages=[['任务搜寻']]
        await self.recover(times=2)
        self.client.questing_status=False
        self.assertFalse(await self.recover(times=1))
        self.assertIsNone(self.client._xuanshu_mainline_recovery)
        self.assertEqual(self.client._xuanshu_mainline_last_success['number'],5)
        self.assertFalse(get_client_automation_ownership(self.client).locked)


if __name__=='__main__':
    unittest.main()

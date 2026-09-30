import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode
from src.questing import Quester
from src.mainline_progress import quest_rows


class OwnedMainlineTrackingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.row = next(row for row in quest_rows() if row['english'] == 'Extra Life')
        self.now = 0.0
        self.page = 0
        self.open = False
        self.selected = False
        quest = SimpleNamespace(name_lang_key=AsyncMock(return_value='QuestTitle_162472'))
        self.client = SimpleNamespace(
            quest_manager=AsyncMock(return_value=SimpleNamespace(quest_data=AsyncMock(return_value={42: quest}))),
            cache_handler=SimpleNamespace(get_langcode_name=AsyncMock(return_value='Extra Life')),
            zone_name=AsyncMock(return_value=self.row['world'].split('(')[0].strip() + '/Area'),
            send_key=AsyncMock(side_effect=self.key),
        )
        self.quester = Quester(self.client, [self.client], None)
        self.quester._questbook_page = AsyncMock(side_effect=self.scan)
        self.quester._click_ui_window = AsyncMock(side_effect=self.click)
        self.quester._mainline_identity = AsyncMock(side_effect=lambda c: (
            (42, 'QuestTitle_162472', 'Extra Life', self.row, True) if self.selected else None))
        for patcher in (
            patch('src.questing.time.monotonic', side_effect=lambda: self.now),
            patch('src.questing.asyncio.sleep', new=AsyncMock(side_effect=self.sleep)),
            patch('src.questing.is_visible_by_path', new=AsyncMock(side_effect=lambda *args: self.open)),
            patch('src.questing.is_free_leader_questing', new=AsyncMock(return_value=True)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def key(self, key):
        self.assertEqual(key, Keycode.Q)
        self.open = not self.open

    async def sleep(self, seconds):
        self.now += seconds

    async def scan(self, client, mainlines=None):
        if mainlines is not None and self.page == 1:
            mainlines.append(('Extra Life', ('mainline', ('card', 'txtGoal'))))
        return ((str(self.page),), None, ('right', ('btnRight',)))

    async def click(self, client, target):
        if target == 'right':
            self.page = (self.page + 1) % 2
        else:
            self.selected = True

    async def test_owned_mainline_on_later_page_restores_and_closes_before_return(self):
        self.assertTrue(await self.quester._restore_owned_mainline(self.client))
        self.assertTrue(self.selected)
        self.assertFalse(self.open)
        self.assertGreaterEqual(self.now, 3)

    async def test_other_world_mainline_does_not_get_selected(self):
        self.client.zone_name.return_value = 'AnotherWorld/Area'
        self.assertFalse(await self.quester._restore_owned_mainline(self.client))
        self.assertFalse(self.selected)
        self.assertFalse(self.open)

    async def test_group_expected_id_can_restore_independently_of_zone(self):
        self.client.zone_name.return_value = 'Arcanum/Area'
        self.assertTrue(await self.quester._restore_owned_mainline(self.client, expected_id=42))
        self.assertFalse(self.open)

    async def test_tracking_read_failure_never_reports_task_absent(self):
        self.quester._mainline_identity = AsyncMock(return_value=None)
        with self.assertRaisesRegex(RuntimeError, '主线已在任务列表'):
            await self.quester._restore_owned_mainline(self.client)
        self.assertFalse(self.open)

    async def test_existing_open_menu_is_closed_after_scan(self):
        self.open = True
        self.assertTrue(await self.quester._restore_owned_mainline(self.client))
        self.assertFalse(self.open)

    async def test_close_failure_keeps_movement_blocked(self):
        self.open = True
        self.client.send_key.side_effect = None
        with self.assertRaisesRegex(RuntimeError, '任务菜单未成功关闭'):
            await self.quester._close_questbook(self.client)

    async def test_failed_page_turn_never_claims_mainline_absent(self):
        self.quester._click_ui_window = AsyncMock()
        with self.assertRaisesRegex(RuntimeError, '翻页未确认'):
            await self.quester._restore_owned_mainline(self.client)
        self.assertFalse(self.open)

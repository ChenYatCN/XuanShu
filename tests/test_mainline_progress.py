import unittest
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from src.mainline_progress import match_quest, log_mainline_progress, quest_rows
from src.collect_matching import CollectNames
from src.questing import Quester
from wizwalker import XYZ, Keycode


class MainlineTests(unittest.IsolatedAsyncioTestCase):
    def test_live_elephant_march_identity_matches_existing_quest_84(self):
        rows = quest_rows()
        for quest_id, code, title in (
            (121315715316472848, '', ''),
            (0, 'QuestTitle_80B3E', ''),
            (0, '', 'Elephant March'),
            (0, '', '大象游行'),
            (0, '', 'Elephant Queen'),
        ):
            with self.subTest(quest_id=quest_id, code=code, title=title):
                row = match_quest(rows, quest_id, code, title)
                self.assertIsNotNone(row)
                self.assertEqual((row['world'], row['number'], row['total']), ('zafariA', 84, 148))
        self.assertIsNone(match_quest(rows, 160440737060796615, 'QuestTitle_71A3D', 'Bad Vacation'))

    async def test_solo_quest_ride_prompt_near_target_sends_x(self):
        # Exercise the existing interaction branch, not a live game client.
        tree = ast.parse(Path('src/questing.py').read_text(encoding='utf-8'))
        method = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.AsyncFunctionDef) and node.name == 'auto_quest_solo')
        branch = next(node for node in ast.walk(method)
                      if isinstance(node, ast.If) and 'npc_range_path' in ast.unparse(node.test)
                      and 'quest_xyz' in ast.unparse(node.test))
        function = ast.AsyncFunctionDef(
            name='interact', args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[],
                kw_defaults=[], defaults=[]), body=[branch], decorator_list=[])
        client = SimpleNamespace(title='p1', send_key=AsyncMock())
        quester = Quester(client, [client], None)
        quester.read_popup = AsyncMock(return_value='按下 &InputBindings_NPCInteract& 骑乘')
        quester._maybe_photo_giant_vat = AsyncMock(return_value=False)
        from src.interaction_prompts import interaction_kind, is_dungeon_entry_prompt
        from src.paths import npc_range_path
        namespace = dict(self=quester, quest_xyz=XYZ(0, 0, 0), current_pos=XYZ(114, 0, 0),
            npc_range_path=npc_range_path, Keycode=Keycode,
            calc_Distance=lambda a, b: abs(a.x - b.x),
            is_visible_by_path=AsyncMock(return_value=True),
            is_spiral_door_open=AsyncMock(return_value=False),
            is_dungeon_entry_prompt=is_dungeon_entry_prompt, interaction_kind=interaction_kind,
            asyncio=SimpleNamespace(sleep=AsyncMock()))
        exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])),
                     'quest-interaction-branch', 'exec'), namespace)
        self.assertEqual(interaction_kind(await quester.read_popup(client)), 'ride')
        await namespace['interact']()
        client.send_key.assert_awaited_once_with(Keycode.X, .1)

    def test_live_novus_monkey_business_identity_is_unique(self):
        rows = quest_rows()
        for quest_id, code, title in (
                (147211413153260846, '', ''),
                (0, 'QuestTitle_17D615', 'Monkey Business'),
                (0, 'QuestTitle_17D615', '猴子生意')):
            row = match_quest(rows, quest_id, code, title)
            self.assertEqual((row['world'], row['number']), ('novus', 56))
        self.assertIsNone(match_quest(rows, 0, '', 'Monkey Business'))
        for code in ('QuestTitle_155196', 'QuestTitle_AB2D5'):
            self.assertIsNone(match_quest(rows, 0, code, 'Monkey Business'))

    def test_id_key_bilingual_notes_and_ambiguity(self):
        row = dict(world='celestia(100)', number=27, english='Example Quest (returns to previous)',
                   keys=['Quest_123'], quest_ids=[42], chinese=[], aliases=['Old Example'])
        self.assertIs(match_quest([row], 42, '', ''), row)
        self.assertIs(match_quest([row], 0, 'Quest_123', ''), row)
        names = CollectNames([['Quest_123', 'Example Quest', '示例任务']])
        self.assertIs(match_quest([row], 0, '', '示例任务', names), row)
        self.assertIs(match_quest([row], 0, '', 'Old Example'), row)
        self.assertIsNone(match_quest([row, dict(row, number=28)], 0, '', 'Example Quest'))
        self.assertIsNone(match_quest([row], 99, '', 'Collect Example Quest'))

    async def test_logs_once_per_identity_not_objective(self):
        quest = SimpleNamespace(mainline=AsyncMock(return_value=True), name_lang_key=AsyncMock(return_value='Quest_123'))
        manager = SimpleNamespace(quest_data=AsyncMock(return_value={42: quest, 99: quest}))
        client = SimpleNamespace(title='p2', quest_id=AsyncMock(return_value=42),
            quest_manager=AsyncMock(return_value=manager),
            cache_handler=SimpleNamespace(get_langcode_name=AsyncMock(return_value='示例任务')))
        with patch('src.mainline_progress.quest_rows', return_value=[]), \
             patch('src.mainline_progress.logger') as log:
            await log_mainline_progress(client)
            await log_mainline_progress(client)
            self.assertEqual(log.info.call_count, 1)
            self.assertIn('未匹配', log.info.call_args.args[0])
            client.quest_id.return_value = 99
            await log_mainline_progress(client)
            self.assertEqual(log.info.call_count, 2)
            client.quest_id.side_effect = RuntimeError('unavailable')
            await log_mainline_progress(client)

    def test_bundled_index_has_world_totals_and_verified_language_keys(self):
        row = next(row for row in quest_rows()
                   if row['world'] == 'celestia' and row['number'] == 27)
        self.assertEqual(row['total'], 100)
        self.assertIn('QuestTitle_00000149', row['keys'])
        self.assertIn('把碎片放在一起', row['chinese'])
        self.assertEqual(row['quest_ids'], [])

    async def test_key_match_logs_chinese_title_for_each_client_once(self):
        quest = SimpleNamespace(
            mainline=AsyncMock(return_value=True),
            name_lang_key=AsyncMock(return_value='QuestTitle_00000149'),
        )
        manager = SimpleNamespace(quest_data=AsyncMock(return_value={42: quest}))
        def client(title):
            return SimpleNamespace(
                title=title, quest_id=AsyncMock(return_value=42),
                quest_manager=AsyncMock(return_value=manager),
                cache_handler=SimpleNamespace(
                    get_langcode_name=AsyncMock(return_value='Putting Pieces Together')),
            )
        p1, p2 = client('p1'), client('p2')
        with patch('src.mainline_progress.logger') as log:
            await log_mainline_progress(p1)
            await log_mainline_progress(p2)
            self.assertEqual(p1._xuanshu_mainline_progress, 'p1 · 天国 主线 27/100')
            await log_mainline_progress(p1)
            self.assertEqual(log.info.call_count, 2)
            self.assertEqual(log.info.call_args_list[0].args,
                             ('{} 当前主线：{} 第 {}/{} 个 | {}',
                              'p1', '天国', 27, 100, '把碎片放在一起'))
            self.assertEqual(log.info.call_args_list[1].args[1], 'p2')
            p2.quest_id.return_value = 43
            await log_mainline_progress(p2)
            self.assertEqual(log.info.call_count, 3)
            self.assertEqual(log.info.call_args_list[-1].args,
                             ('{} 当前主线：未匹配 | {}', 'p2', 'Quest ID: 43'))

    async def test_confirmed_ui_progress_survives_gaps_until_new_identity_resolves(self):
        quest = SimpleNamespace(mainline=AsyncMock(return_value=True),
                                name_lang_key=AsyncMock(return_value='QuestTitle_00000149'))
        manager = SimpleNamespace(quest_data=AsyncMock(return_value={42: quest}))
        client = SimpleNamespace(title='p1', quest_id=AsyncMock(return_value=42),
            quest_manager=AsyncMock(return_value=manager),
            cache_handler=SimpleNamespace(get_langcode_name=AsyncMock(return_value='')))
        with patch('src.mainline_progress.logger') as log:
            await log_mainline_progress(client)
            confirmed = client._xuanshu_mainline_progress
            self.assertEqual(confirmed, 'p1 · 天国 主线 27/100')
            client.quest_id.side_effect = RuntimeError('loading')
            await log_mainline_progress(client)
            self.assertEqual(client._xuanshu_mainline_progress, confirmed)
            client.quest_id.side_effect = None
            client.quest_id.return_value = 0
            await log_mainline_progress(client)
            self.assertEqual(client._xuanshu_mainline_progress, confirmed)
            client.quest_id.return_value = 43
            await log_mainline_progress(client)
            await log_mainline_progress(client)
            self.assertEqual(client._xuanshu_mainline_progress, confirmed)
            self.assertEqual(log.info.call_count, 2)  # confirmed + one unresolved log
            # The same ID becomes readable later; it must still be retried.
            quest.name_lang_key.return_value = 'QuestTitle_17D615'
            manager.quest_data.return_value = {43: quest}
            await log_mainline_progress(client)
            self.assertEqual(client._xuanshu_mainline_progress, 'p1 · novus 主线 56/88')
            client.quest_id.return_value = 44
            quest.mainline.return_value = False
            manager.quest_data.return_value = {44: quest}
            await log_mainline_progress(client)
            self.assertEqual(client._xuanshu_mainline_progress, 'p1 · novus 主线 56/88')

    async def test_language_key_still_matches_when_title_lookup_fails(self):
        quest = SimpleNamespace(
            mainline=AsyncMock(return_value=True),
            name_lang_key=AsyncMock(return_value='QuestTitle_1627B5'),
        )
        manager = SimpleNamespace(quest_data=AsyncMock(return_value={18: quest}))
        client = SimpleNamespace(
            title='p1', quest_id=AsyncMock(return_value=18),
            quest_manager=AsyncMock(return_value=manager),
            cache_handler=SimpleNamespace(
                get_langcode_name=AsyncMock(side_effect=ValueError('missing language'))),
        )
        with patch('src.mainline_progress.logger') as log:
            await log_mainline_progress(client)
            self.assertEqual(log.info.call_args.args,
                             ('{} 当前主线：{} 第 {}/{} 个 | {}',
                              'p1', '魔法城', 18, 39, '晶莹剔透'))

    async def test_known_side_quest_is_not_logged_even_if_key_matches(self):
        quest = SimpleNamespace(
            mainline=AsyncMock(return_value=False),
            name_lang_key=AsyncMock(return_value='QuestTitle_00000149'),
        )
        manager = SimpleNamespace(quest_data=AsyncMock(return_value={42: quest}))
        client = SimpleNamespace(
            title='p1', quest_id=AsyncMock(return_value=42),
            quest_manager=AsyncMock(return_value=manager),
            cache_handler=SimpleNamespace(get_langcode_name=AsyncMock(return_value='Putting Pieces Together')),
        )
        with patch('src.mainline_progress.logger') as log:
            await log_mainline_progress(client)
            log.info.assert_not_called()

    async def test_index_error_does_not_stop_questing(self):
        client = SimpleNamespace(title='p1', quest_id=AsyncMock(return_value=42))
        with patch('src.mainline_progress.quest_rows', side_effect=ValueError('bad index')):
            await log_mainline_progress(client)

    async def test_solo_quest_iteration_calls_progress_logger_before_movement(self):
        client = AsyncMock()
        client.title = 'p1'
        client.questing_status = True
        client.auto_pet_status = False
        client.use_potions = False
        client.mainline_finder_enabled = False
        client.entity_detect_combat_status = False
        client.quest_position.position.return_value = XYZ(100, 0, 0)
        quester = Quester(client, [client], None)
        quester._quest_dialogue_blocks_movement = AsyncMock(return_value=False)
        quester.handle_pending_dungeon_confirmation = AsyncMock(return_value=False)
        quester._quest_party_probe_blocks_movement = AsyncMock(return_value=False)
        quester._maybe_refresh_stalled_dungeon_quest = AsyncMock(return_value=True)
        with (patch('src.questing.close_npc_quest_menu', new=AsyncMock(return_value=False)),
              patch('src.questing.close_automation_popup', new=AsyncMock(return_value=False)),
              patch('src.questing.is_spiral_door_open', new=AsyncMock(return_value=False)),
              patch('src.questing.is_free', new=AsyncMock(return_value=True)),
              patch('src.questing.is_potion_needed', new=AsyncMock(return_value=False)),
              patch('src.mainline_progress.log_mainline_progress', new=AsyncMock()) as progress):
            await quester.auto_quest_solo()
        progress.assert_awaited_once_with(client)
        client.teleport.assert_not_awaited()

    async def test_legacy_leader_iteration_logs_each_questing_client(self):
        p1, p2 = AsyncMock(), AsyncMock()
        for number, client in enumerate((p1, p2), 1):
            client.title = f'p{number}'
            client.questing_status = True
            client.auto_pet_status = False
            client.process_id = number
            client.zone_name.return_value = 'WizardCity/Area'
        quester = Quester(p1, [p1, p2], 1)
        quester._quest_dialogue_blocks_movement = AsyncMock(return_value=False)
        quester.get_follower_clients = AsyncMock(return_value=[])
        quester.get_questing_clients = AsyncMock(return_value=[p1, p2])
        quester.bring_clients_to_same_location = AsyncMock()
        quester.determine_solo_zone = AsyncMock(return_value=False)
        quester.get_client_quests = AsyncMock(return_value={})
        quester.leader_wait_for_free = AsyncMock()
        quester.heal_and_handle_potions = AsyncMock()
        quester.handle_dungeon_recall = AsyncMock()
        quester.auto_pet_questing = AsyncMock()
        quester.determine_new_leader_and_followers = AsyncMock(return_value=([], {}))
        quester.handle_zone_correction = AsyncMock()
        seen = []
        async def progress(client):
            seen.append(client.title)
            if len(seen) == 2:
                p1.questing_status = False
        with (patch('src.questing.get_quest_name', new=AsyncMock(return_value='Same quest')),
              patch('src.questing.is_free_leader_questing', new=AsyncMock(return_value=False)),
              patch('src.mainline_progress.log_mainline_progress', side_effect=progress)):
            await quester.auto_quest_leader(False, False, None, False, False)
        self.assertCountEqual(seen, ['p1', 'p2'])

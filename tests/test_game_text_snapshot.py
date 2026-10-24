"""One validated language profile per actual installation and application run."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib
import importlib.util
import os
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch

from tests.test_collect_catalog import lang,wad


class LanguageSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('src.game_text_snapshot'),
                             'per-installation runtime language snapshot is missing')
        self.module=importlib.import_module('src.game_text_snapshot')
        self.module._SNAPSHOTS.clear()
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.install=Path(self.temp.name)/'Live';self.write_install(self.install,'新星界图','解封')
        self.client=SimpleNamespace(cache_handler=SimpleNamespace(install_location=self.install))

    def write_install(self,install,title,verb,*,collect='采集新版',unknown=False):
        data=install/'Data/GameData';data.mkdir(parents=True,exist_ok=True)
        tables={
            'GUI':[('00001017','','Spiral Map')],
            'GUI2':[('00001561','','Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Open'),
                    ('_COLLECT','','Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Collect')],
            'WizardQuestGoals':[('Kill','','Defeat'),('00000670','','Press Z to Photomance a Photo of')],
            'WizardZone':[('00001763','','Outsiders Camp')],
        }
        wad(data/'Root.wad',[(f'Locale/en-US/{key}.lang',lang(key,rows),True) for key,rows in tables.items()])
        translated={
            'GUI':[('00001017','developer description',title)],
            'GUI2':[('00001561','not a second English alias',f'按 &Icons_XKey& 或 &Icons_LeftMouseClick& {verb}'),
                    ('_COLLECT','',f'按 &Icons_XKey& 或 &Icons_LeftMouseClick& {collect}')],
            'WizardQuestGoals':[('Kill','Defeat','打败新版'),('00000670','','按Z拍摄新版')],
            'WizardZone':[('00001763','Outsiders Camp','营地新版')],
        }
        if unknown:translated['GUI2'].append(('999999','Press &InputBindings_NPCInteract& to Ride','按 X 乘灵舟'))
        wad(data/'Locale_en-US-root.wad',[(f'Locale/en-US/{key}.lang',lang(key,rows),True)
                                        for key,rows in translated.items()])

    def test_effective_translation_replaces_fixed_chinese_and_retains_english_and_id(self):
        from src.interaction_prompts import matches_text,interaction_kind,quest_has_action,photomancy_action
        self.assertTrue(matches_text('新星界图','GUI_00001017',client=self.client))
        self.assertFalse(matches_text('漩涡星系图','GUI_00001017',client=self.client))
        self.assertTrue(matches_text('Spiral Map','GUI_00001017',client=self.client))
        self.assertTrue(matches_text('<center><string;GUI_00001017></center>','GUI_00001017',client=self.client))
        self.assertEqual(interaction_kind('按 X 或 解封',client=self.client),'open')
        self.assertTrue(quest_has_action('打败新版 怪物 地点：营地','defeat',client=self.client))
        self.assertFalse(quest_has_action('击败 怪物 地点：营地','defeat',client=self.client))
        self.assertTrue(photomancy_action('按Z拍摄新版 石柱',client=self.client))
        self.assertFalse(matches_text('developer description','GUI_00001017',client=self.client))

    def test_same_run_never_rereads_and_restart_gets_new_overlay_value(self):
        first=self.module.snapshot_for_install(self.install)
        self.write_install(self.install,'另一次启动的新图','开启新版')
        with patch.object(self.module,'_load_snapshot',side_effect=AssertionError('snapshot must not refresh')):
            self.assertIs(self.module.snapshot_for_install(self.install),first)
            self.assertIn('新星界图',first.values('GUI_00001017'))
        self.module._SNAPSHOTS.clear()  # Simulate a new process; no persistent cache exists.
        restarted=self.module.snapshot_for_install(self.install)
        self.assertIn('另一次启动的新图',restarted.values('GUI_00001017'))
        self.assertNotIn('新星界图',restarted.values('GUI_00001017'))
        with self.assertRaises(TypeError):restarted.records['GUI_00001017']=('bad',)

    def test_fresh_process_reads_patch_update_while_existing_profile_remains_frozen(self):
        old=self.module.snapshot_for_install(self.install)
        self.write_install(self.install,'新进程星图','新进程动作')
        script=('import json; from src.game_text_snapshot import snapshot_for_install; '
                f'print(json.dumps(snapshot_for_install({str(self.install)!r}).values("GUI_00001017")))')
        environment=dict(os.environ,PYTHONPATH=os.pathsep.join(path for path in sys.path if path))
        child=subprocess.run([sys.executable,'-B','-X','utf8','-c',script],env=environment,
            capture_output=True,text=True,encoding='utf8',check=True,timeout=15)
        self.assertIn('新进程星图',json.loads(child.stdout))
        self.assertIn('新星界图',old.values('GUI_00001017'))
        self.assertNotIn('新进程星图',old.values('GUI_00001017'))

    def test_live_test_and_other_installations_do_not_share_snapshots(self):
        other=Path(self.temp.name)/'Test';self.write_install(other,'测试服星图','测试解封')
        first=self.module.snapshot_for_install(self.install)
        second=self.module.snapshot_for_install(other)
        self.assertIsNot(first,second)
        self.assertIn('新星界图',first.values('GUI_00001017'))
        self.assertIn('测试服星图',second.values('GUI_00001017'))
        self.assertNotIn('测试服星图',first.values('GUI_00001017'))
        same=SimpleNamespace(cache_handler=SimpleNamespace(install_location=self.install/'..'/'Live'))
        self.assertIs(self.module.snapshot_for_client(same),first)

    def test_directory_can_become_known_after_initial_start(self):
        client=SimpleNamespace(cache_handler=SimpleNamespace(install_location=None))
        before=self.module.snapshot_for_client(client)
        self.assertIn('漩涡星系图',before.values('GUI_00001017'))
        client.cache_handler.install_location=self.install
        after=self.module.snapshot_for_client(client)
        self.assertIn('新星界图',after.values('GUI_00001017'))

    def test_process_installation_overrides_dependency_global_default(self):
        client=SimpleNamespace(_pymem=SimpleNamespace(process_handle=123),
            cache_handler=SimpleNamespace(install_location=Path(self.temp.name)/'WrongDefault'))
        with patch.object(self.module,'_process_install_path',return_value=self.install):
            snapshot=self.module.snapshot_for_client(client)
        self.assertIn('新星界图',snapshot.values('GUI_00001017'))

    def test_inactive_patch_and_font_archive_cannot_override_active_translation(self):
        data=self.install/'Data/GameData'
        content=[('Locale/en-US/GUI.lang',lang('GUI',[('00001017','','未启用的补丁图')]),True)]
        wad(data/'Locale_en-US-root.wad.d',content)
        wad(data/'Locale_English-Root.wad',content)
        self.assertNotIn('未启用的补丁图',self.module.snapshot_for_client(self.client).values('GUI_00001017'))

    def test_missing_overlay_uses_current_root_and_missing_ids_use_fixed_fallback(self):
        (self.install/'Data/GameData/Locale_en-US-root.wad').unlink()
        current=self.module.snapshot_for_install(self.install)
        self.assertIn('Spiral Map',current.values('GUI_00001017'))
        self.assertNotIn('漩涡星系图',current.values('GUI_00001017'))
        self.assertIn('拜访',current.values('WizardQuestGoals_TalkNPC'))

    def test_empty_valid_current_value_does_not_restore_old_chinese(self):
        from src.interaction_prompts import matches_text
        wad(self.install/'Data/GameData/Locale_en-US-root.wad',[
            ('Locale/en-US/GUI.lang',lang('GUI',[('00001017','','')]),True)])
        current=self.module.snapshot_for_install(self.install)
        self.assertIsNone(current.error)
        self.assertFalse(matches_text('漩涡星系图','GUI_00001017',client=self.client))
        self.assertTrue(matches_text('Spiral Map','GUI_00001017',client=self.client))

    def test_missing_root_freezes_fallback_and_reports_only_once(self):
        (self.install/'Data/GameData/Root.wad').unlink()
        with patch.object(self.module.logger,'warning') as warning:
            first=self.module.snapshot_for_install(self.install)
            self.assertIs(self.module.snapshot_for_install(self.install),first)
        self.assertIsNotNone(first.error)
        self.assertIn('漩涡星系图',first.values('GUI_00001017'))
        warning.assert_called_once()

    def test_corrupt_late_table_discards_entire_batch_without_partial_override(self):
        data=self.install/'Data/GameData'
        wad(data/'Locale_en-US-root.wad',[
            ('Locale/en-US/GUI.lang',lang('GUI',[('00001017','','半成品新标题')]),True),
            ('Locale/en-US/WizardQuestGoals.lang',b'bad UTF16',False)])
        snapshot=self.module.snapshot_for_install(self.install)
        self.assertIsNotNone(snapshot.error)
        self.assertNotIn('半成品新标题',snapshot.values('GUI_00001017'))
        self.assertIn('漩涡星系图',snapshot.values('GUI_00001017'))
        self.assertFalse(snapshot.records)

    def test_truncated_records_and_broken_compression_are_not_published(self):
        data=self.install/'Data/GameData';overlay=data/'Locale_en-US-root.wad'
        for payload,compressed in [(('1:GUI\r\n00001017\r\nmissing third field').encode('utf16'),False),
                                   (lang('GUI',[('00001017','','截断压缩')]),True)]:
            self.module._SNAPSHOTS.clear()
            wad(overlay,[('Locale/en-US/GUI.lang',payload,compressed)])
            if compressed:overlay.write_bytes(overlay.read_bytes()[:-3])
            self.assertIsNotNone(self.module.snapshot_for_install(self.install).error)

    def test_invalid_or_mismatched_header_cannot_publish_even_an_empty_table(self):
        overlay=self.install/'Data/GameData/Locale_en-US-root.wad'
        for content in [lang('GUI',[('00001017','','坏表头标题')]).replace(b'1\x00:',b'x\x00:'),
                        lang('WrongTable',[])]:
            with self.subTest(content=content[:24]):
                self.module._SNAPSHOTS.clear()
                wad(overlay,[('Locale/en-US/GUI.lang',content,True)])
                self.assertIsNotNone(self.module.snapshot_for_install(self.install).error)

    def test_replacement_during_read_discards_batch_and_does_not_retry_this_run(self):
        from src import collect_catalog
        original=collect_catalog.language_files
        def replacing(path,**kwargs):
            yield from original(path,**kwargs)
            if path.name=='Locale_en-US-root.wad':
                self.write_install(self.install,'替换期间的新标题','替换期间的新动作')
                stat=path.stat();os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns+1_000_000))
        with patch.object(collect_catalog,'language_files',replacing):
            snapshot=self.module.snapshot_for_install(self.install)
        self.assertIsNotNone(snapshot.error)
        self.assertFalse(snapshot.records)
        self.assertIs(self.module.snapshot_for_install(self.install),snapshot)

    def test_concurrent_clients_share_one_fully_published_snapshot(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            snapshots=list(pool.map(self.module.snapshot_for_install,[self.install]*3))
        self.assertTrue(all(s is snapshots[0] for s in snapshots))
        self.assertIn('新星界图',snapshots[0].values('GUI_00001017'))

    def test_duplicate_chinese_across_action_kinds_is_not_guessed(self):
        from src.interaction_prompts import interaction_kind,task_object_prompt
        self.write_install(self.install,'新星界图','同义冲突',collect='同义冲突')
        self.assertIsNone(interaction_kind('按 X 或 同义冲突',client=self.client))
        self.assertFalse(task_object_prompt('物件','按 X 或 同义冲突',client=self.client))
        self.assertEqual(interaction_kind('<string;GUI2_00001561>',client=self.client),'open')
        self.assertEqual(interaction_kind('<string;GUI2__COLLECT>',client=self.client),'collect')

    def test_dynamic_resources_do_not_expand_existing_action_id_set(self):
        from src.interaction_prompts import interaction_kind,_INTERACTION_IDS
        before={kind:tuple(ids) for kind,ids in _INTERACTION_IDS.items()}
        self.write_install(self.install,'新星界图','解封',unknown=True)
        self.assertIsNone(interaction_kind('按 X 乘灵舟',client=self.client))
        self.assertEqual(before,{kind:tuple(ids) for kind,ids in _INTERACTION_IDS.items()})

    def test_duplicate_quest_actions_and_menu_titles_require_their_existing_ids(self):
        from src.interaction_prompts import quest_has_action
        from src.region_teleport import menu_kind
        data=self.install/'Data/GameData'
        wad(data/'Locale_en-US-root.wad',[
            ('Locale/en-US/GUI.lang',lang('GUI',[('00001017','','共享标题')]),True),
            ('Locale/en-US/GUI2.lang',lang('GUI2',[('00000399','','共享标题')]),True),
            ('Locale/en-US/WizardQuestGoals.lang',lang('WizardQuestGoals',[
                ('Kill','','共享动作'),('00000667','','共享动作')]),True)])
        self.assertFalse(quest_has_action('共享动作 目标','defeat',client=self.client))
        self.assertFalse(quest_has_action('共享动作 目标','pet_mode',client=self.client))
        self.assertTrue(quest_has_action('<string;WizardQuestGoals_Kill>','defeat',client=self.client))
        self.assertIsNone(menu_kind('共享标题',client=self.client))
        self.assertEqual(menu_kind('<string;GUI_00001017>',client=self.client),'world_gate')

    def test_direct_quest_caller_passes_client_to_world_and_interaction_matchers(self):
        from src.questing import Quester
        reader=Quester(self.client,[self.client],None)
        reader.read_popup=AsyncMock(return_value='按 X 或 解封')
        reader.read_spiral_door_title=AsyncMock(return_value='新星界图')
        reader._wait_internal_quest_failure=AsyncMock(return_value=False)
        self.assertFalse(asyncio.run(reader.detected_interact_from_popup(self.client)))
        self.assertFalse(asyncio.run(reader.new_world_doors(self.client)))

    def test_current_target_exclusion_and_existing_pet_label_follow_same_profile(self):
        from src.current_target_probe import object_candidate
        from src.auto_pet import _is_dance_go_prompt
        data=self.install/'Data/GameData'
        wad(data/'Locale_en-US-root.wad',[
            ('Locale/en-US/WizardQuestGoals.lang',lang('WizardQuestGoals',[('TalkNPC','','拜访新版')]),True),
            ('Locale/en-US/PetGames.lang',lang('PetGames',[('Action_Go','','新版起跳')]),True)])
        self.assertFalse(object_candidate('拜访新版 人物 地点：营地','物件','按 X 或 打开',client=self.client))
        self.assertTrue(_is_dance_go_prompt('新版起跳',client=self.client))
        self.assertFalse(_is_dance_go_prompt('开始重复！',client=self.client))
        self.assertTrue(_is_dance_go_prompt('Go!',client=self.client))

    def test_probe_frame_uses_actual_clients_profile_instead_of_quester_wrapper(self):
        from src import current_target_probe as probe
        from src.questing import Quester
        from wizwalker import XYZ
        reader=Quester(self.client,[self.client],None)
        task=(10,20,XYZ(0,0,0),'World/Here');progress=(10,20,'Use Relic in Camp')
        reader._dungeon_quest_snapshot=AsyncMock(return_value=progress)
        reader._quest_interaction_signature=AsyncMock(return_value=('Relic','按 X 或 解封'))
        reader.read_popup=AsyncMock(return_value='按 X 或 解封')
        self.client.questing_status=True
        self.client.body=object()
        state=dict(task=task,target=XYZ(0,0,0),terminal=dict(position=XYZ(0,0,0)),control=None,
                   owner=probe.get_client_automation_ownership(self.client),
                   context=probe._target_approach_context(self.client,self.client))
        with patch.object(probe,'_safe',AsyncMock(return_value=True)), \
             patch.object(probe,'_movement_task_snapshot',AsyncMock(return_value=task)), \
             patch.object(probe,'_movement_position',AsyncMock(return_value=XYZ(0,0,0))), \
             patch.object(probe,'_unique_popup',AsyncMock(return_value='same-popup')), \
             patch.object(probe,'_same_movement_task',return_value=True), \
             patch.object(probe,'_movement_failure_blocks_interaction',AsyncMock(return_value=False)):
            self.assertIsNotNone(asyncio.run(probe._frame(reader,state)))

    def test_online_friends_legacy_chinese_cannot_override_two_current_resource_values(self):
        from src import utils
        data=self.install/'Data/GameData'
        wad(data/'Locale_en-US-root.wad',[('Locale/en-US/GUI.lang',lang('GUI',[
            ('00000510','','在线友人新版'),('FriendsOnline','','在线友人新版')]),True)])
        label=SimpleNamespace(maybe_text=AsyncMock(return_value='在线好友'));button=object()
        async def click(window):
            self.assertIs(window,button)
            label.maybe_text.return_value='在线友人新版'
        self.client.mouse_handler=SimpleNamespace(click_window=AsyncMock(side_effect=click))
        async def find(_window,name):return label if name=='lblFriendsList' else button
        with patch.object(utils,'_maybe_get_named_window',side_effect=find):
            asyncio.run(utils._cycle_to_online_friends(self.client,object()))
        self.client.mouse_handler.click_window.assert_awaited_once_with(button)

    def test_world_and_region_titles_use_same_client_snapshot(self):
        from src.region_teleport import menu_kind
        self.assertEqual(menu_kind('新星界图',client=self.client),'world_gate')
        self.assertEqual(menu_kind('<string;GUI_00001017>',client=self.client),'world_gate')
        self.assertEqual(menu_kind('Tamed Demox',client=self.client),'region')
        self.assertIsNone(menu_kind('',client=self.client))

    def test_region_metadata_uses_same_snapshot_without_old_alias_from_another_id(self):
        from src.region_teleport import resolve_region_target,option_names
        install=self.install;data=install/'Data/GameData';(install/'Bin').mkdir()
        (install/'Bin/revision.dat').write_text('unchanged-revision')
        zone='World/ZoneA';display='WizardZone_00001763'
        def prop(key,value):return struct.pack('<II',8*(8+len(value)),key)+value
        def text(value):return struct.pack('<H',len(value))+value.encode()
        header=(bytes.fromhex('76da9f4ce1510200')+prop(0x9e5c7bca,struct.pack('<I',1))
                +prop(0x816963f8,text(zone))+prop(0x74f6ec6e,text(display)))
        wad(data/'World-ZoneA.wad',[('gamedata.bin',header,False)])
        self.client.zone_name=AsyncMock(return_value='World/Other')
        profile=self.module.snapshot_for_client(self.client)
        target=asyncio.run(resolve_region_target(self.client,zone,'任务 地点：Outsiders Camp'))
        self.assertEqual(target.zone,zone)
        self.assertTrue(option_names('营地新版',client=self.client)&target.names)
        self.assertFalse(option_names('流亡者营地',client=self.client)&target.names)
        self.assertIs(self.module.snapshot_for_client(self.client),profile)

    def test_region_duplicate_current_label_does_not_choose_between_zone_ids(self):
        from src.region_teleport import RegionRecord,choose_region_target,option_names
        data=self.install/'Data/GameData'
        wad(data/'Locale_en-US-root.wad',[('Locale/en-US/WizardZone.lang',lang('WizardZone',[
            ('00001763','','同名区域'),('Other','','同名区域')]),True)])
        records=[RegionRecord('World/A','WizardZone_00001763',1,('Outsiders Camp',)),
                 RegionRecord('World/B','WizardZone_Other',1,('Different Area',))]
        target=choose_region_target('World/A','',records,client=self.client)
        self.assertFalse(option_names('同名区域',client=self.client)&target.names)
        self.assertTrue(option_names('World/A',client=self.client)&target.names)


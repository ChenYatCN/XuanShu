"""Region selection must never spend the world-gate fallback or hitter inputs."""
import asyncio
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import struct
import unittest
from unittest.mock import AsyncMock,patch

from src.questing import Quester
from wizwalker import Primitive
from wizwalker.memory.memory_objects.enums import WindowFlags
from src.automation_ownership import automation_owner,get_client_automation_ownership

class MenuRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client=SimpleNamespace(title='p1',process_id=1)
        self.reader=Quester(self.client,[self.client],None)
        self.reader._wait_internal_quest_failure=AsyncMock(return_value=False)
        self.reader._defer_internal_quest_failure=AsyncMock()
        self.reader.read_quest_txt=AsyncMock(return_value='跟随 踪迹 地点：Howling Lands')

    async def test_all_non_world_titles_are_regions_without_name_whitelist(self):
        for title in ('<center>Tamed Demox</center>','电话簿','Streamportal','A future regional menu'):
            with self.subTest(title=title),patch('src.questing.travel_region_menu',new=AsyncMock(return_value=True),create=True) as select:
                self.reader.read_spiral_door_title=AsyncMock(return_value=title)
                self.assertTrue(await self.reader.new_world_doors(self.client))
                select.assert_awaited_once()
                self.assertIs(select.await_args.args[0],self.client)

    async def test_only_spiral_map_title_uses_existing_world_gate_path(self):
        for title in ('漩涡星系图','Spiral Map','<string;GUI_00001017>'):
            with self.subTest(title=title),patch('src.questing.travel_region_menu',new=AsyncMock(),create=True) as select:
                self.reader.read_spiral_door_title=AsyncMock(return_value=title)
                self.assertFalse(await self.reader.new_world_doors(self.client))
                select.assert_not_awaited()

    async def test_unreadable_or_empty_title_is_handled_wait_never_world_gate(self):
        self.reader.read_spiral_door_title=AsyncMock(return_value='')
        with patch('src.questing.travel_region_menu',new=AsyncMock(),create=True) as select:
            self.assertTrue(await self.reader.new_world_doors(self.client))
            select.assert_not_awaited()
        self.reader._defer_internal_quest_failure.assert_awaited_once()

    async def test_legacy_group_does_not_select_region_menu_for_assigned_hitters(self):
        hitter=SimpleNamespace(title='p2',process_id=2)
        self.reader.clients=[self.client,hitter]
        self.reader.current_leader_pid=1
        self.reader.d_location='stale region from an older operation'
        self.reader.new_world_doors=AsyncMock(return_value=True)
        with patch('src.questing.new_portals_cycle',new=AsyncMock()) as select,patch('src.questing.is_visible_by_path',new=AsyncMock(return_value=True)):
            await self.reader.handle_spiral_navigation()
        select.assert_not_awaited()


class RegionResourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_goal_record_id_is_read_from_goal_not_assumed_equal_to_map_key(self):
        from src.region_teleport import read_active_goal
        goal=SimpleNamespace(read_value_from_offset=AsyncMock(return_value=572984775))
        quest=SimpleNamespace(goal_data=AsyncMock(return_value={114841790859720417:goal}))
        client=SimpleNamespace(quest_manager=AsyncMock(return_value=SimpleNamespace(
            quest_data=AsyncMock(return_value={114841790859720415:quest}))))
        self.assertIs(await read_active_goal(client,(114841790859720415,572984775)),goal)
        goal.read_value_from_offset.assert_awaited_once_with(0x70,Primitive.uint32)

    async def test_duplicate_active_goal_field_does_not_choose_first(self):
        from src.region_teleport import read_active_goal
        goals={n:SimpleNamespace(read_value_from_offset=AsyncMock(return_value=20)) for n in (30,40)}
        quest=SimpleNamespace(goal_data=AsyncMock(return_value=goals))
        client=SimpleNamespace(quest_manager=AsyncMock(return_value=SimpleNamespace(quest_data=AsyncMock(return_value={10:quest}))))
        self.assertIsNone(await read_active_goal(client,(10,20)))

    async def test_zone_header_reads_verified_resource_fields_and_rejects_mismatched_path(self):
        from src.region_teleport import parse_zone_header,RegionMatchError
        # Actual top-level property IDs/encoding from the installed gamedata.bin.
        def prop(key,value):return struct.pack('<II',8*(8+len(value)),key)+value
        def text(value):return struct.pack('<H',len(value))+value.encode()
        zone='Darkmoor/DM_Z00_OutsidersCamp'
        raw=bytes.fromhex('76da9f4ce1510200')+prop(0x9e5c7bca,struct.pack('<I',1))+prop(0x816963f8,text(zone))+prop(0x74f6ec6e,text('WizardZone_00001763'))
        self.assertEqual(parse_zone_header(raw,zone),(zone,'WizardZone_00001763',1))
        for bad in (b'broken',raw[:20],raw.replace(zone.encode(),b'X'+zone.encode()[1:])):
            with self.subTest(raw=bad[:12]),self.assertRaises(RegionMatchError):parse_zone_header(bad,zone)

    async def test_localized_exact_match_reuses_records_and_rejects_partial_names(self):
        from src.region_teleport import exact_names
        self.assertTrue(exact_names('Outsiders Camp') & exact_names('流亡者营地'))
        self.assertTrue(exact_names('<string;WizardZone_00001746>') & exact_names('Outsiders Camp'))
        self.assertFalse(exact_names('Outsiders') & exact_names('流亡者营地'))
        self.assertFalse(exact_names('Outsiders Camp Annex') & exact_names('流亡者营地'))

    async def test_interior_requires_same_resource_area_id_and_unique_exterior(self):
        from src.region_teleport import RegionRecord,choose_region_target,RegionMatchError
        inside=RegionRecord('Darkmoor/Interiors/DM_Z03_Hideout','WizardZone_00001751',2,('Howling Lands',))
        outside=RegionRecord('Darkmoor/DM_Z03_HowlingLands','WizardZone_00001751',1,('Howling Lands','啸狼荒原'))
        target=choose_region_target(inside.zone,'',[inside,outside])
        self.assertEqual(target.zone,outside.zone)
        for records in ([inside],[inside,RegionRecord(outside.zone,'different',1,outside.names)],
                        [inside,outside,RegionRecord('Darkmoor/Other','WizardZone_00001751',1,outside.names)]):
            with self.subTest(records=records),self.assertRaises(RegionMatchError):
                choose_region_target(inside.zone,'Howling Lands',records)

    async def test_readable_goal_zone_has_priority_over_hud_location(self):
        from src.region_teleport import RegionRecord,choose_region_target
        camp=RegionRecord('Darkmoor/DM_Z00_OutsidersCamp','WizardZone_00001746',1,('Outsiders Camp','流亡者营地'))
        other=RegionRecord('Darkmoor/DM_Z03_HowlingLands','WizardZone_00001751',1,('Howling Lands','啸狼荒原'))
        self.assertEqual(choose_region_target(camp.zone,'Howling Lands',[camp,other]).zone,camp.zone)
        self.assertEqual(choose_region_target(None,'流亡者营地',[camp,other]).zone,camp.zone)

    async def test_ambiguous_resource_display_name_cannot_authorize_unique_label_row(self):
        from src.region_teleport import RegionRecord,choose_region_target,option_names
        first=RegionRecord('World/ZoneA','Zone_A',1,('Area A','同名地点'))
        second=RegionRecord('World/ZoneB','Zone_B',1,('Area B','同名地点'))
        target=choose_region_target(first.zone,'',[first,second])
        self.assertFalse(option_names('同名地点')&target.names)
        self.assertTrue(option_names(first.zone)&target.names)
        self.assertTrue(option_names('Area A')&target.names)

    async def test_ambiguous_bilingual_aliases_and_shared_ids_are_all_removed(self):
        from src.region_teleport import RegionRecord,choose_region_target,option_names
        first=RegionRecord('World/A','SharedID',1,('Outsiders Camp',))
        second=RegionRecord('World/B','SharedID',1,('Outsiders Camp',))
        target=choose_region_target(first.zone,'',[first,second])
        for label in ('流亡者营地','Outsiders Camp','SharedID'):
            self.assertFalse(option_names(label)&target.names,label)
        self.assertTrue(option_names(first.zone)&target.names)


class MenuWindow:
    serial=100
    def __init__(self,name,fixture,children=(),text='',kind='Window'):
        type(self).serial+=1
        self.address=type(self).serial
        self.label,self.fixture,self.nodes,self.text,self.kind=name,fixture,list(children),text,kind
        self.up=None;self.visible=True;self.disabled=False;self.checked=False
        for child in self.nodes:child.up=self
    async def name(self):return self.label
    async def read_base_address(self):return self.address
    async def parent(self):return self.up
    async def children(self):
        if self.label=='optionWindow':return self.fixture.rows()
        return self.nodes
    async def is_visible(self):return self.visible and self.fixture.menu
    async def flags(self):return WindowFlags.visible|(WindowFlags.disabled if self.disabled else 0)
    async def maybe_read_type_name(self):return self.kind
    async def is_control_grayed(self):
        if self.label=='teleportButton':return self.fixture.gray
        if self.label=='leftButton':return self.fixture.page==1
        if self.label=='rightButton':return self.fixture.page==len(self.fixture.pages)
        return False
    async def read_value_from_offset(self,offset,primitive):
        if self.kind!='ControlCheckBox' or offset!=0x40c:raise AssertionError((self.kind,offset))
        return int(self.fixture.selected==(self.fixture.page,self.label))
    async def maybe_text(self):return self.text
    async def maybe_checked(self):return False  # Old offset must not be trusted.
    async def scale_to_client(self):
        return SimpleNamespace(center=lambda:({'leftButton':1,'rightButton':2,'teleportButton':3}.get(
            self.label,10+int(self.label[3:]) if self.label.startswith('opt') else 0),0))
    async def get_child_by_name(self,name):
        for child in await self.children():
            if child.label==name:return child
        raise ValueError(name)


class RegionMenuFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from src import region_teleport as region
        self.region=region;self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.page=1;self.pages=[['流亡者营地','墓都']]
        self.menu=True;self.gray=False;self.loading=False;self.battle=False
        self.quest,self.goal=10,20;self.objective='任务 地点：Outsiders Camp'
        self.zone='Darkmoor/DM_Z01_Graveholm';self.destination='Darkmoor/DM_Z00_OutsidersCamp'
        self.goal_zone=self.destination;self.selected=None;self.events=[];self.on_read=lambda:None
        self.on_click=lambda _name:None;self.on_sleep=lambda:None
        self.travel='success';self.miss_selection=False;self.stuck_pages=False
        self.panel=MenuWindow('optionWindow',self)
        self.world_title=MenuWindow('title',self,text='<string;GUI_00001017>',kind='ControlText');self.world_title.visible=False
        self.title=MenuWindow('streamTitle',self,text='<center>Tamed Demox</center>',kind='ControlText')
        self.button=MenuWindow('teleportButton',self,kind='ControlButton')
        self.control=MenuWindow('ControlSprite',self,[self.world_title,self.title,self.panel,self.button])
        self.root=MenuWindow('Root',self,[MenuWindow('WorldView',self,[MenuWindow('',self,[MenuWindow('messageBoxBG',self,[self.control])])])])
        mouse=AsyncMock();mouse.click_lock=None;mouse.click_predelay=.02
        self.pointer=None
        async def position(x,y):self.pointer=(x,y)
        mouse.set_mouse_position.side_effect=position
        def send(_handle,message,*_args):
            if message==0x202:
                x,_=self.pointer
                name={1:'leftButton',2:'rightButton',3:'teleportButton'}.get(x,f'opt{x-10}')
                self.dispatch(name)
        self.stack.enter_context(patch.object(region.user32,'SendMessageW',side_effect=send))
        self.goal_record=SimpleNamespace(goal_destination_zone=AsyncMock(side_effect=lambda:self.goal_zone),
            read_value_from_offset=AsyncMock(return_value=20))
        self.client=SimpleNamespace(title='p1',process_id=1,questing_status=True,root_window=self.root,
            quest_id=AsyncMock(side_effect=lambda:self.quest),goal_id=AsyncMock(side_effect=lambda:self.goal),
            zone_name=AsyncMock(side_effect=lambda:self.zone),is_loading=AsyncMock(side_effect=lambda:self.loading),
            in_battle=AsyncMock(side_effect=lambda:self.battle),is_running=lambda:True,
            body=object(),mouse_handler=mouse,window_handle=1)
        self.read_objective=AsyncMock(side_effect=lambda _:self.objective)
        self.stack.enter_context(patch.object(region,'read_control_text',lambda w:w.maybe_text()))
        self.stack.enter_context(patch.object(region,'read_active_goal',AsyncMock(side_effect=lambda *_:self.goal_record)))
        self.stack.enter_context(patch.object(region,'resolve_region_target',AsyncMock(side_effect=lambda *_:region.RegionTarget(
            self.destination,frozenset(('outsiders camp','流亡者营地')), 'exact-zone'))))
        self.stack.enter_context(patch('src.teleport_math._movement_body',AsyncMock(return_value=(self.client.body,None))))
        self.stack.enter_context(patch('src.teleport_math._movement_control_changed',AsyncMock(return_value=False)))
        real_sleep=asyncio.sleep
        async def sleep(_delay):
            self.on_sleep();await real_sleep(.001)
        self.stack.enter_context(patch.object(region.asyncio,'sleep',sleep))
        self.stack.enter_context(patch.object(region,'READ_TIMEOUT',.08))
        self.stack.enter_context(patch.object(region,'TRAVEL_TIMEOUT',.2))
        self.stack.enter_context(patch.object(region,'SELECTION_TIMEOUT',.08))

    def rows(self):
        self.on_read()
        result=[MenuWindow('pageCount',self,text=f'<center>{self.page}/{len(self.pages)}</center>',kind='ControlText'),
                MenuWindow('leftButton',self,kind='ControlButton'),MenuWindow('rightButton',self,kind='ControlButton')]
        result.extend(MenuWindow(f'opt{i}',self,text=text,kind='ControlCheckBox') for i,text in enumerate(self.pages[self.page-1]))
        for child in result:child.up=self.panel
        return result

    def dispatch(self,name):
        self.events.append(name);self.on_click(name)
        if name.startswith('opt') and not self.miss_selection:self.selected=(self.page,name)
        if name=='rightButton' and not self.stuck_pages:self.page+=1
        if name=='leftButton' and not self.stuck_pages:self.page-=1
        if name=='teleportButton':
            if self.travel=='success':self.zone=self.destination;self.menu=False
            if self.travel=='wrong':self.zone='Darkmoor/Other';self.menu=False

    async def run_menu(self):return await self.region.travel_region_menu(self.client,read_objective=self.read_objective)

    async def test_single_page_localized_select_confirm_and_arrival_refresh(self):
        self.assertTrue(await self.run_menu())
        self.assertEqual(self.events,['opt0','teleportButton'])
        self.assertEqual(self.region.read_active_goal.await_count,2)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_multi_page_searches_all_then_reacquires_correct_row(self):
        self.pages=[['Outsiders Camp'],['Graveholm']]
        self.assertTrue(await self.run_menu())
        self.assertEqual(self.events,['rightButton','leftButton','opt0','teleportButton'])

    async def test_target_on_later_page_and_nonfirst_start_page(self):
        self.pages=[['Graveholm'],['Outsiders Camp'],['Mortal Plain']];self.page=3
        self.assertTrue(await self.run_menu())
        self.assertEqual(self.events,['leftButton','leftButton','rightButton','rightButton','leftButton','opt0','teleportButton'])

    async def test_missing_and_duplicate_candidates_never_select_or_travel(self):
        for pages in ([['Graveholm']], [['Outsiders Camp','流亡者营地']], [['Outsiders Camp'],['流亡者营地']]):
            with self.subTest(pages=pages):
                self.pages=pages;self.page=1;self.events=[]
                self.assertFalse(await self.run_menu())
                self.assertFalse(any(x.startswith('opt') or x=='teleportButton' for x in self.events))
                diagnostic=self.client._quest_region_transfer_diagnostic
                self.assertEqual(diagnostic['client'],'p1');self.assertTrue(diagnostic['destinations']);self.assertTrue(diagnostic['reason'])

    async def test_unreadable_title_waits_without_any_input(self):
        self.title.text=''
        self.assertFalse(await self.run_menu());self.assertEqual(self.events,[])

    async def test_markup_only_title_is_not_a_successfully_read_region_title(self):
        self.title.text='<center> </center>'
        self.assertFalse(await self.run_menu());self.assertEqual(self.events,[])

    async def test_hidden_world_title_is_ignored_and_unknown_visible_title_is_region(self):
        self.title.text='Telephone Directory'
        self.assertTrue(await self.run_menu())

    async def test_both_visible_conflicting_titles_are_not_ready(self):
        self.world_title.visible=True
        self.assertFalse(await self.run_menu());self.assertEqual(self.events,[])

    async def test_world_title_never_enters_region_handler(self):
        self.title.visible=False;self.world_title.visible=True
        self.assertFalse(await self.run_menu());self.assertEqual(self.events,[])

    async def test_gray_or_disabled_teleport_button_is_not_clicked(self):
        for disabled in (False,True):
            with self.subTest(disabled=disabled):
                self.gray=not disabled;self.button.disabled=disabled;self.selected=None;self.events=[]
                self.assertFalse(await self.run_menu())
                self.assertNotIn('teleportButton',self.events)

    async def test_selection_must_be_confirmed_by_current_checkbox_field(self):
        self.miss_selection=True
        self.assertFalse(await self.run_menu());self.assertEqual(self.events,['opt0'])

    async def test_stuck_or_incomplete_page_is_bounded(self):
        self.pages=[['Graveholm'],['Outsiders Camp']];self.stuck_pages=True
        self.assertFalse(await self.run_menu());self.assertEqual(self.events,['rightButton'])

    async def test_page_contents_changing_on_return_abort_selection(self):
        self.pages=[['Outsiders Camp'],['Graveholm']]
        def mutate(name):
            if name=='leftButton':self.pages[0]=['Mortal Plain']
        self.on_click=mutate
        self.assertFalse(await self.run_menu());self.assertEqual(self.events,['rightButton','leftButton'])

    async def test_quest_or_goal_or_area_or_stop_or_battle_changes_prevent_travel(self):
        mutations=[lambda:setattr(self,'goal',21),lambda:setattr(self,'quest',11),lambda:setattr(self,'zone','Other/Zone'),
            lambda:setattr(self.client,'questing_status',False),lambda:setattr(self,'battle',True),
            lambda:setattr(self,'goal_zone','Darkmoor/AnotherGoal'),lambda:setattr(self,'objective','new task')]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.goal=20;self.quest=10;self.zone='Darkmoor/DM_Z01_Graveholm';self.client.questing_status=True
                self.battle=False;self.goal_zone=self.destination;self.objective='任务 地点：Outsiders Camp';self.events=[];self.selected=None
                self.on_click=lambda name:mutate() if name.startswith('opt') else None
                self.assertIsNone(await self.run_menu());self.assertEqual(self.events,['opt0'])

    async def test_wrong_arrival_and_no_arrival_are_not_success_or_repeated_travel(self):
        for travel in ('wrong','none'):
            with self.subTest(travel=travel):
                self.zone='Darkmoor/DM_Z01_Graveholm';self.menu=True;self.events=[];self.selected=None;self.travel=travel
                self.client._quest_region_transfer_receipt=None
                self.assertFalse(await self.run_menu());self.assertEqual(self.events.count('teleportButton'),1)
                if travel=='none':
                    self.assertFalse(await self.run_menu());self.assertEqual(self.events.count('teleportButton'),1)

    async def test_loading_must_finish_before_success(self):
        self.loading=True
        self.assertIsNone(await self.run_menu());self.assertEqual(self.events,[])
        self.loading=False
        def loading_on_click(name):
            if name=='teleportButton':self.loading=True
        self.on_click=loading_on_click;count=0
        def end_load():
            nonlocal count
            if self.loading:
                count+=1
                if count==3:self.loading=False
        self.on_sleep=end_load
        self.assertTrue(await self.run_menu());self.assertGreaterEqual(count,3)

    async def test_expected_loading_may_temporarily_hide_hud_but_must_refresh_after_arrival(self):
        self.read_objective.side_effect=lambda _:'' if self.loading else self.objective
        self.on_click=lambda name:setattr(self,'loading',True) if name=='teleportButton' else None
        ticks=0
        def settle():
            nonlocal ticks
            if self.loading:
                ticks+=1
                if ticks==3:self.loading=False
        self.on_sleep=settle
        self.assertTrue(await self.run_menu());self.assertEqual(self.events,['opt0','teleportButton'])

    async def test_added_page_after_scan_prevents_selection(self):
        original=self.region.resolve_region_target.side_effect
        def add_page(*args):
            self.pages.append(['Outsiders Camp'])
            return original(*args)
        self.region.resolve_region_target.side_effect=add_page
        self.assertFalse(await self.run_menu());self.assertEqual(self.events,[])

    async def test_window_replaced_while_acquiring_mouse_prevents_stale_click(self):
        async def replace():self.control.address+=1
        self.client.mouse_handler.__aenter__.side_effect=replace
        self.assertFalse(await self.run_menu());self.assertEqual(self.events,[])

    async def test_task_changes_during_predelay_prevent_travel_dispatch(self):
        def change():
            if self.pointer==(3,0):self.goal=21
        self.on_sleep=change
        self.assertIsNone(await self.run_menu());self.assertEqual(self.events,['opt0'])
        self.assertFalse(self.client._quest_region_transfer_receipt['sent'])

    async def test_waiting_for_mouse_lock_rechecks_window_before_page_input(self):
        self.pages=[['Graveholm'],['Outsiders Camp']]
        lock=asyncio.Lock();await lock.acquire();self.client.mouse_handler.click_lock=lock
        entered=asyncio.Event()
        self.client.mouse_handler.__aenter__.side_effect=lambda:entered.set()
        task=asyncio.create_task(self.run_menu());await asyncio.wait_for(entered.wait(),1)
        self.control.address+=1;lock.release()
        self.assertFalse(await task);self.assertEqual(self.events,[])

    async def test_loading_hides_task_ids_and_zone_until_arrival(self):
        def readable(value):
            if self.loading:raise self.region.MenuNotReady('loading data')
            return value
        self.client.zone_name.side_effect=lambda:readable(self.zone)
        self.client.quest_id.side_effect=lambda:readable(self.quest)
        self.client.goal_id.side_effect=lambda:readable(self.goal)
        self.on_click=lambda name:setattr(self,'loading',True) if name=='teleportButton' else None
        ticks=0
        def settle():
            nonlocal ticks
            if self.loading:
                ticks+=1
                if ticks==3:self.loading=False
        self.on_sleep=settle
        self.assertTrue(await self.run_menu());self.assertGreaterEqual(ticks,3)

    async def test_hitter_or_other_owner_cannot_act(self):
        self.client.quest_party_quester=object()
        self.assertIsNone(await self.run_menu());self.assertEqual(self.events,[])
        self.client.quest_party_quester=None
        started=asyncio.Event();release=asyncio.Event()
        async def other():
            async with automation_owner(self.client,'other'):
                started.set();await release.wait()
        task=asyncio.create_task(other());await started.wait()
        self.assertIsNone(await self.run_menu());self.assertEqual(self.events,[])
        release.set();await task

    async def test_cancellation_propagates_and_releases_only_own_state(self):
        selected=asyncio.Event();pause=asyncio.Event()
        async def stop_at_selection(x,y):
            self.pointer=(x,y)
            if (x,y)==(-100,-100) and self.selected is not None:
                selected.set();await pause.wait()
        self.client.mouse_handler.set_mouse_position.side_effect=stop_at_selection
        task=asyncio.create_task(self.run_menu());await asyncio.wait_for(selected.wait(),1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertFalse(get_client_automation_ownership(self.client).locked)
        self.assertFalse(getattr(self.client,'_quest_region_transfer_active',False))
        self.assertEqual(self.events,['opt0']);self.client.mouse_handler.__aexit__.assert_awaited_once()

"""Task-owned selection in UniverseMap's regional destination list."""
import asyncio
from dataclasses import dataclass
import html
from pathlib import Path
import re
import struct

from loguru import logger
from wizwalker import AddressOutOfRange,MemoryReadError,Primitive,user32
from wizwalker.file_readers.wad import Wad
from wizwalker.memory.memory_objects.enums import WindowFlags

from src.automation_ownership import automation_owner,get_client_automation_ownership
from src.game_text_catalog import PORTAL_RECORDS
from src.game_text_snapshot import client_install_path,snapshot_for_client,text_values
from src.interaction_prompts import matches_text,plain_text,split_quest_location
from src.utils import get_spiral_teleport_button
from src.window_text import read_control_text

READ_TIMEOUT=3.0
SELECTION_TIMEOUT=3.0
TRAVEL_TIMEOUT=25.0
MAX_PAGES=64
UI_READ_ERRORS=(MemoryReadError,AddressOutOfRange,UnicodeError,ValueError)
BUSY_FLAGS=('refilling_potions','quest_recovery_owner','post_combat_movement_active',
            'feeding_pet_status','entity_detect_combat_status','quest_party_safe_return_active',
            '_quest_dialogue_update_pending')


class MenuNotReady(ValueError):
    pass


class RegionMatchError(ValueError):
    pass


def menu_kind(title, *, client=None):
    if not isinstance(title,str) or not title.strip():
        return None
    if not plain_text(title) and not re.fullmatch(
            r'(?:<center>)?<string;[^<>;]+>(?:</center>)?',html.unescape(title).strip(),re.I):
        return None
    if matches_text(title,'GUI_00001017',client=client):
        if matches_text(title,'GUI2_00000398','GUI2_00000399','GUI2_00001319','GUI2_00001320',client=client):
            return None  # An ambiguous translated title cannot authorize world-gate fallback.
        return 'world_gate'
    return 'region'


def exact_names(value, *, client=None):
    """Only complete resource values/IDs; no translation or substring scoring."""
    raw=html.unescape(str(value or '')).strip()
    reference=re.fullmatch(r'(?:<center>)?<string;([^<>;]+)>(?:</center>)?',raw,re.I)
    key=reference[1] if reference else raw
    values={plain_text(raw).casefold()}-{''}
    if reference or key in PORTAL_RECORDS:
        values.update([key.casefold(),*(plain_text(s).casefold() for s in text_values(key,client) if s)])
    else:
        # Only the existing canonical English names select aliases. A Chinese
        # label does not reverse-select an ID or combine conflicting records.
        for text_id,(english,_) in PORTAL_RECORDS.items():
            if plain_text(english).casefold()==plain_text(raw).casefold():
                values.update(plain_text(s).casefold() for s in text_values(text_id,client) if s)
    return frozenset(values)


def option_names(value, *, client=None):
    """Menu rows expose a literal label or an explicit resource/zone ID."""
    raw=html.unescape(str(value or '')).strip()
    reference=re.fullmatch(r'(?:<center>)?<string;([^<>;]+)>(?:</center>)?',raw,re.I)
    key=reference[1] if reference else raw
    if reference:
        return frozenset([key.casefold(),*(plain_text(s).casefold() for s in text_values(key,client) if s)])
    return frozenset([plain_text(raw).casefold()])-{''}


@dataclass(frozen=True)
class RegionRecord:
    zone:str
    display_id:str
    zone_type:int
    names:tuple


@dataclass(frozen=True)
class RegionTarget:
    zone:str
    names:frozenset
    proof:str


def parse_zone_header(data,expected_zone=None):
    """Read only WorldData's three verified, byte-aligned header properties.

    The later spawn lists are bit-packed and intentionally not parsed here.
    Property IDs agree with entity_collision's m_zoneDisplayName resource.
    """
    if len(data)<8 or data[:4]!=bytes.fromhex('76da9f4c'):
        raise RegionMatchError('不支持的区域资源头')
    fields={};position=8
    for _ in range(16):
        if position+8>len(data):break
        bits,key=struct.unpack_from('<II',data,position)
        size=bits//8
        if bits%8 or size<8 or position+size>len(data):break
        value=data[position+8:position+size];position+=size
        if key in (0x816963f8,0x74f6ec6e):
            if len(value)<2:break
            length=struct.unpack_from('<H',value)[0]
            if length+2!=len(value):break
            fields[key]=value[2:].decode('utf8')
        elif key==0x9e5c7bca and len(value)==4:
            fields[key]=struct.unpack('<I',value)[0]
        if len(fields)==3:break
    zone,display,kind=(fields.get(key) for key in (0x816963f8,0x74f6ec6e,0x9e5c7bca))
    if (not isinstance(zone,str) or '/' not in zone or not isinstance(display,str)
            or not display or not isinstance(kind,int) or expected_zone and zone!=expected_zone):
        raise RegionMatchError('区域路径或显示区域资源标识不完整/不一致')
    return zone,display,kind


def choose_region_target(goal_zone,hud_location,records, *, client=None):
    def names_for(record):
        if client is None:
            return frozenset(alias for name in record.names for alias in exact_names(name))
        # The actual area's display ID, not another ID with the same English
        # place name, determines its current Chinese label.
        return frozenset(alias for name in (*record.names,*text_values(record.display_id,client))
                         for alias in option_names(name,client=client))

    if goal_zone:
        goals=[record for record in records if record.zone==goal_zone]
        if len(goals)!=1:raise RegionMatchError(f'目标区域资源不唯一或不可读：{goal_zone}')
        record=goals[0]
        if record.zone_type!=1 or '/Interiors/' in record.zone:
            # Exact shared display-area ID is evidence; a similar filename or
            # translated name alone cannot establish an interior's parent.
            parents=[r for r in records if r.zone_type==1 and '/Interiors/' not in r.zone
                and r.display_id==record.display_id and r.zone.split('/',1)[0]==record.zone.split('/',1)[0]]
            if len(parents)!=1:raise RegionMatchError(f'室内/地牢没有唯一且可靠的所属大地图：{goal_zone}')
            record=parents[0];proof='shared-resource-area-id'
        else:proof='exact-goal-zone'
    else:
        candidates=[r for r in records if r.zone_type==1 and '/Interiors/' not in r.zone
            and (exact_names(hud_location) if client is None else option_names(hud_location,client=client))&names_for(r)]
        if len(candidates)!=1:raise RegionMatchError(f'任务显示地点无法唯一对应区域资源：{hud_location!r}')
        record=candidates[0];proof='exact-hud-resource-name'
    names=set(names_for(record))
    ambiguous={alias for other in records
        if other.zone!=record.zone and other.zone_type==1 and '/Interiors/' not in other.zone
        for alias in names_for(other)}
    names.difference_update(ambiguous)
    names.add(record.zone.casefold())
    if not any(other.zone!=record.zone and other.zone_type==1 and '/Interiors/' not in other.zone
               and other.display_id==record.display_id for other in records):
        names.add(record.display_id.casefold())
    if not names:raise RegionMatchError('目的地区域没有可核对名称')
    return RegionTarget(record.zone,frozenset(names),proof)


async def read_active_goal(client,task):
    quest=(await (await client.quest_manager()).quest_data()).get(task[0])
    if quest is None:return None
    goals=await quest.goal_data()
    # Current GoalData map keys are template/global IDs; +0x70 is the active
    # goal ID, verified against CharacterRegistry and the task HUD in-game.
    matches=[]
    for goal in goals.values():
        if await goal.read_value_from_offset(0x70,Primitive.uint32)==task[1]:matches.append(goal)
    return matches[0] if len(matches)==1 else None


async def resolve_region_target(client,goal_zone,objective):
    world=(goal_zone or await client.zone_name()).split('/',1)[0]
    install=client_install_path(client)
    if install is None:raise RegionMatchError('实际客户端安装目录尚未确定，无法核对区域资源')
    language=snapshot_for_client(client)
    data_dir=install/'Data/GameData'
    revision=(install/'Bin/revision.dat').read_text().strip()
    cache_key=(str(install),world,revision)
    cache=getattr(client,'_quest_region_resource_cache',None)
    if not isinstance(cache,dict):cache={};client._quest_region_resource_cache=cache
    if cache_key not in cache:
        paths=sorted(data_dir.glob(world+'-*.wad'))
        if not paths or len(paths)>2048:raise RegionMatchError('区域资源索引为空或超出有限读取预算')
        headers=[]
        for path in paths:
            wad=Wad(path)
            try:
                if 'gamedata.bin' not in await wad.names():continue
                zone,display,kind=parse_zone_header(await wad.get_file('gamedata.bin'))
                if zone.replace('/','-')!=path.stem or zone.split('/',1)[0]!=world:
                    raise RegionMatchError(f'区域资源文件与声明路径不一致：{path.name}')
                headers.append((zone,display,kind))
            finally:wad.close()
        records=[RegionRecord(zone,display,kind,language.values(display)) for zone,display,kind in headers]
        cache[cache_key]=tuple(records)
    return choose_region_target(goal_zone,split_quest_location(objective)[1],cache[cache_key],client=client)


async def _menu_base(client):
    button=await get_spiral_teleport_button(client)
    parent=await button.parent() if button else None
    if parent is None or await parent.name()!='ControlSprite':raise MenuNotReady('传送窗口尚未就绪')
    if await button.maybe_read_type_name()!='ControlButton':raise MenuNotReady('传送按钮类型尚未就绪')
    children=await parent.children()
    panels=[w for w in children if await w.name()=='optionWindow' and await w.is_visible()]
    if len(panels)!=1:raise MenuNotReady('目的地列表尚未唯一就绪')
    titles=[]
    for window in children:
        if await window.name() in ('title','streamTitle') and await window.is_visible():
            text=await read_control_text(window)
            if menu_kind(text,client=client) is None:raise MenuNotReady('可见标题文本为空或不可读')
            titles.append(text)
    identities={('world_gate' if menu_kind(text,client=client)=='world_gate' else html.unescape(text).strip()) for text in titles}
    if len(identities)!=1:raise MenuNotReady('可见标题不可读或互相冲突')
    return parent,panels[0],button,titles[0]


async def read_menu_title(client):
    return (await _menu_base(client))[3]


@dataclass(frozen=True)
class MenuOption:
    name:str
    text:str
    window:object


@dataclass(frozen=True)
class MenuPage:
    identity:tuple
    title:str
    page:int
    total:int
    options:tuple
    panel:object
    button:object
    controls:tuple

    @property
    def content(self):return tuple((row.name,row.text) for row in self.options)


async def read_menu(client):
    parent,panel,button,title=await _menu_base(client)
    controls=await panel.children();pages=[];options=[]
    for child in controls:
        name=await child.name()
        if not await child.is_visible():continue
        if name=='pageCount':
            match=re.fullmatch(r'(\d+)\s*/\s*(\d+)',plain_text(await read_control_text(child)))
            if match:pages.append(tuple(map(int,match.groups())))
        elif re.fullmatch(r'opt\d+',name):
            if await child.maybe_read_type_name()!='ControlCheckBox':raise MenuNotReady('目的地不是已确认的选择控件')
            text=await read_control_text(child)
            if not text or not text.strip():raise MenuNotReady('目的地文本尚未就绪')
            options.append(MenuOption(name,text,child))
    if len(pages)!=1 or not 1<=pages[0][0]<=pages[0][1]<=MAX_PAGES or not options:
        raise MenuNotReady('页码或目的地内容无效/未就绪')
    return MenuPage((await parent.read_base_address(),title),title,*pages[0],tuple(options),panel,button,tuple(controls))


async def _available(window):
    return (await window.is_visible() and WindowFlags.disabled not in await window.flags()
            and await window.is_control_grayed() is False)


async def _checked(option):
    # Only this region checkbox uses the native m_bChecked property. The old
    # dependency's +0x404 points elsewhere; +0x40c is also verified by a user
    # selecting opt3 while opt0/1/2 remain false. No dependency offsets change.
    value=await option.window.read_value_from_offset(0x40c,Primitive.uint8)
    if value not in (0,1):raise MenuNotReady('目的地选中字段暂不可读')
    return bool(value)


async def travel_region_menu(client,*,read_objective):
    from src.teleport_math import _movement_body,_movement_control_changed
    ownership=get_client_automation_ownership(client)
    if (ownership.locked and ownership._owner_task is not asyncio.current_task()
            or getattr(client,'_quest_region_transfer_active',False)):
        return None
    task=await client.quest_id(),await client.goal_id()
    zone=await client.zone_name();objective=await read_objective(client)
    goal=None;goal_zone=None;target=None;seen=[];identity=None;total=None

    def blocked(reason):
        diagnostic=dict(client=client.title,task=task,goal_zone=goal_zone,objective=objective,
            target_zone=target.zone if target else None,destinations=list(seen),reason=reason)
        previous=getattr(client,'_quest_region_transfer_diagnostic',None)
        if previous!=diagnostic:logger.warning('区域传送未匹配/未确认：{}',diagnostic)
        client._quest_region_transfer_diagnostic=diagnostic
        return False

    async def current(*,travel=False):
        if (not getattr(client,'questing_status',True)
                or hasattr(client,'is_running') and not client.is_running()
                or getattr(client,'quest_party_quester',None) not in (None,client)
                or any(getattr(client,flag,False) for flag in BUSY_FLAGS)):
            return False
        loading=await client.is_loading()
        if loading and travel:
            return True  # No new inputs after Travel; HUD/body may be rebuilding.
        if await client.in_battle():return False
        if (await client.quest_id(),await client.goal_id())!=task:return False
        if await _movement_control_changed(client,control):return False
        if await read_objective(client)!=objective:return False
        if goal is not None and await goal.goal_destination_zone()!=goal_zone:return False
        if not travel and (loading or await client.zone_name()!=zone):return False
        return True

    async def frame(*,expected_page=None):
        async with asyncio.timeout(READ_TIMEOUT):
            while True:
                if not await current():return None
                try:
                    page=await read_menu(client)
                except UI_READ_ERRORS:
                    await asyncio.sleep(.1);continue
                except RuntimeError as exc:
                    if not str(exc).startswith(('控件文本结构无效：','控件文本指针无效：')):raise
                    await asyncio.sleep(.1);continue
                if menu_kind(page.title,client=client)!='region':raise RegionMatchError('当前标题是世界门，区域处理器不执行')
                if identity is not None and page.identity!=identity:raise RegionMatchError('传送窗口已替换，停止旧选择')
                if total is not None and page.total!=total:raise RegionMatchError('菜单总页数发生变化')
                if expected_page is None or page.page==expected_page:return page
                await asyncio.sleep(.1)

    async def click(page,name,*,selected=False,on_dispatched=None):
        async def button():
            fresh=await frame()
            if fresh is None:return None
            if (fresh.page,fresh.total,fresh.content)!=(page.page,page.total,page.content):
                raise RegionMatchError('输入前页面内容发生变化')
            if selected:
                if [row.name for row in fresh.options if await _checked(row)]!=[selected]:
                    raise RegionMatchError('传送前选中状态已变化')
                window=fresh.button
            else:
                windows=[w for w in fresh.controls if await w.name()==name]
                if len(windows)!=1:raise RegionMatchError(f'控件不唯一：{name}')
                window=windows[0]
            if not await _available(window):raise RegionMatchError(f'控件灰色或禁用：{name}')
            return window

        # MouseHandler.click_window checks neither task nor UI after waiting on
        # click_lock/predelay. Share its lock/hook and position API, but guard
        # this menu's synchronous down/up pair after those waits. Public mouse
        # behavior and dependency files are unchanged.
        mouse=client.mouse_handler
        async with mouse:
            if mouse.click_lock is None:mouse.click_lock=asyncio.Lock()
            async with mouse.click_lock:
                window=await button()
                if window is None:return False
                center=(await window.scale_to_client()).center()
                await mouse.set_mouse_position(*center)
                try:
                    await asyncio.sleep(mouse.click_predelay)
                    window=await button()
                    if window is None:return False
                    if (await window.scale_to_client()).center()!=center:
                        raise RegionMatchError('输入前控件位置发生变化')
                    if not await current():return False
                    user32.SendMessageW(client.window_handle,0x201,1,0)
                    user32.SendMessageW(client.window_handle,0x202,0,0)
                    if on_dispatched is not None:on_dispatched()
                    return True
                finally:
                    await mouse.set_mouse_position(-100,-100)

    async def click_page(page,step):
        name='rightButton' if step==1 else 'leftButton'
        fresh=await frame()
        if fresh is None:return None
        if (fresh.page,fresh.total,fresh.content)!=(page.page,page.total,page.content):
            raise RegionMatchError('点击翻页前页面内容发生变化')
        buttons=[w for w in fresh.controls if await w.name()==name]
        if len(buttons)!=1 or not await _available(buttons[0]):raise RegionMatchError(f'翻页按钮不可用：{name}')
        if not await click(fresh,name):return None
        next_page=await frame(expected_page=page.page+step)
        if next_page is not None and next_page.total!=page.total:raise RegionMatchError('菜单总页数发生变化')
        return next_page

    control=None
    try:
        _,control=await _movement_body(client)
        if not await current():return None
        if any(type(value) is not int or value<=0 for value in task):return blocked('任务ID/目标ID尚未就绪')
        async with automation_owner(client,'region-teleport'):
            if not await current():return None
            client._quest_region_transfer_active=True
            try:
                goal=await read_active_goal(client,task)
                goal_zone=await goal.goal_destination_zone() if goal else None
                if not await current():return None
                page=await frame()
                if page is None:return None
                identity=page.identity;total=page.total
                seen.extend(dict(page=page.page,control=row.name,text=row.text) for row in page.options)
                receipt_key=(task,zone,objective,goal_zone,identity)
                receipt=getattr(client,'_quest_region_transfer_receipt',None)
                if isinstance(receipt,dict) and receipt.get('key')==receipt_key and receipt.get('sent'):
                    return blocked('本窗口已发出传送请求，未确认到达；不重复点击')
                contents={};matches=[]
                seen.clear()
                async with asyncio.timeout(45):
                    for _ in range(page.total-1):
                        if page.page==1:break
                        page=await click_page(page,-1)
                        if page is None:return None
                    for index in range(1,page.total+1):
                        if page.page!=index:raise RegionMatchError('页面顺序不连续')
                        contents[index]=page.content
                        for option in page.options:
                            seen.append(dict(page=index,control=option.name,text=option.text))
                        if index<page.total:
                            page=await click_page(page,1)
                            if page is None:return None
                    target=await resolve_region_target(client,goal_zone,objective)
                    if not await current():return None
                    if target.zone==zone:return blocked('已在目标所属区域，不重复进行区域传送')
                    matches=[(row['page'],row['control'],row['text']) for row in seen if option_names(row['text'],client=client)&target.names]
                    if len(matches)!=1:return blocked(f'目的地候选为 {len(matches)} 个，要求唯一；依据={target.proof}')
                    number,name,text=matches[0]
                    for _ in range(page.total-1):
                        if page.page==number:break
                        page=await click_page(page,-1 if page.page>number else 1)
                        if page is None:return None
                    page=await frame()
                    if page is None:return None
                    if page.page!=number or page.content!=contents[number]:return blocked('返回目标页后目的地内容已变化')
                    option=next(row for row in page.options if (row.name,row.text)==(name,text))
                    if not await _available(option.window):return blocked('目标目的地按钮灰色或禁用')
                    if not await click(page,name):return None
                    async with asyncio.timeout(SELECTION_TIMEOUT):
                        while True:
                            page=await frame()
                            if page is None:return None
                            if page.page!=number or page.content!=contents[number]:return blocked('确认选中时页面内容已变化')
                            selected=[row.name for row in page.options if await _checked(row)]
                            if selected==[name] and await _available(page.button):break
                            await asyncio.sleep(.1)
                    receipt=dict(key=receipt_key,sent=False,target=target.zone)
                    client._quest_region_transfer_receipt=receipt
                    if not await click(page,'teleportButton',selected=name,
                            on_dispatched=lambda:receipt.update(sent=True)):return None
                    if not receipt['sent']:return blocked('传送输入未确认派发')
                async with asyncio.timeout(TRAVEL_TIMEOUT):
                    while True:
                        try:
                            if not await current(travel=True):return None
                            if await client.is_loading():
                                await asyncio.sleep(.1);continue
                            arrived=await client.zone_name()
                            if arrived!=zone:
                                if arrived!=target.zone:return blocked(f'实际到达 {arrived}，预期 {target.zone}')
                                fresh_goal=await read_active_goal(client,task)
                                if (fresh_goal is None or await fresh_goal.goal_destination_zone()!=goal_zone
                                        or not await current(travel=True)):
                                    return None
                                if await client.is_loading() or await client.zone_name()!=target.zone:continue
                                logger.info('Client {}：区域传送已确认到达 {}，任务已重新读取，交回现有自动任务',client.title,arrived)
                                return True
                        except UI_READ_ERRORS:
                            if not await client.is_loading():raise
                        await asyncio.sleep(.1)
            finally:
                client._quest_region_transfer_active=False
    except asyncio.TimeoutError:
        if not await current(travel=True):return None
        return blocked('菜单读取、翻页、选中/按钮确认或到达确认超时；停止本轮，不重复传送')
    except (RegionMatchError,*UI_READ_ERRORS,OSError) as exc:
        return blocked(str(exc))

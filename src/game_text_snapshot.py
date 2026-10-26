"""Validated, immutable language values per actual game install and application run.

IDs and action semantics remain in game_text_catalog. This service replaces only
their text values; it never learns a new action from an English/Chinese label.
"""
import asyncio
import ctypes
from dataclasses import dataclass
import os
from pathlib import Path
import re
import struct
from threading import RLock
from types import MappingProxyType

from loguru import logger
from src.game_text_catalog import INTERACTION_RECORDS,PORTAL_RECORDS,TEXT_RECORDS,WORLD_RECORDS

_FIXED={key:tuple(values) for group in (INTERACTION_RECORDS,TEXT_RECORDS,PORTAL_RECORDS,WORLD_RECORDS)
        for key,values in group.items()}
_TABLES=frozenset({key.split('_',1)[0] for key in _FIXED}|{'WizardZone'})
_CJK=re.compile(r'[\u3400-\u9fff]')
_SNAPSHOTS={}
_LOCK=RLock()


@dataclass(frozen=True,eq=False)
class LanguageSnapshot:
    records:object
    install:Path|None=None
    sources:tuple=()
    error:str|None=None

    def values(self,text_id):
        return self.records.get(text_id,_FIXED.get(text_id,()))


_FALLBACK=LanguageSnapshot(MappingProxyType({}))


def _process_install_path(client):
    """Query this already-open process handle; never use the SDK's global install."""
    from ctypes import wintypes
    handle=client._pymem.process_handle
    handle=getattr(handle,'value',handle)
    if type(handle) is not int or not handle:
        raise ValueError('client process handle unavailable')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    query=kernel.QueryFullProcessImageNameW
    query.argtypes=(wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD))
    query.restype=wintypes.BOOL
    buffer=ctypes.create_unicode_buffer(32768);length=wintypes.DWORD(len(buffer))
    if not query(handle,0,buffer,ctypes.byref(length)):
        raise OSError(ctypes.get_last_error(),'QueryFullProcessImageNameW failed')
    image=Path(buffer.value)
    if image.name.casefold()!='wizardgraphicalclient.exe' or image.parent.name.casefold()!='bin':
        raise ValueError('unexpected game executable location')
    return image.parent.parent


def client_install_path(client):
    if client is None:
        return None
    existing=getattr(client,'_text_install_location',None)
    if isinstance(existing,(str,os.PathLike)):
        return Path(existing)
    try:
        process=getattr(client,'_pymem',None)
        if process is not None:
            install=_process_install_path(client)
        else:
            # Explicit paths also support offline readers without a live process.
            install=getattr(getattr(client,'cache_handler',None),'install_location',None)
        if not isinstance(install,(str,os.PathLike)):
            return None  # Startup may precede discovery; do not freeze an unknown path.
        install=Path(install).resolve()
    except (OSError,ValueError,AttributeError) as exc:
        if not getattr(client,'_text_install_warning',False):
            logger.warning('客户端 {} 实际语言资源目录尚不可用，暂用固定表后备：{}',getattr(client,'title','?'),exc)
            client._text_install_warning=True
        return None
    client._text_install_location=install
    return install


def _signature(paths):
    result=[]
    for path in paths:
        try:
            stat=path.stat()
        except FileNotFoundError:
            result.append((str(path),None))
        else:
            result.append((str(path),stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns))
    return tuple(result)


def _load_snapshot(install):
    # Only active archives participate. .wad.d/.r/.c are patch sources, not
    # active overrides. Locale_English-Root.wad is the verified font archive.
    from src.collect_catalog import language_files,parse_language
    data=install/'Data/GameData'
    paths=(data/'Root.wad',data/'Locale_en-US-root.wad')
    before=_signature(paths)
    if not paths[0].is_file():
        raise FileNotFoundError(f'{paths[0]} missing')
    base={};effective={}
    for index,path in enumerate(paths):
        if not path.is_file():
            continue
        for name,content in language_files(path,strict=True,tables=_TABLES):
            table=Path(name).stem
            rows=list(parse_language(content,strict=True,expected_table=table))
            for code,_,display in rows:
                if index==0:
                    base[code]=display
                effective[code]=display  # The active overlay wins per ID, never by union.
    if _signature(paths)!=before:
        raise ValueError('language files changed/replaced while reading')
    records={}
    for code,display in effective.items():
        # The middle language field can be a developer description. Preserve
        # verified fixed English and Root's English, never treat it as an alias.
        english=[value for value in _FIXED.get(code,()) if value and not _CJK.search(value)]
        root_value=base.get(code,'')
        if root_value and not _CJK.search(root_value):
            english.append(root_value)
        records[code]=tuple(dict.fromkeys([*english,display]))
    return LanguageSnapshot(MappingProxyType(records),install,before)


def snapshot_for_install(install):
    install=Path(install).resolve()
    key=os.path.normcase(str(install))
    with _LOCK:
        if key not in _SNAPSHOTS:
            try:
                snapshot=_load_snapshot(install)
            except (OSError,ValueError,UnicodeError,struct.error) as exc:
                snapshot=LanguageSnapshot(MappingProxyType({}),install,error=str(exc))
                logger.warning('语言快照读取失败：install={}；本次运行固定使用内置表后备，重启后重读；原因={}',install,exc)
            else:
                missing=len(_FIXED.keys()-snapshot.records.keys())
                logger.info('语言快照已建立：install={} 记录数={} 固定表ID缺失数={}；优先使用 Locale_en-US-root.wad；若ID缺失则使用内置固定表；本次运行不刷新',
                            install,len(snapshot.records),missing)
            _SNAPSHOTS[key]=snapshot  # Publish only a complete profile or an explicit fallback.
        return _SNAPSHOTS[key]


def snapshot_for_client(client=None):
    if client is None:
        return _FALLBACK
    snapshot=getattr(client,'_game_text_snapshot',None)
    if isinstance(snapshot,LanguageSnapshot):
        return snapshot
    install=client_install_path(client)
    if install is None:
        return _FALLBACK
    snapshot=snapshot_for_install(install)
    client._game_text_snapshot=snapshot
    return snapshot


async def initialize_client_language(client):
    return await asyncio.to_thread(snapshot_for_client,client)


def text_values(text_id,client=None):
    return snapshot_for_client(client).values(text_id)

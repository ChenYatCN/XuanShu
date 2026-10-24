"""Launcher-only account organization stored alongside existing account overrides."""

from uuid import uuid4

DEFAULT_ACCOUNT_GROUPS = {'favorites': '常用', 'warehouse': '仓库', 'other': '其他'}


def get_account_groups(settings, defaults=None) -> dict[str, str]:
    defaults = defaults or DEFAULT_ACCOUNT_GROUPS
    saved = settings.get_setting('account_group_definitions') if settings else None
    if not isinstance(saved, dict):
        return dict(defaults)
    groups = {key: name.strip() for key, name in saved.items()
              if isinstance(key, str) and key and key != 'all'
              and isinstance(name, str) and name.strip()}
    if 'favorites' not in groups:
        groups = {'favorites': defaults['favorites'], **groups}
    return groups


def _validate_group_name(groups, name, current=None):
    name = name.strip()
    if not name:
        raise ValueError('account_group_name_empty')
    if name.casefold() in ('全部', 'all'):
        raise ValueError('account_group_name_reserved')
    if any(key != current and existing.casefold() == name.casefold()
           for key, existing in groups.items()):
        raise ValueError('account_group_name_duplicate')
    return name


def create_group(settings, name: str, defaults=None) -> str:
    groups = get_account_groups(settings, defaults)
    name = _validate_group_name(groups, name)
    group_id = uuid4().hex
    groups[group_id] = name
    settings.set_setting('account_group_definitions', groups)
    return group_id


def rename_group(settings, group_id: str, name: str, defaults=None):
    groups = get_account_groups(settings, defaults)
    if group_id not in groups:
        raise ValueError('account_group_missing')
    groups[group_id] = _validate_group_name(groups, name, group_id)
    settings.set_setting('account_group_definitions', groups)


def delete_group(settings, group_id: str, defaults=None):
    groups = get_account_groups(settings, defaults)
    if group_id == 'favorites':
        raise ValueError('account_group_default_protected')
    if group_id not in groups:
        raise ValueError('account_group_missing')
    groups.pop(group_id)
    saved = settings.get_setting('account_groups')
    assignments = saved if isinstance(saved, dict) else {}
    changes = {
        'account_group_definitions': groups,
        'account_groups': {nick: 'favorites' if group == group_id else group
                           for nick, group in assignments.items()},
    }
    if settings.get_setting('launcher_account_group') == group_id:
        changes['launcher_account_group'] = 'favorites'
    settings.set_settings(changes)


def get_account_group(settings, nickname: str) -> str:
    groups = settings.get_setting('account_groups') if settings else None
    group = groups.get(nickname) if isinstance(groups, dict) else None
    return group if isinstance(group, str) and group in get_account_groups(settings) else 'favorites'


def set_account_group(settings, nickname: str, group: str):
    if group not in get_account_groups(settings):
        raise ValueError('Unknown account group')
    saved = settings.get_setting('account_groups')
    groups = dict(saved) if isinstance(saved, dict) else {}
    groups[nickname] = group
    settings.set_setting('account_groups', groups)


def rename_account_group(settings, old: str, new: str):
    if old == new:
        return
    saved = settings.get_setting('account_groups')
    groups = dict(saved) if isinstance(saved, dict) else {}
    if old in groups:
        groups[new] = groups.pop(old)
        settings.set_setting('account_groups', groups)


def remove_account_group(settings, nickname: str):
    saved = settings.get_setting('account_groups')
    groups = dict(saved) if isinstance(saved, dict) else {}
    if nickname in groups:
        groups.pop(nickname)
        settings.set_setting('account_groups', groups)

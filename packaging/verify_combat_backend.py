"""Probe double-enchant support in the selected sources or the compiled EXE.

No game process is opened. The smoke test parses real playstyles and checks
the resulting Move objects, rather than trusting a package version string.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import inspect
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VENV_PACKAGES = ROOT / '.venv' / 'Lib' / 'site-packages'
PREFIX = 'wizwalker.extensions.wizsprinter'
COMBAT_SOURCE = ROOT / 'libs/wizsprinter/wizwalker/extensions/wizsprinter/sprinty_combat.py'
BACKEND_MODULES = (
    PREFIX + '.combat_backends.combat_api',
    PREFIX + '.combat_backends.combat_config_parser',
    PREFIX + '.sprinty_combat',
    PREFIX + '.combat_backends.config_backend',
)


def check_capabilities(provider_class, combat_class):
    provider = object.__new__(provider_class)
    examples = (
        ('Glowbug[Shrike][epic] | pass', 'Glowbug', 'Shrike', False),
        ('"Sound of Musicology - T1"["Shadow Creature Shrike"][epic] @ enemy | pass',
         'Sound of Musicology - T1', 'Shadow Creature Shrike', True),
    )
    for text, card, material, literal in examples:
        config = provider.parse_config(text)
        priorities = config.infinite_rounds[0].priorities
        move = priorities[0].move
        if not (move.card.name == card and move.card.is_literal == literal
                and move.enchant.name == material
                and move.enchant.is_literal == literal
                and getattr(move, 'second_enchant', None) is not None
                and move.second_enchant.name == 'epic'
                and priorities[-1].move.card.name == 'pass'):
            raise RuntimeError('战斗后端未正确保留融合素材、第二层 Epic 或 pass 后备动作。')
    # Parsing alone is insufficient: the executor must consume second_enchant.
    execute = getattr(combat_class, 'try_execute_config', None)
    if execute is None or 'second_enchant' not in execute.__code__.co_names:
        raise RuntimeError('战斗执行器缺少 second_enchant，无法执行融合后的第二次附魔。')


def load_source_backend():
    if Path(sys.prefix).resolve() != (ROOT / '.venv').resolve():
        raise RuntimeError('请使用项目 .venv/Scripts/python.exe 运行检测。')
    # Same dependency precedence as 启动XuanShu.bat and XuanShu.spec.
    sys.path.insert(0, str(VENV_PACKAGES))
    importlib.invalidate_caches()
    modules = {name: importlib.import_module(name) for name in ('wizwalker', *BACKEND_MODULES)}
    for name, module in modules.items():
        source = Path(module.__file__).resolve()
        if not source.is_relative_to(VENV_PACKAGES.resolve()):
            raise RuntimeError(f'检测到旧版或外部依赖覆盖 {name}：{source}')
    provider = modules[BACKEND_MODULES[-1]].CombatConfigProvider
    runtime_combat = modules[BACKEND_MODULES[2]].SprintyCombat
    check_capabilities(provider, runtime_combat)
    print(f'[后端来源] 解析器：{modules[BACKEND_MODULES[1]].__file__}')
    print(f'[后端来源] 源码启动执行器：{inspect.getfile(runtime_combat)}')
    # PyInstaller intentionally replaces only SprintyCombat with this local
    # compatibility module; import it against the same installed backend API.
    spec = importlib.util.spec_from_file_location(PREFIX + '._build_probe', COMBAT_SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'找不到打包用战斗执行器：{COMBAT_SOURCE}')
    compatibility = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compatibility)
    check_capabilities(provider, compatibility.SprintyCombat)
    print(f'[后端来源] 打包兼容执行器：{COMBAT_SOURCE}')
    print('[检测通过] 新版战斗后端支持 A[B][epic]，两层附魔和后备动作解析正确。')


def check_bundle_sources(entries):
    """Verify the parser, API and provider actually selected by PyInstaller."""
    for module_name in (BACKEND_MODULES[0], BACKEND_MODULES[1], BACKEND_MODULES[3]):
        expected = (VENV_PACKAGES / (module_name.replace('.', '/') + '.py')).resolve()
        matches = [Path(source).resolve() for name, source, kind in entries
                   if name == module_name and kind.startswith('PYMODULE')]
        if not matches or any(source != expected for source in matches):
            raise RuntimeError(f'战斗后端来源不正确或缺失：{module_name}；期望 {expected}，实际 {matches}')


def check_compiled_backend(exe):
    """Load ONLY the four combat modules from PYZ, not the app entry point.

    Dependency imports use the checked build environment. Execute the bundled
    grammar/API/provider together so an old compiled parser cannot be hidden
    by a newer loose .py data file or newer installed package.
    """
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(exe))
    pyz_names = [name for name in archive.toc if name.endswith('.pyz')]
    if len(pyz_names) != 1:
        raise RuntimeError('成品没有唯一的 PYZ 模块归档，无法确认实际战斗后端。')
    pyz = archive.open_embedded_archive(pyz_names[0])
    previous = {}
    try:
        for name in BACKEND_MODULES:
            if name not in pyz.toc:
                raise RuntimeError(f'成品缺少编译后的战斗模块：{name}')
            code = pyz.extract(name)
            if not isinstance(code, types.CodeType):
                raise RuntimeError(f'无法读取成品战斗模块：{name}')
            previous[name] = sys.modules.get(name)
            module = types.ModuleType(name)
            module.__package__ = name.rpartition('.')[0]
            module.__file__ = f'{exe}!{name}'
            sys.modules[name] = module
            exec(code, module.__dict__)
        provider = sys.modules[BACKEND_MODULES[-1]].CombatConfigProvider
        combat = sys.modules[BACKEND_MODULES[2]].SprintyCombat
        check_capabilities(provider, combat)
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    print(f'[成品检测通过] {exe} 的编译后端实际支持 A[B][epic]。')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--exe', type=Path)
    args = parser.parse_args()
    try:
        load_source_backend()
        if args.exe:
            check_compiled_backend(args.exe)
    except Exception as error:
        print(f'[后端检测失败] {error}', file=sys.stderr)
        print('已阻止继续打包/交付，请检查上面的依赖来源。', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

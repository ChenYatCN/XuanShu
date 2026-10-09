"""Verify this isolated import-only build, then prepare uniquely named delivery files."""
import difflib
import dis
import hashlib
import json
import marshal
from pathlib import Path
import types
import zipfile

from PyInstaller.archive.readers import CArchiveReader

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
NAME = 'XuanShu-autoquest-415-base-importfix-r1-20261009'
PACKAGE = HERE / 'package'
snapshot = json.loads((HERE / 'snapshot.json').read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def structure(value):
    if isinstance(value, types.CodeType):
        fields = ('co_argcount', 'co_posonlyargcount', 'co_kwonlyargcount',
                  'co_nlocals', 'co_stacksize', 'co_flags', 'co_code', 'co_consts',
                  'co_names', 'co_varnames', 'co_name', 'co_qualname',
                  'co_firstlineno', 'co_linetable', 'co_exceptiontable',
                  'co_freevars', 'co_cellvars')
        return {'code': {key: structure(getattr(value, key)) for key in fields}}
    if isinstance(value, bytes):
        return {'bytes': value.hex()}
    if isinstance(value, tuple):
        return {'tuple': [structure(v) for v in value]}
    if isinstance(value, frozenset):
        return {'frozenset': sorted([structure(v) for v in value], key=repr)}
    return {'type': type(value).__name__, 'repr': repr(value)}


before = (HERE / 'XuanShu.py.before').read_bytes()
after = (ROOT / 'XuanShu.py').read_bytes()
expected = before.replace(b'from src.auto_pet import nomnom\r\n',
    b'from src.auto_pet import nomnom\r\nfrom src.automation_ownership import automation_owner\r\n', 1)
assert after == expected, 'production main change is not exactly the intended import'
for relative in ('src/questing.py', 'src/utils.py', 'src/teleport_math.py'):
    assert sha(ROOT / relative) == snapshot['source_hashes_before'][relative], relative
(HERE / 'import-only.diff').write_text(''.join(difflib.unified_diff(
    before.decode('utf-8-sig').splitlines(keepends=True),
    after.decode('utf-8-sig').splitlines(keepends=True),
    fromfile='XuanShu.py.before', tofile='XuanShu.py')), encoding='utf-8')

exe = PACKAGE / (NAME + '.exe')
original = PACKAGE / 'XuanShu.exe'
assert not exe.exists(), 'delivery filename already exists'
assert original.is_file()
original.rename(exe)
reader = CArchiveReader(str(exe))
pyz_name = next(name for name in reader.toc if name.endswith('.pyz'))
pyz = reader.open_embedded_archive(pyz_name)
packed_main = marshal.loads(reader.extract('XuanShu'))
report = {}
modules = [('XuanShu.py', packed_main)]
modules += [(relative, pyz.extract(relative[:-3].replace('/', '.')))
            for relative in ('src/questing.py', 'src/utils.py', 'src/teleport_math.py',
                             'src/automation_ownership.py', 'src/task_lifecycle.py')]
for relative, packed in modules:
    current = compile((ROOT / relative).read_bytes(), relative, 'exec', optimize=2)
    normalized = structure(packed)
    assert normalized == structure(current), f'embedded code mismatch: {relative}'
    report[relative] = {
        'matches_current_code': True,
        'source_sha256': sha(ROOT / relative),
        'code_fingerprint': hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest(),
    }
instructions = list(dis.get_instructions(packed_main))
assert any(i.opname == 'IMPORT_NAME' and i.argval == 'src.automation_ownership' for i in instructions)
assert any(i.opname == 'IMPORT_FROM' and i.argval == 'automation_owner' for i in instructions)
assert any(i.opname == 'STORE_NAME' and i.argval == 'automation_owner' for i in instructions)
report['embedded_main_import_confirmed'] = True
report['comparison'] = 'Structural compiled code at optimize=2; only build-path filenames excluded.'
(HERE / 'packed-code-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')

exe_hash = sha(exe)
old_exe = ROOT / 'artifacts/auto-quest-415-base-20261009-163300/package/XuanShu-autoquest-415-base-test-20261009.exe'
assert sha(old_exe) == '70287493b760531cec8c68dfbebe8683958057e82d9a1188d659989cba73b8a9'
assert exe_hash != sha(old_exe)
readme = f'''# 4.1.5 基底导入修复版 r1

本轮生产源码仅新增主程序导入：
from src.automation_ownership import automation_owner

根因：dialogue_loop 与 _follow_quester_session 使用了现有输入协调接口，但主程序未导入它。
之前的函数片段测试手工提供该名称，掩盖了主程序缺失导入。这是验证遗漏。
本轮没有修改任务执行、普通对话、恢复分支、坐标跟随或好友传送流程。

验证：8项测试全部通过。真实 import 整个 XuanShu 后，取已加载的生产代码对象，
独立对话、任务内对话、坐标跟随、好友传送均使用同一个真实 app.__dict__。
输入锁 automation_owner 和 gather_owned 使用实际导入，未手工补入这些接口。
仅模拟游戏客户端及读写操作、main 启动时设置的 walker 和必要闭包状态。
删除真实主程序的 automation_owner 后，审计能检测缺失，独立对话能复现 NameError。
58个基底方法、5个直接辅助方法及3个移动/菜单辅助函数的外部名称审计通过。
walker 是 main 中初始化的 ClientHandler 启动状态，已核对真实 STORE_GLOBAL。

独立新打包；主程序和5个相关模块内嵌编译码与当前源码一致，
内嵌主程序包含 automation_owner 的 IMPORT_NAME、IMPORT_FROM 和 STORE_NAME。
src/questing.py、src/utils.py、src/teleport_math.py 哈希与修复前相同。
旧候选原文件保留，新文件名：{exe.name}
EXE SHA256：{exe_hash}

尚未在实际游戏内验证，以上为真实主程序命名解析与模拟游戏读写测试。
请使用这个 r1 文件复测之前立即报错的入口。
'''
(PACKAGE / '修复说明.md').write_text(readme, encoding='utf-8')
(PACKAGE / 'SHA256SUMS.txt').write_text(f'{exe_hash}  {exe.name}\n', encoding='utf-8')
archive = HERE / (NAME + '.zip')
with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
    for file in (exe, PACKAGE / '修复说明.md', PACKAGE / 'SHA256SUMS.txt',
                 HERE / 'runtime-entry-tests.log', HERE / 'packed-code-verification.json'):
        bundle.write(file, arcname=file.name)
with zipfile.ZipFile(archive) as bundle:
    assert bundle.testzip() is None
    assert hashlib.sha256(bundle.read(exe.name)).hexdigest() == exe_hash
manifest = {
    'exe': str(exe), 'exe_size': exe.stat().st_size, 'exe_sha256': exe_hash,
    'zip': str(archive), 'zip_size': archive.stat().st_size, 'zip_sha256': sha(archive),
    'tests': '8 passed; production main namespace, game IO simulated',
    'production_source_change': 'one existing-interface import in XuanShu.py',
    'embedded_code_matches': list(name for name, _ in modules),
    'old_candidate_preserved': True, 'live_game_verified': False,
}
(HERE / 'delivery-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
print(json.dumps(manifest, indent=2))

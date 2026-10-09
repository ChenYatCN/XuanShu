import ast, unittest
from pathlib import Path
from loguru import logger
import src.questing as questing
logger.remove()
tree = ast.parse(Path('artifacts/auto-quest-415-restore-20261009/src/questing.py').read_text(encoding='utf-8-sig'))
for name in ('_advance_npc_dialogue', '_maybe_refresh_stalled_dungeon_quest', '_recover_power_core_exit', '_maybe_recover_nightmare', '_run_nightmare_recovery', '_nightmare_can_act'):
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
    namespace = dict(questing.__dict__)
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<initial snapshot>', 'exec'), namespace)
    setattr(questing.Quester, name, namespace[name])
names = [
 'tests.test_power_core_recovery.PowerCoreRecoveryTests.test_exit_confirmation_and_party_transition',
 'tests.test_nightmare_krok_recovery.NightmareKrokRecoveryTests.test_strict_twelve_point_order_then_exit_and_stable_zone',
 'tests.test_darkmoor_castle_route.DarkmoorCastleRouteTests.test_dialogue_worker_records_short_page_between_route_ticks',
]
result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(names))
print('Representative failing cases against captured initial methods:', len(result.failures), 'failures,', len(result.errors), 'errors')

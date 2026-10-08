"""Offline probes using the real logout/click loop; never connect to the game."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from loguru import logger
from src.ibao_core import CharacterSelectionError, Keycode, wizardInfo
from src.ibao_runtime import IbaoGroups, create_session

logger.remove()
SCALE = .1  # Keep sleeps above Windows scheduler granularity.


async def scenario(label, *, initial='world', settings_delay=.5,
                   confirmation_delay=0, overlay=False, loading_seconds=0,
                   lost_first_escape=False, vanished_quit=False,
                   read_error=False, hang_read=False, cancel=False):
    started = asyncio.get_running_loop().time()
    clock = lambda: (asyncio.get_running_loop().time() - started) / SCALE
    state = SimpleNamespace(phase=initial, menu_at=None, confirm_at=None,
                            esc=0, quit=0, confirm=0, play=0, loading_inputs=0,
                            errors=0, quit_lookups=0)
    client = SimpleNamespace(title='simulation', window_handle=0, root_window=object())
    scope = create_session(client, {'switch_delay': .3})
    scope['print'] = lambda *args, **kwargs: None
    scope['asyncio'] = SimpleNamespace(
        sleep=lambda seconds: asyncio.sleep(seconds * SCALE),
        timeout=lambda seconds: asyncio.timeout(seconds * SCALE),
        get_running_loop=lambda: SimpleNamespace(time=clock))

    def refresh():
        if state.menu_at is not None and clock() >= state.menu_at:
            state.phase, state.menu_at = 'settings', None
        if state.confirm_at is not None and clock() >= state.confirm_at:
            state.phase, state.confirm_at = 'confirm', None

    async def loading():
        if hang_read:
            await asyncio.Event().wait()
        if read_error and state.errors == 0:
            state.errors += 1
            raise RuntimeError('transient root read failure')
        return clock() < loading_seconds
    client.is_loading = loading
    async def key(code, duration=.3):
        if clock() < loading_seconds:
            state.loading_inputs += 1
        if code == Keycode.ESC:
            state.esc += 1
            if not (lost_first_escape and state.esc == 1) and state.menu_at is None:
                state.menu_at = clock() + settings_delay
        await asyncio.sleep(duration * SCALE)
    client.send_key = key
    client.wait_for_zone_change = AsyncMock()

    def path_visible(path):
        refresh()
        if path == scope['quitButton']:
            return state.phase == 'settings' or (overlay and state.phase == 'confirm')
        if path == scope['logOutConfirm']:
            return state.phase == 'confirm'
        if path == scope['playButton']:
            return state.phase == 'selection'
        return False

    async def window(root, path):
        if path in (scope['txtName'], scope['txtLevel'], scope['txtLocation']):
            text = {tuple(scope['txtName']): 'target', tuple(scope['txtLevel']): '100',
                    tuple(scope['txtLocation']): 'The Commons'}[tuple(path)]
            return SimpleNamespace(maybe_text=AsyncMock(return_value=text))
        if path == scope['quitButton'] and vanished_quit:
            state.quit_lookups += 1
            # First lookup sees Settings; second is the helper's fresh lookup.
            if state.quit_lookups == 2:
                return False
        if path_visible(path):
            return SimpleNamespace(path=path, is_visible=AsyncMock(side_effect=lambda: path_visible(path)))
        return False
    scope['window_from_path'] = window

    async def click(control):
        if clock() < loading_seconds:
            state.loading_inputs += 1
        if control.path == scope['quitButton']:
            state.quit += 1
            if state.phase == 'settings':
                if confirmation_delay:
                    state.phase, state.confirm_at = 'pending', clock() + confirmation_delay
                else:
                    state.phase = 'confirm'
        elif control.path == scope['logOutConfirm']:
            state.confirm += 1
            state.phase = 'selection'
        elif control.path == scope['playButton']:
            state.play += 1
            state.phase = 'entered'
    client.mouse_handler = SimpleNamespace(click_window=click)
    task = asyncio.create_task(scope['logout_and_in'](
        client, wizardInfo('target', '100', 'The Commons', 0, 0, 0), True, 'simulation'))
    if cancel:
        await asyncio.sleep(.003)
        task.cancel()
    try:
        await asyncio.wait_for(task, 7)
        outcome = 'entered_target'
    except CharacterSelectionError:
        outcome = 'short_recovery_error'
    except asyncio.CancelledError:
        outcome = 'cancelled'
    except Exception as exc:
        outcome = type(exc).__name__
    return {'scenario': label, 'outcome': outcome, 'phase': state.phase,
            'esc': state.esc, 'exit_clicks': state.quit, 'confirm_clicks': state.confirm,
            'play_clicks': state.play, 'loading_inputs': state.loading_inputs,
            'elapsed_game_seconds': round(clock(), 1)}


async def recovery_probe(error):
    client = SimpleNamespace(title='simulation')
    failed, recovered = asyncio.Event(), asyncio.Event()
    async def guard(run, clients):
        return await run()
    async def worker(client, settings):
        failed.set()
        raise error
    async def recover(client, settings):
        recovered.set()
        await asyncio.Event().wait()
    manager = IbaoGroups(lambda: [client], Mock(), worker=worker, ui_guard=guard,
                         recovery=recover, inactivity_timeout=180)
    manager.add(['simulation'], {})
    await failed.wait()
    try:
        await asyncio.wait_for(recovered.wait(), .03)
    except TimeoutError:
        pass
    result = {'scenario': f'recovery_route_{type(error).__name__}',
              'recovery_started_without_180s_wait': recovered.is_set()}
    await manager.stop(['simulation'])
    return result


async def main():
    probes = [
        ('delayed_settings', {}),
        ('existing_settings', {'initial': 'settings'}),
        ('existing_confirmation', {'initial': 'confirm'}),
        ('already_on_selection', {'initial': 'selection'}),
        ('lost_first_escape', {'lost_first_escape': True}),
        ('delayed_confirmation', {'confirmation_delay': .6}),
        ('quit_disappears_before_click_lookup', {'vanished_quit': True}),
        ('loading_then_settings', {'loading_seconds': 2}),
        ('persistent_missing_controls', {'settings_delay': 999}),
        ('hung_loading_read', {'hang_read': True}),
        ('cancel_during_logout', {'cancel': True}),
        ('confirmation_over_visible_quit', {'overlay': True}),
        ('one_transient_loading_read_error', {'read_error': True}),
        ('healthy_loading_longer_than_15s', {'loading_seconds': 16}),
    ]
    results = [await scenario(label, **options) for label, options in probes]
    results += [await recovery_probe(RuntimeError('transient root read failure')),
                await recovery_probe(CharacterSelectionError('logout timeout'))]
    expected = {'persistent_missing_controls': 'short_recovery_error',
                'hung_loading_read': 'short_recovery_error',
                'cancel_during_logout': 'cancelled'}
    failures = []
    for result in results:
        label = result['scenario']
        if label.startswith('recovery_route_'):
            matches = result['recovery_started_without_180s_wait'] == label.endswith('CharacterSelectionError')
        else:
            matches = (result['outcome'] == expected.get(label, 'entered_target')
                       and result['loading_inputs'] == 0)
            if result['outcome'] == 'entered_target':
                matches = matches and result['play_clicks'] == 1
            if label == 'confirmation_over_visible_quit':
                matches = matches and result['exit_clicks'] == result['confirm_clicks'] == 1
        if not matches:
            failures.append(label)
    print(json.dumps({'offline': True, 'real_game_input': False,
                      'production_modified_by_probe': False, 'time_scale': SCALE,
                      'passed': len(results) - len(failures), 'failures': failures,
                      'results': results}, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())

"""Real launch/recovery control flow with fake native handles and UI only."""
import ast
import asyncio
import threading
import os
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import wizwalker
from src import ibao_core, ibao_runtime as runtime
from wizwalker.errors import HookNotActive

REAL_SLEEP = asyncio.sleep


def account_helpers(namespace, tree=None):
    """Install real private launch helpers into an extracted command fixture."""
    tree = tree or ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
    funcs = [n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))
        and n.name.startswith(('_account_', '_wait_process_', '_rollback_', '_launch_reserved_'))]
    exec(compile(ast.Module(body=funcs,type_ignores=[]),'XuanShu.py','exec'),namespace)
    namespace.update(_account_launches={},_account_launch_generations={},_account_launch_current={},
                     _account_native_launch_lock=asyncio.Lock())
    namespace['_account_owned_wizard_pids']=lambda: {h+100 for h in namespace['get_all_wizard_handles']()}
    namespace['_account_terminate_spawned_pid']=lambda pid: namespace['_kill_process_by_handle'](pid-100)
    namespace['utils'].get_pid_from_handle=lambda h:h+100


def launcher_namespace():
    tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
    handles, events = {11}, []
    old = SimpleNamespace(title='p1', window_handle=11, account_nick='account',
                          player_gid=101, is_ibao=False, is_running=lambda: 11 in handles,
                          close=AsyncMock(), process_id=111)
    def client_cls(handle):
        events.append(('client', handle))
        return SimpleNamespace(title='Wizard101', window_handle=handle,
            account_nick=None, player_gid=None, process_id=handle + 100,
            is_running=lambda: handle in handles, close=AsyncMock())
    def kill(handle):
        events.append(('kill', handle))
        handles.discard(handle)
    def launch(nick, path):
        handle = max([100, *handles]) + 1
        events.append(('launch', nick, handle))
        handles.add(handle)
        return handle
    ns = dict(Client=object, asyncio=asyncio, logger=Mock(), wizwalker=wizwalker,
        walker=SimpleNamespace(clients=[old], _managed_handles=[11], client_cls=client_cls),
        launched_account_map={11:'account'}, released_handles=set(), _hooking_in_progress=set(),
        window_config_applied=set(), _relaunching_clients={}, _account_launches={},
        _account_launch_generations={}, _account_launch_current={},
        _account_native_launch_lock=asyncio.Lock(), client_speeds={},
        client_resizing=False, client_resizing_manager=SimpleNamespace(teardown_client=AsyncMock()),
        _apply_account_window_config=AsyncMock(), _send_hooked_clients_update=Mock(),
        stop_questing_on_client_loss=AsyncMock(), _kill_process_by_handle=kill,
        _restart_always_on_tasks=Mock(), _restart_active_toggle_tasks=AsyncMock(),
        get_all_wizard_handles=lambda: set(handles),
        utils=SimpleNamespace(get_wiz_install=lambda:'fake-path', override_wiz_install_location=Mock(),
                              get_all_wizard_handles=lambda: set(handles),get_pid_from_handle=lambda h:h+100),
        launch_account_instance=launch, wizlaunch=SimpleNamespace(get_nickname_by_gid=lambda _: 'account'),
        settings=SimpleNamespace(get_setting=lambda _: None), gui_send_queue=SimpleNamespace(put=Mock()),
        xuanshu_gui=SimpleNamespace(GUICommand=lambda *args: args,
                                  GUICommandType=SimpleNamespace(ClearLaunchCheckboxes=1)),
        ibao_groups=SimpleNamespace(groups=[], stop=AsyncMock()),
        _events=events, _handles=handles, _old=old)
    async def initialize(client, **kwargs):
        events.append(('init', client.window_handle))
        client.account_nick = ns['launched_account_map'].get(client.window_handle)
    ns['_init_client_attrs'] = initialize
    names = {'_relaunch_managed_client', '_recover_ibao_client'}
    funcs = [n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))
             and (n.name in names or n.name.startswith(('_account_', '_wait_process_', '_rollback_', '_launch_reserved_')))]
    # Only whole helper definitions; nested rollback callbacks remain in their parent.
    parents = {id(child) for f in funcs for child in ast.walk(f) if child is not f}
    funcs = [f for f in funcs if id(f) not in parents]
    exec(compile(ast.Module(body=funcs,type_ignores=[]),'XuanShu.py','exec'),ns)
    # OS boundary only: these are the exact child PIDs of this fake launcher.
    ns['_account_owned_wizard_pids']=lambda: {h+100 for h in handles}
    ns['_account_terminate_spawned_pid']=lambda pid: handles.discard(pid-100)
    for command, name in [('LaunchInstance','initial'),('RelaunchClient','manual'),('UnhookClient','unhook')]:
        case = next(c for n in ast.walk(tree) if isinstance(n,ast.Match) for c in n.cases
                    if isinstance(c.pattern,ast.MatchValue) and isinstance(c.pattern.value,ast.Attribute)
                    and c.pattern.value.attr == command)
        fn = ast.parse('async def '+name+'():\n    for _once in (1,):\n        pass').body[0]
        fn.body[0].body = case.body
        exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])), 'XuanShu.py','exec'),ns)
    return ns


class ReplacementTransactionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        self.ns=launcher_namespace()
        self.stack.enter_context(patch('src.ibao_runtime.ibao_restart_trace',AsyncMock()))
        self.stack.enter_context(patch('src.ibao_runtime.prepare_restarted_client',AsyncMock()))

    async def relaunch(self):
        return await self.ns['_relaunch_managed_client'](11,'account','p1')

    def assert_rolled_back(self):
        self.assertEqual(self.ns['_handles'], set(), 'failed replacement left a live process')
        self.assertEqual(self.ns['walker'].clients, [])
        self.assertEqual(self.ns['walker']._managed_handles, [])
        self.assertEqual(self.ns['launched_account_map'], {})
        self.assertEqual(self.ns['_relaunching_clients'], {})
        self.assertEqual(self.ns['_hooking_in_progress'], set())
        self.assertEqual(self.ns['_account_launches'], {})

    async def test_r6_prepare_failure_kills_and_removes_every_new_handle(self):
        with patch('src.ibao_runtime.prepare_restarted_client',AsyncMock(side_effect=ValueError('prepare'))):
            with self.assertRaises(ValueError): await self.relaunch()
        self.assert_rolled_back()

    async def test_r7_hook_failure_kills_replacement(self):
        with patch('src.ibao_runtime.prepare_restarted_client',AsyncMock(side_effect=HookNotActive('Client'))):
            with self.assertRaises(HookNotActive): await self.relaunch()
        self.assert_rolled_back()

    async def test_r8_attribute_failure_kills_replacement(self):
        self.ns['_init_client_attrs']=AsyncMock(side_effect=ValueError('init'))
        with self.assertRaises(ValueError): await self.relaunch()
        self.assert_rolled_back()

    async def test_constructor_failure_is_inside_rollback_boundary(self):
        self.ns['walker'].client_cls=Mock(side_effect=ValueError('constructor'))
        with self.assertRaises(ValueError): await self.relaunch()
        self.assert_rolled_back()

    async def test_native_login_failure_after_spawn_must_not_leave_unreturned_handle(self):
        original=self.ns['launch_account_instance']
        def failed(*args):
            original(*args)
            raise RuntimeError('native login failed after spawn')
        self.ns['launch_account_instance']=failed
        with self.assertRaises(RuntimeError): await self.relaunch()
        self.assert_rolled_back()

    async def test_r12_stop_during_native_launch_joins_then_kills_returned_process(self):
        started, finish=threading.Event(), threading.Event()
        original=self.ns['launch_account_instance']
        def launch(*args):
            started.set(); finish.wait(3); return original(*args)
        self.ns['launch_account_instance']=launch
        task=asyncio.create_task(self.relaunch())
        try:
            for _ in range(200):
                if started.is_set(): break
                await REAL_SLEEP(.01)
            self.assertTrue(started.is_set())
            task.cancel(); await REAL_SLEEP(0)
            task.cancel(); await REAL_SLEEP(0)
            self.assertFalse(task.done())
        finally:
            finish.set()
            await asyncio.gather(task,return_exceptions=True)
        self.assert_rolled_back()

    async def test_r4_initial_cannot_launch_in_auto_recovery_map_gap(self):
        ready, proceed=threading.Event(), threading.Event()
        original=self.ns['launch_account_instance']; count=[0]
        def hold(*args):
            count[0]+=1
            if count[0]==1: ready.set(); proceed.wait(3)
            return original(*args)
        self.ns['launch_account_instance']=hold
        auto=asyncio.create_task(self.relaunch())
        try:
            for _ in range(200):
                if ready.is_set(): break
                await REAL_SLEEP(.01)
            self.assertTrue(ready.is_set())
            self.assertEqual(self.ns['launched_account_map'],{})
            self.ns['com']=SimpleNamespace(data=(['account'],'fake-path'))
            await self.ns['initial']()
        finally:
            proceed.set(); await auto
        self.assertEqual(sum(e[0]=='launch' for e in self.ns['_events']),1)

    async def test_r3_manual_during_auto_prepare_cannot_start_second_process(self):
        ready, proceed=asyncio.Event(), asyncio.Event()
        async def hold(_): ready.set(); await proceed.wait()
        with patch('src.ibao_runtime.prepare_restarted_client',hold):
            auto=asyncio.create_task(self.relaunch())
            await asyncio.wait_for(ready.wait(),2)
            self.ns['com']=SimpleNamespace(data=(11,'account'))
            try: await self.ns['manual']()
            finally: proceed.set(); await auto
        self.assertEqual(sum(e[0]=='launch' for e in self.ns['_events']),1)

    async def test_r5_two_auto_transactions_have_one_launch_owner(self):
        ready, proceed=asyncio.Event(), asyncio.Event()
        async def hold(_): ready.set(); await proceed.wait()
        with patch('src.ibao_runtime.prepare_restarted_client',hold):
            first=asyncio.create_task(self.relaunch())
            await asyncio.wait_for(ready.wait(),2)
            second=asyncio.create_task(self.relaunch())
            await REAL_SLEEP(.02)
            proceed.set()
            await asyncio.gather(first,second,return_exceptions=True)
        self.assertEqual(sum(e[0]=='launch' for e in self.ns['_events']),1)

    async def test_r1_r2_initial_repeated_account_in_same_command_launches_once(self):
        self.ns['_handles'].clear(); self.ns['walker'].clients.clear()
        self.ns['walker']._managed_handles.clear(); self.ns['launched_account_map'].clear()
        self.ns['com']=SimpleNamespace(data=(['account','account'],'fake-path'))
        await self.ns['initial']()
        await self.ns['initial']()
        self.assertEqual(sum(e[0]=='launch' for e in self.ns['_events']),1)

    async def test_manual_waits_for_old_window_disappearance_before_native_launch(self):
        original=self.ns['_kill_process_by_handle']; checks=[0]
        def delayed(handle):
            self.ns['_events'].append(('kill-request',handle))
        def handles():
            checks[0]+=1
            if checks[0]>=4: self.ns['_handles'].discard(11)
            return set(self.ns['_handles'])
        self.ns['_kill_process_by_handle']=delayed
        self.ns['get_all_wizard_handles']=handles
        self.ns['com']=SimpleNamespace(data=(11,'account'))
        await self.ns['manual']()
        self.assertNotIn(11,self.ns['_handles'])
        self.assertEqual(sum(e[0]=='launch' for e in self.ns['_events']),1)

    async def test_manual_or_initial_native_owner_rejects_auto(self):
        for entry in ('manual','initial'):
            with self.subTest(entry=entry):
                self.ns=launcher_namespace()
                if entry=='initial':
                    self.ns['_handles'].clear(); self.ns['walker'].clients.clear()
                    self.ns['launched_account_map'].clear()
                original=self.ns['launch_account_instance']
                ready,proceed=threading.Event(),threading.Event()
                def native(*args): ready.set(); proceed.wait(2); return original(*args)
                self.ns['launch_account_instance']=native
                self.ns['com']=SimpleNamespace(data=(11,'account') if entry=='manual' else (['account'],'fake-path'))
                task=asyncio.create_task(self.ns[entry]())
                try:
                    for _ in range(100):
                        if ready.is_set(): break
                        await REAL_SLEEP(.005)
                    self.assertTrue(ready.is_set())
                    with self.assertRaises(RuntimeError): await self.relaunch()
                finally: proceed.set(); await task
                self.assertEqual(sum(e[0]=='launch' for e in self.ns['_events']),1)

    async def test_r9_r10_six_real_transactions_rollback_before_retry_with_stable_identity(self):
        calls=[]; maxima=[]
        original=self.ns['launch_account_instance']
        def native(nick,path):
            calls.append(nick)
            self.assertEqual(self.ns['_handles'],set(),'retry began before full rollback')
            handle=original(nick,path); maxima.append(len(self.ns['_handles'])); return handle
        self.ns['launch_account_instance']=native
        count=[0]
        async def prepare(client):
            count[0]+=1
            if count[0]<=5:
                self.ns['_old'].account_nick='stale-wrong-account'
                raise ValueError('temporary preparation failure')
        context=dict(account='account',handle=11,title='p1',is_active=lambda:True)
        with patch('src.ibao_runtime.prepare_restarted_client',prepare):
            for _ in range(5):
                with self.assertRaises(ValueError):
                    await self.ns['_recover_ibao_client'](self.ns['_old'],{'_recovery_context':context})
                self.assert_rolled_back()
            new=await self.ns['_recover_ibao_client'](self.ns['_old'],{'_recovery_context':context})
        self.assertEqual(calls,['account']*6)
        self.assertEqual(max(maxima),1)
        self.assertEqual(new.account_nick,'account')
        self.assertEqual(len(self.ns['walker'].clients),1)
        self.assertEqual(self.ns['_account_launches'],{})
        self.assertEqual(self.ns['_events'].count(('kill',11)),1,
                         'a retired old handle must not be reused as current account state')

    async def test_unreadable_process_snapshot_does_not_lose_native_returned_handle(self):
        snapshot=self.ns['_account_owned_wizard_pids']; count=[0]
        def intermittent():
            count[0]+=1
            if count[0]==2: raise RuntimeError('transient process enumeration failure')
            return snapshot()
        self.ns['_account_owned_wizard_pids']=intermittent
        # Initial pre-snapshot succeeds, post-native snapshot fails once.
        with patch('src.ibao_runtime.prepare_restarted_client',AsyncMock(side_effect=ValueError('prepare'))):
            with self.assertRaises(ValueError): await self.relaunch()
        self.assert_rolled_back()

    async def test_optional_identity_refresh_is_not_a_second_takeover_gate(self):
        async def wrong(client, **kwargs): client.account_nick='another-account'
        self.ns['_init_client_attrs']=wrong
        new=await self.relaunch()
        self.assertIs(new,self.ns['walker'].clients[0])
        self.assertEqual(self.ns['_handles'],{101})
        self.assertEqual(self.ns['launched_account_map'],{101:'account'})

    async def test_callback_generation_is_not_a_replacement_health_gate(self):
        active=[True]
        async def prepare(client): active[0]=False
        context=dict(account='account',handle=11,title='p1',is_active=lambda:active[0])
        with patch('src.ibao_runtime.prepare_restarted_client',prepare):
            new=await self.ns['_recover_ibao_client'](self.ns['_old'],{'_recovery_context':context})
        self.assertIs(new,self.ns['walker'].clients[0])
        self.assertEqual(self.ns['_handles'],{101})

    async def test_child_process_snapshot_excludes_unrelated_and_preexisting_parent(self):
        tree=ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        fn=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='_account_owned_wizard_pids')
        ns={'os':os}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'XuanShu.py','exec'),ns)
        processes=[SimpleNamespace(th32ProcessID=os.getpid(),th32ParentProcessID=0,szExeFile='python.exe'),
            SimpleNamespace(th32ProcessID=501,th32ParentProcessID=os.getpid(),szExeFile=b'WizardGraphicalClient.exe'),
            SimpleNamespace(th32ProcessID=502,th32ParentProcessID=0,szExeFile='WizardGraphicalClient.exe'),
            SimpleNamespace(th32ProcessID=503,th32ParentProcessID=os.getpid(),szExeFile='unrelated.exe')]
        with patch('pymem.process.list_processes',return_value=iter(processes)):
            self.assertEqual(ns['_account_owned_wizard_pids'](),{501})

    async def test_review_old_kill_timeout_keeps_account_occupied_for_initial(self):
        for entry in ('auto','manual'):
            with self.subTest(entry=entry):
                self.ns=launcher_namespace()
                self.ns['_kill_process_by_handle']=Mock()
                self.ns['_wait_process_disappearance']=AsyncMock(return_value=False)
                if entry=='auto':
                    with self.assertRaises(RuntimeError): await self.relaunch()
                else:
                    self.ns['com']=SimpleNamespace(data=(11,'account'))
                    await self.ns['manual']()
                self.assertIn(11,self.ns['_handles'])
                self.ns['com']=SimpleNamespace(data=(['account'],'fake-path'))
                await self.ns['initial']()
                self.assertEqual(sum(e[0]=='launch' for e in self.ns['_events']),0,
                    'old process survived but Initial was allowed to duplicate the account')

    async def test_review_cancel_during_constructor_failure_rollback_releases_finished_reservation(self):
        ready,proceed=asyncio.Event(),asyncio.Event()
        rollback=self.ns['_rollback_account_replacement']
        async def hold(transaction): ready.set(); await proceed.wait(); await rollback(transaction)
        self.ns['_rollback_account_replacement']=hold
        self.ns['walker'].client_cls=Mock(side_effect=ValueError('constructor'))
        task=asyncio.create_task(self.relaunch())
        try:
            await asyncio.wait_for(ready.wait(),1)
            task.cancel(); await REAL_SLEEP(0); task.cancel(); await REAL_SLEEP(0)
        finally: proceed.set(); await asyncio.gather(task,return_exceptions=True)
        self.assert_rolled_back()

    async def test_background_global_refresh_cancel_does_not_revoke_finished_takeover(self):
        handoff=asyncio.Event()
        refresh_tasks=[]
        async def final_handoff():
            refresh_tasks.append(asyncio.current_task())
            handoff.set()
            await asyncio.Event().wait()
        self.ns['_restart_active_toggle_tasks']=final_handoff
        context=dict(account='account',handle=11,title='p1',is_active=lambda:True)
        new=await self.ns['_recover_ibao_client'](self.ns['_old'],{'_recovery_context':context})
        await asyncio.wait_for(handoff.wait(),1)
        refresh_tasks[0].cancel()
        await asyncio.gather(*refresh_tasks,return_exceptions=True)
        self.assertIs(new,self.ns['walker'].clients[0])
        self.assertEqual(self.ns['_handles'],{101})
        self.assertEqual(self.ns['_account_launches'],{})

    async def test_failed_new_process_kill_blocks_duplicate_via_existing_handle_map(self):
        ready,proceed=asyncio.Event(),asyncio.Event()
        terminate=self.ns['_account_terminate_spawned_pid']
        kill=self.ns['_kill_process_by_handle']
        wait=self.ns['_wait_process_disappearance']
        self.ns['_account_terminate_spawned_pid']=lambda pid:None
        self.ns['_kill_process_by_handle']=lambda h:kill(h) if h==11 else None
        async def delayed(handle,timeout=5):
            if handle!=11:
                ready.set(); await proceed.wait()
                terminate(handle+100)
            return await wait(handle,timeout)
        self.ns['_wait_process_disappearance']=delayed
        with patch('src.ibao_runtime.prepare_restarted_client',AsyncMock(side_effect=ValueError('prepare'))):
            task=asyncio.create_task(self.relaunch())
            try:
                await asyncio.wait_for(ready.wait(),1)
                self.assertEqual(self.ns['launched_account_map'],{101:'account'})
                self.ns['com']=SimpleNamespace(data=(['account'],'fake-path'))
                await self.ns['initial']()
                self.assertEqual(sum(e[0]=='launch' for e in self.ns['_events']),1)
                self.assertFalse(task.done())
            finally: proceed.set(); await asyncio.gather(task,return_exceptions=True)
        self.assert_rolled_back()

    async def test_review_initial_cancel_after_native_error_without_handle_rolls_back_spawned_pid(self):
        self.ns['_handles'].clear(); self.ns['walker'].clients.clear()
        self.ns['walker']._managed_handles.clear(); self.ns['launched_account_map'].clear()
        original=self.ns['launch_account_instance']
        def failed(*args): original(*args); raise ValueError('native login after spawn')
        self.ns['launch_account_instance']=failed
        ready,proceed=asyncio.Event(),asyncio.Event()
        capture=self.ns['_account_capture_native_pids']
        async def hold(*args): ready.set(); await proceed.wait(); await capture(*args)
        self.ns['_account_capture_native_pids']=hold
        self.ns['com']=SimpleNamespace(data=(['account'],'fake-path'))
        task=asyncio.create_task(self.ns['initial']())
        try:
            await asyncio.wait_for(ready.wait(),1)
            task.cancel(); await REAL_SLEEP(0)
        finally: proceed.set(); await asyncio.gather(task,return_exceptions=True)
        self.assert_rolled_back()

    async def test_review_unhook_is_intentional_stop_not_unexpected_death(self):
        started=asyncio.Event(); recovered=[]
        async def guard(run,_): return await run()
        async def worker(*_): started.set(); await asyncio.Event().wait()
        async def recover(*_): recovered.append('recovery'); await asyncio.Event().wait()
        manager=runtime.IbaoGroups(lambda:self.ns['walker'].clients,Mock(),worker,guard,recovery=recover)
        self.ns['ibao_groups']=manager
        self.ns['chat_monitor']=SimpleNamespace(sync=AsyncMock())
        with patch('src.ibao_runtime.ibao_restart_trace',AsyncMock()):
            manager.add(['p1'],{})
            try:
                await started.wait()
                self.ns['com']=SimpleNamespace(data=11)
                await self.ns['unhook']()
                await manager.remove_missing()
                for _ in range(4): await REAL_SLEEP(0)
                self.assertEqual(manager.groups,[], 'explicit Unhook must revoke production ownership')
                self.assertEqual(recovered,[])
                self.assertEqual(self.ns['_handles'],{11},'Unhook must leave the process for manual use')
            finally: await manager.stop()


class ProcessDeathTests(unittest.IsolatedAsyncioTestCase):
    async def scenario(self, *, remove_first=False, hook_error=False, failures=0):
        old=SimpleNamespace(title='p1',window_handle=11,account_nick='account',is_running=lambda:True)
        new=SimpleNamespace(title='p1',window_handle=22,account_nick='account',is_running=lambda:True)
        clients=[old]; events=[]; resumed=asyncio.Event(); calls=[]
        async def worker(client, settings):
            if client is new: resumed.set(); await asyncio.Event().wait()
            if hook_error: raise HookNotActive('Client')
            raise ibao_core.CharacterSelectionError('fixture')
        async def guard(run, _clients): return await run()
        async def recover(client, settings):
            calls.append(settings)
            if len(calls)<=failures: raise ValueError('temporary offline')
            clients[:]=[new]; return new
        manager=runtime.IbaoGroups(lambda:clients,Mock(),worker,guard,recovery=recover,inactivity_timeout=.001)
        async def fast_sleep(_): await REAL_SLEEP(0)
        with patch('src.ibao_runtime.asyncio.sleep',fast_sleep), patch('src.ibao_runtime.ibao_restart_trace',AsyncMock()):
            manager.add(['p1'],{})
            group=manager.groups[0]
            if remove_first:
                clients.clear(); old.is_running=lambda:False
                await manager.remove_missing()
            try:
                for _ in range(200):
                    if resumed.is_set() or not manager.groups: break
                    await REAL_SLEEP(.001)
                self.assertTrue(resumed.is_set(),'unexpected death permanently stopped production')
                self.assertFalse(group['stopping'])
                self.assertIs(group['workers'][0][0],new)
                self.assertEqual(len(calls),failures+1)
            finally: await manager.stop()

    async def test_r13_hook_not_active_recovers_and_resumes(self):
        await self.scenario(hook_error=True)

    async def test_r13_remove_missing_does_not_turn_death_into_user_stop(self):
        await self.scenario(remove_first=True)

    async def test_r9_five_failures_then_success_keeps_recovering(self):
        await self.scenario(failures=5)


class RestartEscapeTests(unittest.IsolatedAsyncioTestCase):
    async def prepare_frames(self, frames):
        cursor=[0]; keys=[]
        client=SimpleNamespace(title='p1',window_handle=11,root_window=object(),
            is_running=lambda:True,activate_hooks=AsyncMock(),
            is_loading=AsyncMock(side_effect=lambda:frames[cursor[0]].get('loading',False)),
            zone_name=AsyncMock(side_effect=lambda:frames[cursor[0]].get('zone','')),
            send_key=AsyncMock(side_effect=lambda *a:keys.append(cursor[0])))
        async def visible(root,path):
            frame=frames[cursor[0]]
            if path==ibao_core.playButton:
                if frame.get('unreadable'): raise ValueError('unreadable Play')
                return frame.get('play',False)
            return frame.get('shell',False) if path==runtime.CHARACTER_SELECT_WINDOW else False
        async def tick(_): cursor[0]=min(cursor[0]+1,len(frames)-1); await REAL_SLEEP(0)
        with patch('src.ibao_core.is_visible_by_path',visible),patch('src.ibao_runtime.asyncio.sleep',tick),patch('src.ibao_runtime.ibao_restart_trace',AsyncMock()):
            await asyncio.wait_for(runtime.prepare_restarted_client(client),1)
        return keys,client

    async def test_e1_ready_play_needs_no_escape(self):
        keys,_=await self.prepare_frames([{'play':True}]); self.assertEqual(keys,[])

    async def test_e2_title_is_repolled_after_each_escape(self):
        keys,_=await self.prepare_frames([{}, {}, {'play':True}]); self.assertEqual(keys,[0,1])

    async def test_e3_e4_shell_latches_intro_permission_off_even_if_it_disappears(self):
        keys,_=await self.prepare_frames([{}, {'shell':True}, {}, {'play':True}])
        self.assertEqual(keys,[0])

    async def test_e5_e6_loading_or_world_never_sends_intro_escape(self):
        keys,_=await self.prepare_frames([{'loading':True},{'zone':'World/Area'},{'play':True}])
        self.assertEqual(keys,[])

    async def test_unreadable_play_is_not_confirmed_absent(self):
        keys,_=await self.prepare_frames([{'unreadable':True},{'play':True}]); self.assertEqual(keys,[])

    async def test_e8_session_waits_for_play_when_character_shell_exists(self):
        keys=[]; polls=[0]
        client=SimpleNamespace(title='p1',root_window=object(),send_key=AsyncMock(side_effect=lambda *a:keys.append('ESC')))
        async def visible(root,path):
            if path==ibao_core.playButton: polls[0]+=1; return polls[0]>=3
            return path==runtime.CHARACTER_SELECT_WINDOW
        scope=dict(playButton=ibao_core.playButton,quitButton=ibao_core.quitButton,
            logOutConfirm=ibao_core.logOutConfirm,is_visible_by_path=visible,
            click_window_until_gone=AsyncMock(),azothFarmer=AsyncMock())
        async def fast(_): await REAL_SLEEP(0)
        with patch('src.ibao_runtime.create_session',return_value=scope),patch('src.ibao_runtime.asyncio.sleep',fast),patch('src.ibao_runtime.ibao_restart_trace',AsyncMock()):
            await runtime._run_client_session(client,{})
        self.assertEqual(keys,[])

    async def test_normal_start_after_consumed_restart_handoff_still_uses_world_logout(self):
        from tests.test_ibao_restart_entry import _InWorldClient, RestartEntryContractTests
        fixture=RestartEntryContractTests()
        client=_InWorldClient()
        client._ibao_intro_completed=True
        client._ibao_restart_handoff=False
        scope=fixture._worker_scope(client)
        async def tick(_): await REAL_SLEEP(0)
        with patch('src.ibao_runtime.create_session',return_value=scope),patch('src.ibao_runtime.asyncio.sleep',tick),patch('src.ibao_runtime.ibao_restart_trace',AsyncMock()):
            try: await asyncio.wait_for(runtime._run_client_session(client,{}),.1)
            except TimeoutError: self.fail('a consumed restart handoff disabled normal in-world logout')
        self.assertEqual(client.state['events'],['esc','click-quit','click-logout-confirm'])

    async def test_e7_prepared_play_then_transiently_absent_never_reopens_intro_escape(self):
        _,client=await self.prepare_frames([{'play':True}])
        keys=[]; polls=[False,False,True]
        client.send_key=AsyncMock(side_effect=lambda *a:keys.append('ESC'))
        async def visible(root,path):
            if path==ibao_core.playButton: return polls.pop(0) if len(polls)>1 else polls[0]
            return False  # Even the shell is temporarily unreadable/absent.
        scope=dict(playButton=ibao_core.playButton,quitButton=ibao_core.quitButton,
            logOutConfirm=ibao_core.logOutConfirm,is_visible_by_path=visible,
            click_window_until_gone=AsyncMock(),azothFarmer=AsyncMock())
        async def tick(_): await REAL_SLEEP(0)
        with patch('src.ibao_runtime.create_session',return_value=scope),patch('src.ibao_runtime.asyncio.sleep',tick),patch('src.ibao_runtime.ibao_restart_trace',AsyncMock()):
            await runtime._run_client_session(client,{})
        self.assertEqual(keys,[])
        self.assertFalse(client._ibao_restart_handoff)


class WorkerOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_w1_w2_old_worker_drains_before_new_worker_and_stale_input_is_revoked(self):
        old=SimpleNamespace(title='p1',account_nick='account',send_key=AsyncMock())
        new=SimpleNamespace(title='p1',account_nick='account')
        clients=[old]; entered=asyncio.Event(); drain=asyncio.Event(); resumed=asyncio.Event()
        events=[]; captured={}
        async def guard(run,_): return await run()
        async def worker(client,settings):
            if client is new: events.append('new-worker'); resumed.set(); await asyncio.Event().wait()
            captured.update(settings); entered.set()
            try: await asyncio.Event().wait()
            finally:
                events.append('old-cleaning'); await drain.wait(); events.append('old-exited')
        async def recover(*_): events.append('recovery'); clients[:]=[new]; return new
        manager=runtime.IbaoGroups(lambda:clients,Mock(),worker,guard,recovery=recover,inactivity_timeout=.01)
        with patch('src.ibao_runtime.ibao_restart_trace',AsyncMock()):
            manager.add(['p1'],{})
            try:
                await entered.wait(); await REAL_SLEEP(.03)
                self.assertEqual(events,['old-cleaning'])
                drain.set(); await asyncio.wait_for(resumed.wait(),1)
                proxy=runtime._TrackedClient(old,Mock(),captured['_is_active'])
                with self.assertRaises(asyncio.CancelledError): await proxy.send_key('ESC')
                self.assertEqual(old.send_key.await_count,0)
                self.assertEqual(events[:4],['old-cleaning','old-exited','recovery','new-worker'])
                before=manager.groups[0]['collected']; captured['_collection_callback']()
                self.assertEqual(manager.groups[0]['collected'],before)
            finally: drain.set(); await manager.stop()

    async def test_w3_duplicate_account_groups_never_have_two_active_workers(self):
        clients=[SimpleNamespace(title=t,account_nick='same') for t in ('p1','p2')]
        active=[0]; peak=[0]
        async def guard(run,_): return await run()
        async def worker(*_):
            active[0]+=1; peak[0]=max(peak[0],active[0])
            try: await asyncio.Event().wait()
            finally: active[0]-=1
        manager=runtime.IbaoGroups(lambda:clients,Mock(),worker,guard)
        with patch('src.ibao_runtime.ibao_restart_trace',AsyncMock()):
            manager.add(['p1','p2'],{})
            try:
                for _ in range(5): await REAL_SLEEP(0)
                self.assertEqual(peak[0],1)
            finally: await manager.stop()

    async def test_w4_stale_progress_callback_cannot_extend_new_generation(self):
        old=SimpleNamespace(title='p1',account_nick='account'); new=SimpleNamespace(title='p1',account_nick='account')
        clock=[0]; captured={}; resumed=asyncio.Event(); recovered_again=asyncio.Event(); calls=[0]
        async def guard(run,_): return await run()
        async def worker(client,settings):
            if client is old: captured.update(settings); raise ibao_core.CharacterSelectionError('first')
            resumed.set(); await asyncio.Event().wait()
        async def recover(*_):
            calls[0]+=1
            if calls[0]>1: recovered_again.set(); await asyncio.Event().wait()
            return new
        manager=runtime.IbaoGroups(lambda:[old,new],Mock(),worker,guard,recovery=recover,inactivity_timeout=.01,clock=lambda:clock[0])
        with patch('src.ibao_runtime.ibao_restart_trace',AsyncMock()):
            manager.add(['p1'],{})
            try:
                await resumed.wait(); clock[0]=10; captured['_progress_callback']()
                try: await asyncio.wait_for(recovered_again.wait(),.1)
                except TimeoutError: self.fail('stale progress callback kept the current generation alive')
            finally: await manager.stop()

    async def test_r11_stop_during_retry_backoff_never_starts_another_attempt(self):
        client=SimpleNamespace(title='p1',account_nick='account'); failed=asyncio.Event(); calls=[]
        async def guard(run,_): return await run()
        async def worker(*_): raise ibao_core.CharacterSelectionError('retry')
        async def recover(*_): calls.append('attempt'); failed.set(); raise ValueError('offline')
        manager=runtime.IbaoGroups(lambda:[client],Mock(),worker,guard,recovery=recover)
        with patch('src.ibao_runtime.ibao_restart_trace',AsyncMock()):
            manager.add(['p1'],{}); await failed.wait(); await manager.stop()
            await REAL_SLEEP(.01)
        self.assertEqual(calls,['attempt'])
        self.assertEqual(manager.groups,[])

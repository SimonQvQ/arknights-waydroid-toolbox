import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import tempfile
import subprocess

spec = importlib.util.spec_from_file_location('toolbox', Path(__file__).with_name('toolbox.py'))
toolbox = importlib.util.module_from_spec(spec)
spec.loader.exec_module(toolbox)


class ScheduleTests(unittest.TestCase):
    def test_midnight_preparation_and_disabled_slots(self):
        config = {'Current': 'Default', 'Timers': {'List': [
            {'Id': 0, 'IsEnabled': True, 'Hour': 0, 'Minute': 3},
            {'Id': 1, 'IsEnabled': False, 'Hour': 12, 'Minute': 0}]}}
        self.assertEqual(toolbox.slots(config)[0]['prepare'], '23:58')
        units = toolbox.scheduler_units(config, {'lead_minutes': 5}, '/usr/lib/toolbox.py')
        self.assertEqual(len(units), 4)
        self.assertIn('OnCalendar=*-*-* 23:58:00', units['arknights-toolbox-slot-0.timer'])
        self.assertIn('Persistent=false', units['arknights-toolbox-slot-0.timer'])

    def test_reminder_fires_one_minute_before_the_slot(self):
        config = {'Timers': {'List': [
            {'Id': 0, 'IsEnabled': True, 'Hour': 3, 'Minute': 0},
            {'Id': 1, 'IsEnabled': True, 'Hour': 0, 'Minute': 0}]}}
        units = toolbox.scheduler_units(config, {'lead_minutes': 5}, '/usr/lib/toolbox.py')
        # 03:00 reminds at 02:59; midnight must wrap instead of going negative.
        self.assertIn('OnCalendar=*-*-* 02:59:00', units['arknights-toolbox-slot-0-remind.timer'])
        self.assertIn('OnCalendar=*-*-* 23:59:00', units['arknights-toolbox-slot-1-remind.timer'])
        self.assertIn('remind --slot 1', units['arknights-toolbox-slot-1-remind.service'])

    def test_invalid_and_once_slots_never_become_daily_jobs(self):
        config = {'Timers': {'List': [
            {'Id': 0, 'IsEnabled': True, 'Hour': 99, 'Minute': 0},
            {'Id': 1, 'IsEnabled': None, 'Hour': 3, 'Minute': 0},
            {'Id': '../../other-service', 'IsEnabled': True, 'Hour': 3, 'Minute': 0}]}}
        self.assertEqual(toolbox.scheduler_units(config, {'lead_minutes': 5}), {})

    def test_custom_profile_preserved(self):
        config = {'Current': 'A', 'Timers': {'CustomConfig': True, 'List': [
            {'Id': 0, 'IsEnabled': True, 'Hour': 3, 'Minute': 0, 'Config': 'B'}]}}
        self.assertEqual(toolbox.slots(config)[0]['configuration'], 'B')


class LaunchTests(unittest.TestCase):
    def test_gui_gets_its_own_systemd_unit(self):
        with patch.object(toolbox, 'run', return_value='') as run:
            unit = toolbox.launch(['/bin/true'], 'waydroid', env={'DISPLAY': ':0'})
        command = run.call_args.args[0]
        self.assertEqual(command[0], 'systemd-run')
        self.assertIn('--unit=' + unit, command)
        self.assertIn('--property=ExitType=cgroup', command)
        self.assertIn('--setenv=DISPLAY=:0', command)

    def test_boot_property_timeout_is_retried(self):
        config = {'Current': 'Default', 'Timers': {'List': [
            {'Id': 0, 'IsEnabled': True, 'Hour': 3, 'Minute': 0}]}}
        ready = {'Session': 'RUNNING', 'Container': 'RUNNING',
                 'Wayland display': 'waydroid-mangoapp', 'IP address': '192.0.2.1'}
        prefs = {'lead_minutes': 5, 'wine_prefix': '/tmp/test-prefix', 'maa_dir': '/tmp/test-maa'}
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(toolbox, 'STATE', Path(directory)), \
                 patch.object(toolbox, 'settings', return_value=prefs), \
                 patch.object(toolbox, 'maa_config', return_value=(None, config)), \
                 patch.object(toolbox, 'waydroid_status', return_value=ready), \
                 patch.object(toolbox, 'game_start'), \
                 patch.object(toolbox, 'desktop_env', return_value={}), \
                 patch.object(toolbox, 'maa_running', return_value=True), \
                 patch.object(toolbox.time, 'sleep'), \
                 patch.object(toolbox, 'run', side_effect=[
                     subprocess.TimeoutExpired('waydroid', 5), '1', 'connected', 'device']) as run:
                result = toolbox.prepare(0)
        self.assertIn('准备好', result['message'])
        self.assertEqual(run.call_count, 4)


class RecoveryTests(unittest.TestCase):
    def _prefs(self):
        return {'lead_minutes': 5, 'wine_prefix': '/tmp/p', 'maa_dir': '/tmp/m',
                'sdk_dir': '/tmp/sdk'}

    def test_stale_session_on_another_display_is_recycled(self):
        """A session left on Niri's socket must be stopped, not abort prepare."""
        config = {'Current': 'Default', 'Timers': {'List': [
            {'Id': 0, 'IsEnabled': True, 'Hour': 3, 'Minute': 0}]}}
        stale = {'Session': 'RUNNING', 'Container': 'RUNNING',
                 'Wayland display': 'wayland-1', 'IP address': '192.0.2.1'}
        stopped_state = {'Session': 'STOPPED'}
        stopped = []

        def fake_status():
            # Stale until the stop lands; afterwards the HUD brings up mangoapp.
            return stopped_state if stopped else stale

        def fake_run(command, **kwargs):
            if command[:2] == ['waydroid', 'session'] and 'stop' in command:
                stopped.append(command)
                return ''
            if command[:3] == ['waydroid', 'prop', 'get']:
                return '1'
            if command[0] == 'waydroid' and command[1:] == ['status']:
                return ''
            return 'device'

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(toolbox, 'STATE', Path(directory)), \
                 patch.object(toolbox, 'settings', return_value=self._prefs()), \
                 patch.object(toolbox, 'maa_config', return_value=(None, config)), \
                 patch.object(toolbox, 'waydroid_status', side_effect=[
                     stale, stopped_state,
                     {'Session': 'RUNNING', 'Container': 'RUNNING',
                      'Wayland display': 'waydroid-mangoapp', 'IP address': '192.0.2.1'}] * 20), \
                 patch.object(toolbox, 'game_start'), \
                 patch.object(toolbox, 'desktop_env', return_value={}), \
                 patch.object(toolbox, 'maa_running', return_value=True), \
                 patch.object(toolbox.time, 'sleep'), \
                 patch.object(toolbox, 'run', side_effect=fake_run):
                result = toolbox.prepare(0)
        self.assertIn('准备好', result['message'])
        self.assertEqual(len(stopped), 1, 'prepare should stop the stale session once')

    def test_damaged_connection_profile_is_repaired(self):
        """MAA turns the bridge off during its guide; prepare must restore it."""
        config = {'Current': 'Default',
                  'Timers': {'List': [{'Id': 0, 'IsEnabled': True, 'Hour': 3, 'Minute': 0}]},
                  'Configurations': {'Default': {'Gui': {'ConnectSettings': {
                      'Address': '10.0.0.1:5555', 'Config': 'General', 'TouchMode': 'MiniTouch',
                      'Extras': {'MuMuEmulator12': {'IsEnabled': False,
                                                    'EnableBridgeConnection': False}}}}}}}
        ready = {'Session': 'RUNNING', 'Container': 'RUNNING',
                 'Wayland display': 'waydroid-mangoapp', 'IP address': '192.0.2.7'}
        written = {}

        def fake_run(command, **kwargs):
            if command[:3] == ['waydroid', 'prop', 'get']:
                return '1'
            if 'get-state' in command:
                return 'device'
            return ''

        def fake_atomic_json(path, value):
            # prepare() also writes last-prepare.json; only the profile matters.
            if str(path).endswith('g.json'):
                written.clear()
                written.update(value)

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(toolbox, 'STATE', Path(directory)), \
                 patch.object(toolbox, 'settings', return_value=self._prefs()), \
                 patch.object(toolbox, 'maa_config', return_value=(Path(directory) / 'g.json', config)), \
                 patch.object(toolbox, 'atomic_json', side_effect=fake_atomic_json), \
                 patch.object(toolbox, 'waydroid_status', return_value=ready), \
                 patch.object(toolbox, 'game_start'), \
                 patch.object(toolbox, 'desktop_env', return_value={}), \
                 patch.object(toolbox, 'maa_running', return_value=True), \
                 patch.object(toolbox.time, 'sleep'), \
                 patch.object(toolbox, 'run', side_effect=fake_run):
                toolbox.prepare(0)
        connection = written['Configurations']['Default']['Gui']['ConnectSettings']
        self.assertEqual(connection['Config'], 'MuMuEmulator12')
        self.assertEqual(connection['TouchMode'], 'MumuExtras')
        self.assertEqual(connection['Address'], '192.0.2.7:5555')
        self.assertTrue(connection['Extras']['MuMuEmulator12']['EnableBridgeConnection'])

    def test_close_maa_falls_back_to_kill_without_a_window(self):
        """A headless MAA (hidden behind a modal dialog) must still be closable."""
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            return ''

        with patch.object(toolbox, 'maa_running', side_effect=[True, True, False]), \
             patch.object(toolbox, 'niri_windows', return_value=[]), \
             patch.object(toolbox.time, 'sleep'), \
             patch.object(toolbox, 'run', side_effect=fake_run):
            toolbox.close_maa(timeout=0)
        self.assertTrue(any('pkill' in c for c in commands),
                        'close_maa must fall back to pkill when no window is listed')


class WatcherTests(unittest.TestCase):
    START = '[INF][TaskQueue] BeginRun: owner="TaskQueue" (called from LinkStartWithTasks)'
    END = '[INF][TaskQueue] Idle: true to true (called from ProcTaskChainMsg)'

    def test_drain_after_start_finishes_once(self):
        state, done = toolbox.scan_log([self.START, self.END], False)
        self.assertFalse(state)
        self.assertTrue(done)

    def test_end_without_a_start_is_ignored(self):
        """Replayed history or a stray idle line must not tear down a session."""
        state, done = toolbox.scan_log([self.END], False)
        self.assertFalse(state)
        self.assertFalse(done)

    def test_manual_stop_does_not_finish(self):
        state, done = toolbox.scan_log(
            [self.START, '[INF][TaskQueue] SetStopped: true'], False)
        self.assertTrue(state, 'a stopped run leaves the queue non-idle')
        self.assertFalse(done)

    def test_each_drain_reports_separately(self):
        state, done = toolbox.scan_log([self.START, self.END], False)
        self.assertTrue(done)
        state, done = toolbox.scan_log([self.START, self.END], state)
        self.assertTrue(done)


class ProcessTests(unittest.TestCase):
    def _fake_proc(self, root, pid, comm, argv):
        directory = root / str(pid)
        directory.mkdir(parents=True)
        (directory / 'comm').write_text(comm + '\n')
        (directory / 'cmdline').write_bytes(b'\0'.join(a.encode() for a in argv) + b'\0')

    def test_python_process_mentioning_maa_is_not_maa(self):
        """`pgrep -f MAA.dll` matched the invoking shell and lied; the scan must not.

        A plain python process whose argv names MAA.exe used to be enough to
        make the toolbox think MAA was already running.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fake_proc(root, 101, 'python3', ['/usr/bin/python3', 'toolbox.py', 'MAA.exe'])
            self._fake_proc(root, 102, 'bash', ['bash', '-c', 'pgrep -f MAA.exe'])
            with patch.object(toolbox.os, 'getuid', return_value=root.stat().st_uid):
                self.assertFalse(toolbox.maa_running(str(root)))

    def test_wine_bootstrap_counts_as_running(self):
        """On a cold prefix MAA runs inside wine before MAA.exe gets its own comm."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fake_proc(root, 201, 'wine',
                            ['/usr/lib/wine/x86_64-unix/wine',
                             '/home/x/.local/share/maa-wine/app/MAA.exe'])
            with patch.object(toolbox.os, 'getuid', return_value=root.stat().st_uid):
                self.assertTrue(toolbox.maa_running(str(root)))

    def test_native_maa_exe_comm_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fake_proc(root, 301, 'MAA.exe', ['MAA.exe'])
            with patch.object(toolbox.os, 'getuid', return_value=root.stat().st_uid):
                self.assertTrue(toolbox.maa_running(str(root)))


class DisplayStackTests(unittest.TestCase):
    """The HUD's gamescope+weston pair; a half-dead pair breaks the next start."""

    def _proc(self, root, pid, comm, argv):
        directory = root / str(pid)
        directory.mkdir(parents=True)
        (directory / 'comm').write_text(comm + '\n')
        (directory / 'cmdline').write_bytes(b'\0'.join(a.encode() for a in argv) + b'\0')

    def _scan(self, root):
        with patch.object(toolbox.os, 'getuid', return_value=root.stat().st_uid):
            return toolbox.stale_display_pids(str(root))

    def test_orphaned_gamescope_is_stale(self):
        """Weston died first; the surviving gamescope holds the start lock."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._proc(root, 10, 'gamescope-wl',
                       ['/usr/bin/gamescope', '--backend', 'sdl', '--mangoapp', '--', 'weston'])
            self.assertEqual(self._scan(root), [10])

    def test_orphaned_weston_is_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._proc(root, 20, 'weston',
                       ['/usr/bin/weston', '--backend=x11', '--socket=waydroid-mangoapp'])
            self.assertEqual(self._scan(root), [20])

    def test_healthy_pair_is_left_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._proc(root, 30, 'gamescope-wl', ['/usr/bin/gamescope', '--mangoapp'])
            self._proc(root, 31, 'weston',
                       ['/usr/bin/weston', '--socket=waydroid-mangoapp'])
            self.assertEqual(self._scan(root), [])

    def test_unrelated_weston_is_ignored(self):
        """A desktop Weston on another socket must never be killed."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._proc(root, 40, 'weston', ['/usr/bin/weston', '--socket=wayland-1'])
            self.assertEqual(self._scan(root), [])


if __name__ == '__main__':
    unittest.main()

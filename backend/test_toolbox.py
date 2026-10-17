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
        self.assertEqual(len(units), 2)
        self.assertIn('OnCalendar=*-*-* 23:58:00', units['arknights-toolbox-slot-0.timer'])
        self.assertIn('Persistent=false', units['arknights-toolbox-slot-0.timer'])

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


if __name__ == '__main__':
    unittest.main()

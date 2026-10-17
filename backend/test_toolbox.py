import importlib.util
from pathlib import Path
import unittest

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


if __name__ == '__main__':
    unittest.main()

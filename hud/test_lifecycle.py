"""Regression checks without running the launcher or changing live sessions."""
import ast
from pathlib import Path
import types
import unittest
from unittest.mock import patch

tree = ast.parse(Path(__file__).with_name('arknights-waydroid-hud').read_text())
tree.body = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef))]
launcher = types.ModuleType('hud_functions')
exec(compile(tree, '<hud-functions>', 'exec'), launcher.__dict__)


class LifecycleTests(unittest.TestCase):
    def test_weston_is_found_below_gamescopereaper(self):
        args = {1: ['/usr/bin/gamescope'],
                2: ['gamescopereaper', '--', '/usr/bin/weston', '--socket=waydroid-mangoapp'],
                3: ['/usr/bin/weston', '--socket=waydroid-mangoapp']}
        children = {1: '2', 2: '3', 3: ''}
        def read(path):
            return children[int(path.parts[2])]
        with patch.object(launcher, 'process_args', side_effect=lambda pid: args.get(pid, [])), \
             patch.object(Path, 'iterdir', lambda path: iter([path / 'worker-thread'])), \
             patch.object(Path, 'read_text', read):
            self.assertTrue(launcher.has_weston(1))
            args.pop(3)
            self.assertFalse(launcher.has_weston(1))


if __name__ == '__main__':
    unittest.main()

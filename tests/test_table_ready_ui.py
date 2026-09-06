from pathlib import Path
import shutil
import subprocess
import unittest


class TableReadyUiTests(unittest.TestCase):
    def test_offline_real_dom_and_session_save_contracts(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required for offline UI verification.')
        result = subprocess.run(
            [node, str(Path(__file__).with_name('table_ready_ui.test.js'))],
            capture_output=True, text=True, timeout=25,
        )
        if result.returncode == 77:
            self.skipTest('Optional jsdom missing; set NODE_PATH to an existing installation.')
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('checks passed', result.stdout)

from pathlib import Path
import shutil
import subprocess
import unittest


class PlayerCompanionUiTests(unittest.TestCase):
    def test_actual_browser_module_with_offline_dom_and_fetch(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is required for the offline browser module test.")
        result = subprocess.run(
            [node, str(Path(__file__).with_name("player_companion_ui.test.js"))],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("checks passed", result.stdout)

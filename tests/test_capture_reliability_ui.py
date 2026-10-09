import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CaptureReliabilityUiTests(unittest.TestCase):
    def test_controller_contract(self):
        completed = subprocess.run(
            ["node", str(ROOT / "tests" / "capture_reliability_ui.test.js")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env={**os.environ, "NO_COLOR": "1"},
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)

    def test_page_has_persistent_alarm_and_recovery_contracts(self):
        html = (ROOT / "dnd-audio.html").read_text(encoding="utf-8")
        for text in (
            "RECORDING INTERRUPTED",
            "AUDIO CAPTURE STOPPED",
            "captureGlobalWarning",
            "RECOVER RECORDING",
            "ACKNOWLEDGE ALARM",
            "SLEEP PROTECTION UNAVAILABLE",
            "FINISH FINALIZATION",
            "beforeunload",
            "confirmPartialCapture",
            "THIS SESSION CONTAINS A RECORDING GAP",
            "WHAT DID I MISS? CANNOT USE CURRENT AUDIO",
        ):
            self.assertIn(text, html)

    def test_offline_queue_and_recorder_recovery(self):
        completed = subprocess.run(
            ['node', str(ROOT / 'tests' / 'recording_storage_ui.test.js')],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()

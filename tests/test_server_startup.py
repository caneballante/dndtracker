import subprocess
import sys
import unittest
from pathlib import Path


class ServerStartupTests(unittest.TestCase):
    def test_server_imports_with_isolated_python_path(self):
        repo_root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                "import runpy; runpy.run_path('server.py', run_name='startup_import_probe')",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[1]


class RuntimePathTest(unittest.TestCase):
    def test_imported_paths_are_project_relative_and_import_is_inert(self):
        source = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import app
import fld.paths as paths
root = Path(sys.argv[1]).resolve()
assert paths.PROJECT_DIR == root
assert paths.DOWNLOAD_DIR == root / 'downloads'
assert paths.STATE_PATH == root / 'queue.sqlite3'
assert paths.ICON_PATH == root / 'assets' / 'fld.ico'
assert callable(app.main)
assert list(Path.cwd().iterdir()) == []
"""
        with tempfile.TemporaryDirectory() as unrelated:
            result = subprocess.run([sys.executable, "-c", source, str(PROJECT)], cwd=unrelated,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(Path(unrelated).iterdir()), [])

    def test_simulated_frozen_paths_split_executable_data_from_resources(self):
        source = """
import runpy
import sys
from pathlib import Path
sys.frozen = True
sys.executable = sys.argv[1]
root = Path(sys.argv[2]).resolve()
paths = runpy.run_path(str(root / 'fld' / 'paths.py'))
exe_dir = Path(sys.executable).resolve().parent
assert paths['FROZEN'] is True
assert paths['PROJECT_DIR'] == root
assert paths['RESOURCE_DIR'] == root
assert paths['DATA_DIR'] == exe_dir
assert paths['DOWNLOAD_DIR'] == exe_dir / 'downloads'
assert paths['STATE_PATH'] == exe_dir / 'queue.sqlite3'
assert paths['LOCK_PATH'] == exe_dir / 'queue.lock'
assert paths['ICON_PATH'] == root / 'assets' / 'fld.ico'
assert not (exe_dir / 'queue.sqlite3').exists()
assert not (exe_dir / 'downloads').exists()
"""
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            unrelated = base / "unrelated"
            unrelated.mkdir()
            executable = base / "portable" / "FLD.exe"
            executable.parent.mkdir()
            result = subprocess.run(
                [sys.executable, "-c", source, str(executable), str(PROJECT)],
                cwd=unrelated, capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(unrelated.iterdir()), [])
            self.assertEqual(list(executable.parent.iterdir()), [])


if __name__ == "__main__":
    unittest.main()

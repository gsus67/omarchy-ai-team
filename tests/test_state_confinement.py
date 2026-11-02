import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import atomic_files
import state_confinement


class StateConfinementTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.outer = Path(temp.name)
        self.root = self.outer / 'fixture'
        self.root.mkdir()
        # The spies go in before the guard: if the guard ever vanishes (python -O drops assert), nothing real is written.
        mkdir = patch.object(Path, 'mkdir', autospec=True); self.mkdir = mkdir.start(); self.addCleanup(mkdir.stop)
        write = patch.object(atomic_files, 'atomic_write'); self.write = write.start(); self.addCleanup(write.stop)
        state_confinement.guard_writes(self, self.root)

    def test_rejects_outside_before_io(self):
        outside = self.outer / 'elsewhere'
        with self.subTest('mkdir'):
            with self.assertRaisesRegex(AssertionError, 'mkdir outside the fixture'):
                (outside / 'dir').mkdir()
            self.mkdir.assert_not_called()
        with self.subTest('atomic_write'):
            with self.assertRaisesRegex(AssertionError, 'write outside the fixture'):
                atomic_files.atomic_write(outside / 'file.json', 'content')
            self.write.assert_not_called()

    def test_allows_inside_fixture(self):
        inside = self.root / 'state'
        inside.mkdir(parents=True, exist_ok=True)
        self.mkdir.assert_called_once_with(inside, parents=True, exist_ok=True)
        atomic_files.atomic_write(inside / 'file.json', 'content')
        self.write.assert_called_once_with(inside / 'file.json', 'content')


if __name__ == '__main__':
    unittest.main()

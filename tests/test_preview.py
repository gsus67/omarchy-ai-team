"""tools/preview.py smoke wrapper: the common PASS/FAIL contract in both modes, and the window-only exit."""
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_preview():
    spec = importlib.util.spec_from_file_location('preview_under_test', ROOT / 'tools/preview.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PreviewSmokeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name)
        self.omarchy = self.temp / 'omarchy'
        for name in ('Commons', 'Ui'):
            (self.omarchy / 'shell' / name).mkdir(parents=True)
        (self.omarchy / 'shell/Ui/qmldir').write_text('')
        self.preview = load_preview()
        self.addCleanup(os.chdir, os.getcwd())
        saved_path = list(sys.path)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), saved_path))

    def run_smoke(self, *, window_smoke, returncode=0, stdout='HARNESS_SMOKE_PASS\n', stderr=''):
        """``preview.main()`` with only the Quickshell/git boundary replaced; returns ``(code, stderr)``."""
        sent = []

        def fake_run(argv, *args, **kwargs):
            if argv[0] == 'quickshell':
                sent.append(kwargs.get('env'))
                return subprocess.CompletedProcess(argv, returncode, stdout, stderr)
            if argv[:2] == ['git', 'rev-parse']:
                return subprocess.CompletedProcess(argv, 1, '', '')
            raise AssertionError('unexpected command %r' % (argv,))

        argv = ['preview.py', '--smoke', '--live', '--artifacts', str(self.temp / 'artifacts')]
        if window_smoke:
            argv.append('--window-smoke')
        with patch.object(sys, 'argv', argv), patch.dict(os.environ, {'OMARCHY_PATH': str(self.omarchy)}), \
                patch.object(self.preview.subprocess, 'run', fake_run), \
                patch('sys.stderr', new_callable=io.StringIO) as err, patch('sys.stdout', new_callable=io.StringIO):
            code = self.preview.main()
        self.sent_env = sent[0] if sent else None
        return code, err.getvalue()

    def test_window_smoke_passes_without_full_flow_artifacts(self):
        code, err = self.run_smoke(window_smoke=True)
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(self.sent_env.get('AI_HARNESS_WINDOW_SMOKE'), '1')

    def test_common_smoke_failures_are_rejected_in_both_modes(self):
        cases = {'exit code with PASS': dict(returncode=1), 'no PASS': dict(stdout=''),
                 'FAIL with colon': dict(stdout='HARNESS_SMOKE_PASS\nHARNESS_SMOKE_FAIL: x\n'),
                 'FAIL without colon in stderr': dict(stderr='HARNESS_SMOKE_FAIL\n')}
        for window_smoke in (True, False):
            for name, options in cases.items():
                with self.subTest(window_smoke=window_smoke, case=name):
                    self.assertEqual(self.run_smoke(window_smoke=window_smoke, **options)[0], 1)

    def test_full_smoke_still_requires_its_artifacts(self):
        code, err = self.run_smoke(window_smoke=False)
        self.assertEqual(code, 1)
        self.assertIn('CLI install/update did not preserve both versions', err)


if __name__ == '__main__':
    unittest.main()

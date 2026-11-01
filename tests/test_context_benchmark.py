"""The synthetic benchmark is arithmetic on fixtures: hand-checked numbers, no inference, no network."""
import ast
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'tools/context_benchmark.py'
OFF = dict(fresh_worker_per_subtask=False, bounded_reads=False, task_plan_with_pointers=False, stable_prompt_prefix=False)
ON = dict.fromkeys(OFF, True)


def load():
    spec = importlib.util.spec_from_file_location('context_benchmark', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture(**changes):
    base = {'name': 'tiny', 'tokens_per_line': 10, 'prefix_tokens': 1000, 'chief_prefix_tokens': 500,
            'turn_tokens': 100, 'brief_tokens': 50, 'chief_turn_tokens': 60, 'report_tokens': 40,
            'files': {'a.py': 1000, 'b.py': 200},
            'subtasks': [{'id': 's1', 'turns': 2, 'reads': []}, {'id': 's2', 'turns': 2, 'reads': []}]}
    return {**base, **changes}


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.bench = load()

    def totals(self, result):
        return (result['total_input'], result['cached_read'], result['new_tokens'], result['rereads'])

    def test_single_session_re_reads_the_growing_context_every_turn(self):
        # turns: 1000/0, 1100/1000, 1200/1100, 1300/1200 (input/cached)
        cold = self.bench.simulate(fixture(), OFF, cache='cold')
        self.assertEqual(self.totals(cold), (4600, 3300, 1300, 0))
        # Legacy prefix starts with the instance id: not shareable even when the cache is warm.
        self.assertEqual(self.totals(self.bench.simulate(fixture(), OFF, cache='warm')), (4600, 3300, 1300, 0))

    def test_fresh_workers_chief_and_stable_prefix_hand_computed(self):
        only_fresh = {**OFF, 'fresh_worker_per_subtask': True, 'stable_prompt_prefix': True}
        # worker1 2200/1050, worker2 2200/2050 (prefix shared), chief 1100/500
        self.assertEqual(self.totals(self.bench.simulate(fixture(), only_fresh, cache='cold')), (5500, 3600, 1900, 0))
        # Warm cache also hits the first worker prefix (1000) and the chief prefix (500).
        self.assertEqual(self.totals(self.bench.simulate(fixture(), only_fresh, cache='warm')), (5500, 5100, 400, 0))
        unstable = {**OFF, 'fresh_worker_per_subtask': True}
        self.assertEqual(self.totals(self.bench.simulate(fixture(), unstable, cache='cold'))[:2], (5500, 2600))

    def test_a_fresh_worker_does_not_pay_for_earlier_subtasks_but_a_session_does(self):
        long = fixture(subtasks=[{'id': 's1', 'turns': 20, 'reads': []}, {'id': 's2', 'turns': 2, 'reads': []}])
        short = fixture(subtasks=[{'id': 's1', 'turns': 1, 'reads': []}, {'id': 's2', 'turns': 2, 'reads': []}])
        fresh = {**OFF, 'fresh_worker_per_subtask': True}
        def second(result):
            return result['by_subtask']['s2']
        self.assertEqual(second(self.bench.simulate(long, fresh)), second(self.bench.simulate(short, fresh)))
        self.assertNotEqual(second(self.bench.simulate(long, OFF)), second(self.bench.simulate(short, OFF)))

    def test_bounded_reads_load_a_window_not_the_whole_file(self):
        reads = [{'file': 'a.py', 'needed_lines': 30}]
        data = fixture(subtasks=[{'id': 's1', 'turns': 1, 'reads': reads}])
        whole = self.bench.simulate(data, OFF)
        window = self.bench.simulate(data, {**OFF, 'bounded_reads': True}, window=120)
        self.assertEqual(whole['read_tokens'], 10000)
        self.assertEqual(window['read_tokens'], 1200)
        small = self.bench.simulate(fixture(subtasks=[{'id': 's1', 'turns': 1, 'reads': [{'file': 'b.py', 'needed_lines': 500}]}]),
                                    {**OFF, 'bounded_reads': True}, window=120)
        self.assertEqual(small['read_tokens'], 2000)  # clipped to the 200-line file

    def test_pointers_skip_unchanged_rereads_but_changed_files_are_read_again(self):
        def data(changed):
            return fixture(subtasks=[{'id': 's1', 'turns': 1, 'reads': [{'file': 'b.py', 'needed_lines': 50}]},
                                     {'id': 's2', 'turns': 1, 'reads': [{'file': 'b.py', 'needed_lines': 50, 'changed': changed}]}])
        self.assertEqual(self.bench.simulate(data(False), OFF)['rereads'], 1)
        pointers = {**OFF, 'task_plan_with_pointers': True}
        self.assertEqual(self.bench.simulate(data(False), pointers)['rereads'], 0)
        self.assertEqual(self.bench.simulate(data(True), pointers)['rereads'], 1)

    def test_default_fixture_improves_total_input_and_ablation_names_each_setting(self):
        report = self.bench.compare(self.bench.DEFAULT_FIXTURE, cache='cold')
        self.assertLess(report['after']['total_input'], report['before']['total_input'])
        self.assertLess(report['after']['rereads'], report['before']['rereads'])
        self.assertEqual(sorted(report['ablation']), sorted(OFF))
        again = self.bench.compare(self.bench.DEFAULT_FIXTURE, cache='cold')
        self.assertEqual(json.dumps(report, sort_keys=True), json.dumps(again, sort_keys=True))

    def test_markdown_table_has_the_four_metrics_deltas_and_the_synthetic_warning(self):
        text = self.bench.markdown(self.bench.compare(self.bench.DEFAULT_FIXTURE, cache='cold'), 'cold')
        for column in ('Entrada total', 'Lectura de caché', 'Tokens nuevos', 'Relecturas', 'ANTES', 'DESPUÉS', 'Δ'):
            self.assertIn(column, text)
        self.assertIn('sintético', text.lower())
        self.assertIn('no acredita', text.lower())
        self.assertEqual(sum(1 for line in text.splitlines() if line.startswith('|')), sum(1 for line in text.splitlines() if line.count('|') >= 5))

    def test_cli_prints_markdown_or_json_reads_a_fixture_and_rejects_invalid_ones_without_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'f.json'
            path.write_text(json.dumps(fixture()))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(self.bench.main(['--fixture', str(path), '--cache', 'both']), 0)
            self.assertIn('cold', out.getvalue().lower())
            self.assertIn('warm', out.getvalue().lower())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(self.bench.main(['--format', 'json']), 0)
            self.assertIn('before', json.loads(out.getvalue())['cold'])
            path.write_text('{"name": 1}')
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(self.bench.main(['--fixture', str(path)]), 1)
            self.assertTrue(json.loads(out.getvalue())['error'])
            self.assertEqual([p.name for p in Path(temp).iterdir()], ['f.json'])

    def test_script_cannot_run_models_or_reach_the_network(self):
        tree = ast.parse(SCRIPT.read_text())
        imported = {alias.name.split('.')[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        imported |= {node.module.split('.')[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
        self.assertFalse(imported & {'socket', 'subprocess', 'urllib', 'http', 'requests', 'random', 'time', 'datetime', 'os'})


if __name__ == '__main__':
    unittest.main()

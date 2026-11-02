import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import price_table


class PriceTableTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        patcher = patch.dict(os.environ, {'HOME': str(self.home)}); patcher.start(); self.addCleanup(patcher.stop)
        self.file = self.home / '.config/omarchy/agents/prices.json'

    def test_rows_list_every_model_and_never_create_the_file(self):
        rows = price_table.rows()
        sonnet = next(row for row in rows if row['model'] == 'claude-sonnet-5-5')
        self.assertEqual((sonnet['input'], sonnet['cacheWrite']), (2.0, 2.5))
        self.assertIsNone(next(row for row in rows if row['model'] == 'gpt-6.1-sol')['input'])
        self.assertFalse(self.file.exists())

    def test_missing_counts_models_with_any_empty_price(self):
        self.assertEqual(price_table.missing(), 7)

    def test_set_value_saves_one_price_and_keeps_the_rest(self):
        price_table.set_value('gpt-6.1-sol', 'input', '2.5')
        saved = json.loads(self.file.read_text())
        self.assertEqual(saved['gpt-6.1-sol']['input'], 2.5)
        self.assertEqual(saved['claude-opus-5-5']['input'], 4.0)
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o600)

    def test_a_damaged_file_is_never_overwritten(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_text('{not json')
        with self.assertRaises(ValueError):
            price_table.set_value('gpt-6.1-sol', 'input', '2')
        with self.assertRaises(ValueError):
            price_table.apply_proposals({'gpt-6.1-sol': {'input': 2}})
        self.assertEqual(self.file.read_text(), '{not json')

    def test_a_bad_cell_does_not_make_autofill_replace_its_neighbours(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_text('{"gpt-6.1-sol": {"input": 9, "output": "oops", "cacheRead": null, "cacheWrite": null}}')
        price_table.apply_proposals({'gpt-6.1-sol': {'input': 2, 'output': 3}})
        saved = json.loads(self.file.read_text())['gpt-6.1-sol']
        self.assertEqual((saved['input'], saved['output']), (9, 3))

    def test_an_empty_text_clears_a_price(self):
        price_table.set_value('claude-opus-5-5', 'input', '')
        self.assertIsNone(json.loads(self.file.read_text())['claude-opus-5-5']['input'])

    def test_invalid_values_and_unknown_names_are_rejected_without_touching_the_file(self):
        for model, field, text in (('gpt-6.1-sol', 'input', 'lots'), ('gpt-6.1-sol', 'input', '-1'), ('gpt-6.1-sol', 'input', 'nan'),
                                   ('gpt-6.1-sol', 'input', '1e9'), ('gpt-6.1-sol', 'speed', '1'), ('no-such-model', 'input', '1')):
            with self.assertRaises(ValueError, msg=(model, field, text)):
                price_table.set_value(model, field, text)
        self.assertFalse(self.file.exists())

    def test_autofill_only_fills_empty_prices_and_never_overwrites_the_users(self):
        price_table.set_value('gpt-6.1-sol', 'input', '9')
        filled = price_table.apply_proposals({
            'gpt-6.1-sol': {'input': 1.0, 'output': 5.0, 'cacheRead': None, 'cacheWrite': 'x', 'source': 'https://example.test/p'},
            'claude-opus-5-5': {'input': 99.0, 'source': 'https://example.test/p'},
            'unknown-model': {'input': 1.0}})
        saved = json.loads(self.file.read_text())
        self.assertEqual(saved['gpt-6.1-sol']['input'], 9.0)
        self.assertEqual(saved['gpt-6.1-sol']['output'], 5.0)
        self.assertIsNone(saved['gpt-6.1-sol']['cacheRead'])
        self.assertEqual(saved['claude-opus-5-5']['input'], 4.0)
        self.assertEqual(filled, [{'model': 'gpt-6.1-sol', 'field': 'output', 'value': 5.0, 'source': 'https://example.test/p'}])

    def test_autofill_with_nothing_usable_writes_nothing(self):
        self.assertEqual(price_table.apply_proposals({'gpt-6.1-sol': {'input': None}}), [])
        self.assertFalse(self.file.exists())


if __name__ == '__main__':
    unittest.main()

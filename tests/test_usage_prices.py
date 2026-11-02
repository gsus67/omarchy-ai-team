import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/usage'))
import prices

KINDS = {'input': 1_000_000, 'output': 500_000, 'cacheRead': 2_000_000, 'cacheWrite': 1_000_000}


class PricesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'agents' / 'prices.json'

    def test_default_prices_match_the_spec(self):
        expected = {
            'claude-opus-5-5': (4.00, 20.00, 0.20, 5.00),
            'claude-sonnet-5-5': (2.00, 10.00, 0.20, 2.50),
            'claude-haiku-5-5': (0.10, 0.50, 0.01, 0.125),
            'claude-fable-5-1': (10.00, 50.00, 0.25, 12.50),
        }
        for model, values in expected.items():
            row = prices.DEFAULT_PRICES[model]
            self.assertEqual((row['input'], row['output'], row['cacheRead'], row['cacheWrite']), values, model)
        self.assertEqual(prices.DEFAULT_PRICES['gpt-6.1-sol'],
                         {'input': None, 'output': None, 'cacheRead': None, 'cacheWrite': None})

    def test_cost_is_tokens_times_price_per_million(self):
        self.assertAlmostEqual(prices.cost_usd(prices.DEFAULT_PRICES, 'claude-sonnet-5-5', KINDS), 2.00 + 5.00 + 0.40 + 2.50)

    def test_unknown_or_null_priced_model_has_no_cost(self):
        for model in ('claude-from-the-future', 'gpt-6.1-sol', '<synthetic>'):
            self.assertIsNone(prices.cost_usd(prices.DEFAULT_PRICES, model, KINDS), model)

    def test_unpriced_model_stays_unpriced_even_with_zero_tokens(self):
        zero = {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0}
        self.assertIsNone(prices.cost_usd(prices.DEFAULT_PRICES, 'gpt-6.1-sol', zero))
        self.assertEqual(prices.cost_usd(prices.DEFAULT_PRICES, 'claude-opus-5-5', zero), 0.0)

    def test_load_creates_the_file_with_defaults_when_missing(self):
        self.assertEqual(prices.load_prices(self.path), prices.DEFAULT_PRICES)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(json.loads(self.path.read_text()), prices.DEFAULT_PRICES)

    def test_loading_without_creating_leaves_no_file_behind(self):
        self.assertEqual(prices.load_prices(self.path, create=False), prices.DEFAULT_PRICES)
        self.assertFalse(self.path.exists())

    def test_load_keeps_a_corrupt_file_untouched_and_falls_back_to_defaults(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{not json')
        self.assertEqual(prices.load_prices(self.path), prices.DEFAULT_PRICES)
        self.assertEqual(self.path.read_text(), '{not json')

    def test_invalid_user_prices_are_ignored_and_never_crash_the_cost(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"claude-opus-5-5": {"input": "oops", "output": 1, "cacheRead": 1, "cacheWrite": 1},'
                             ' "claude-sonnet-5-5": {"input": -1, "output": 1, "cacheRead": 1, "cacheWrite": 1},'
                             ' "claude-haiku-5-5": {"input": NaN, "output": 1, "cacheRead": 1, "cacheWrite": 1},'
                             ' "claude-fable-5-1": {"input": true, "output": 1, "cacheRead": 1, "cacheWrite": 1}}')
        loaded = prices.load_prices(self.path)
        for model in ('claude-opus-5-5', 'claude-sonnet-5-5', 'claude-haiku-5-5', 'claude-fable-5-1'):
            self.assertEqual(loaded[model]['input'], prices.DEFAULT_PRICES[model]['input'], model)  # the bad cell falls back
            self.assertEqual({loaded[model][kind] for kind in ('output', 'cacheRead', 'cacheWrite')}, {1}, model)  # its neighbours stay
            self.assertIsInstance(prices.cost_usd(loaded, model, KINDS), float)

    def test_user_prices_override_defaults(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({'gpt-6.1-sol': {'input': 1.0, 'output': 2.0, 'cacheRead': 0.1, 'cacheWrite': 1.0}}))
        loaded = prices.load_prices(self.path)
        self.assertEqual(loaded['claude-opus-5-5'], prices.DEFAULT_PRICES['claude-opus-5-5'])
        self.assertAlmostEqual(prices.cost_usd(loaded, 'gpt-6.1-sol', KINDS), 1.0 + 1.0 + 0.2 + 1.0)


if __name__ == '__main__':
    unittest.main()

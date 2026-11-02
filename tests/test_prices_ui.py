import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PricesUiTests(unittest.TestCase):
    def test_page_is_mounted_and_goes_back_to_settings(self):
        source = (ROOT / 'HarnessContent.qml').read_text()
        self.assertRegex(source, re.compile(r'PricesPage \{.*?visible: root\.page === "prices".*?root\.snapshot\.prices', re.S))
        self.assertIn('root.page === "prices"', re.search(r'function goBack\(\) \{.*?\n  \}', source, re.S)[0])

    def test_settings_row_shows_how_many_prices_are_missing(self):
        source = (ROOT / 'SettingsPage.qml').read_text()
        self.assertIn('pageRequested("prices")', source)
        self.assertIn('root.prices.missing', source)

    def test_page_sends_the_backend_commands_and_wraps(self):
        page = (ROOT / 'PricesPage.qml').read_text()
        self.assertIn('["price-autofill"]', page)
        self.assertIn('["price-save", JSON.stringify(changes)]', page)
        self.assertEqual(page.count('Ui.Button {'), 2)  # autofill + one save for the whole page
        self.assertIn('Flow {', page)
        self.assertIn('Ui.PanelSeparator', page)


if __name__ == '__main__':
    unittest.main()

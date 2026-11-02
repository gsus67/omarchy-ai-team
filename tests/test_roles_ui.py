import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RolesUiTests(unittest.TestCase):
    def test_main_screen_has_a_total_row_that_opens_the_roles_page(self):
        source = (ROOT / 'HarnessContent.qml').read_text()
        row = re.search(r'objectName: "usage-total".*?\n        \}', source, re.S)[0]
        self.assertIn('root.page = "roles"', row)
        self.assertRegex(source, re.compile(r'RolesPage \{.*?visible: root\.page === "roles".*?root\.snapshot\.roleUsage', re.S))

    def test_share_meters_never_turn_urgent(self):
        self.assertIn('warn: false', (ROOT / 'RolesPage.qml').read_text())
        self.assertIn('root.warn &&', (ROOT / 'UsageBar.qml').read_text())


if __name__ == '__main__':
    unittest.main()

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

    def test_the_total_is_measured_since_the_common_start_not_over_30_days(self):
        source = (ROOT / 'RolesPage.qml').read_text()
        self.assertNotIn('30 días', source)
        self.assertIn('root.tr("Medido desde {fecha}"', source)
        self.assertIn('root.tr("Inicio de medición no disponible")', source)
        self.assertRegex(source, r'root\.info\.since\b')

    def test_unknown_values_are_not_shown_as_zero(self):
        source = (ROOT / 'RolesPage.qml').read_text()
        self.assertEqual(source.count('root.tr("Desconocido")'), 2)  # the total and each role's tokens
        self.assertIn('root.info.total === null', source)
        self.assertIn('role.modelData.tokens === null', source)
        self.assertIn('role.modelData.share === null ? null', source)  # the bar reads null as unknown, never a coerced 0
        self.assertNotIn('role.modelData.share * 100) ||', source)
        self.assertNotRegex(source, r'(total|tokens|share)\s*\|\|\s*0')

    def test_share_meters_never_turn_urgent(self):
        self.assertIn('warn: false', (ROOT / 'RolesPage.qml').read_text())
        self.assertIn('root.warn &&', (ROOT / 'UsageBar.qml').read_text())


if __name__ == '__main__':
    unittest.main()

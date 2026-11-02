import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_AGENTS_ICON = '\U000f16a3'  # the robot of the Omarchy agents panel
OLD_ICON = '\U000f06a9'              # AI Team's former robot, too close to the one above


class UsageSummaryUiTests(unittest.TestCase):
    def test_the_front_page_lists_one_clickable_row_of_bars_per_account(self):
        source = (ROOT / 'HarnessContent.qml').read_text()
        block = re.search(r'objectName: "usage-summary".*?\n  \}\n', source, re.S)
        self.assertTrue(block, 'the usage summary is missing')
        block = block.group(0)
        self.assertIn('root.snapshot.usage', block)
        self.assertIn('root.page === ""', block)
        self.assertIn('UsageRow {', block)
        self.assertRegex(block, r'onOpened:.*root\.usageAccount = ')
        self.assertIn('root.page = "usage"', block)

    def test_the_detail_page_is_mounted_for_the_clicked_account(self):
        source = (ROOT / 'HarnessContent.qml').read_text()
        mount = re.search(r'UsagePage \{.*?\n      \}\n', source, re.S)
        self.assertTrue(mount, 'the detail page is not mounted')
        self.assertIn('visible: root.page === "usage"', mount.group(0))
        self.assertIn('root.usageAccount', mount.group(0))

    def test_bars_and_detail_follow_the_theme_and_flag_high_usage(self):
        for name in ('UsageBar.qml', 'UsageRow.qml', 'UsagePage.qml'):
            source = (ROOT / name).read_text()
            self.assertNotRegex(source, r'#[0-9a-fA-F]{3,8}\b', name)
        self.assertIn('Commons.Color.urgent', (ROOT / 'UsageBar.qml').read_text())
        self.assertIn('>= 90', (ROOT / 'UsageBar.qml').read_text())

    def test_the_bar_icon_differs_from_the_consumption_panel_icon(self):
        source = (ROOT / 'Panel.qml').read_text()
        icon = re.search(r'objectName: "harness-icon".*?text: "(.)"', source, re.S).group(1)
        self.assertNotIn(icon, (OFFICIAL_AGENTS_ICON, OLD_ICON))


if __name__ == '__main__':
    unittest.main()

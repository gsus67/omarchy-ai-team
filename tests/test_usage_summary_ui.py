import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_AGENTS_ICON = '\U000f16a3'  # the robot of the Omarchy agents panel
OLD_ICON = '\U000f06a9'              # AI Team's former robot, too close to the one above


class UsageSummaryUiTests(unittest.TestCase):
    def test_the_main_screen_shows_the_usage_summary_only_on_the_front_page(self):
        source = (ROOT / 'HarnessContent.qml').read_text()
        block = re.search(r'Flow \{[^}]*?objectName: "usage-summary".*?\n  \}\n', source, re.S)
        self.assertTrue(block, 'the usage summary is missing')
        self.assertIn('root.snapshot.usage', block.group(0))
        self.assertIn('root.page === ""', block.group(0))

    def test_usage_colors_follow_the_theme(self):
        source = (ROOT / 'HarnessContent.qml').read_text()
        block = re.search(r'objectName: "usage-summary".*?\n  \}\n', source, re.S).group(0)
        self.assertNotRegex(block, r'#[0-9a-fA-F]{3,8}\b')
        self.assertIn('Commons.Color.urgent', block)

    def test_the_bar_icon_differs_from_the_consumption_panel_icon(self):
        source = (ROOT / 'Panel.qml').read_text()
        icon = re.search(r'objectName: "harness-icon".*?text: "(.)"', source, re.S).group(1)
        self.assertNotIn(icon, (OFFICIAL_AGENTS_ICON, OLD_ICON))


if __name__ == '__main__':
    unittest.main()

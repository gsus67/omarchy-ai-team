import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_AGENTS_ICON = '\U000f16a3'  # the robot of the Omarchy agents panel
OLD_ICON = '\U000f06a9'              # AI Team's former robot, too close to the one above


class UsageSummaryUiTests(unittest.TestCase):
    def test_the_front_page_lists_one_clickable_row_of_bars_per_account_below_the_switches(self):
        source = (ROOT / 'HarnessContent.qml').read_text()
        block = re.search(r'objectName: "usage-summary".*?(?=\n        RowLayout \{\n          Layout\.fillWidth: true\n          spacing: Style\.space\(2\)\n          Ui\.Button \{\n            objectName: "launch-default")', source, re.S)
        self.assertTrue(block, 'the usage summary is missing')
        block = block.group(0)
        self.assertIn('root.snapshot.usage', block)
        self.assertIn('UsageRow {', block)
        self.assertRegex(block, r'onOpened:.*root\.usageAccount = ')
        self.assertIn('root.page = "usage"', block)
        # order on the front page: the four switches, then the usage bars, then the launcher row
        self.assertLess(source.index('objectName: "goal-"'), source.index('objectName: "usage-summary"'))
        self.assertLess(source.index('objectName: "usage-summary"'), source.index('objectName: "launch-default"'))

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

    def test_the_detail_page_gives_window_labels_room_instead_of_clipping_them(self):
        self.assertIn('property real labelWidth', (ROOT / 'UsageBar.qml').read_text())
        page = (ROOT / 'UsagePage.qml').read_text()
        self.assertIn('labelWidth: Style.space(96)', page)
        self.assertIn('Layout.leftMargin: Style.space(96 + 4)', page)

    def test_bars_use_the_same_theme_colors_and_motion_as_the_official_meter(self):
        source = (ROOT / 'UsageBar.qml').read_text()
        self.assertIn('root.high ? Commons.Color.urgent : Commons.Color.foreground', source)
        self.assertIn('Style.selectedFillFor(Commons.Color.foreground, Commons.Color.accent)', source)
        self.assertIn('Behavior on width', source)
        self.assertIn('metrics.controlHeight * 0.14', source)

    def test_every_row_and_section_is_closed_by_a_discreet_divider(self):
        row = (ROOT / 'CompactRow.qml').read_text()
        self.assertIn('property bool divider: true', row)
        self.assertIn('Ui.PanelSeparator', row)
        self.assertIn('strength: 0.08', row)
        self.assertIn('Ui.PanelSeparator', (ROOT / 'UsageRow.qml').read_text())
        page = (ROOT / 'UsagePage.qml').read_text()
        self.assertGreaterEqual(page.count('Ui.PanelSeparator'), 4)

    def test_the_front_page_has_no_doubled_line_before_the_launcher(self):
        source = (ROOT / 'HarnessContent.qml').read_text()
        self.assertNotIn('Ui.PanelSeparator { Layout.fillWidth: true }\n\n        RowLayout {\n          Layout.fillWidth: true\n          spacing: Style.space(2)\n          Ui.Button {\n            objectName: "launch-default"', source)

    def test_the_error_detail_visibility_is_always_a_boolean(self):
        # `a && (b || c)` can evaluate to undefined and QML then logs "Unable to assign [undefined] to bool" on every open.
        source = (ROOT / 'HarnessContent.qml').read_text()
        self.assertIn('visible: !!(statusLine.truncated && (root.errorText || root.snapshot.warning))', source)

    def test_the_bar_icon_differs_from_the_consumption_panel_icon(self):
        source = (ROOT / 'Panel.qml').read_text()
        icon = re.search(r'objectName: "harness-icon".*?text: "(.)"', source, re.S).group(1)
        self.assertNotIn(icon, (OFFICIAL_AGENTS_ICON, OLD_ICON))


if __name__ == '__main__':
    unittest.main()

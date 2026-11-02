import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Pages whose option lists can be wider than the 380 px panel: they must wrap, never clip.
PAGES = ('ContextPage.qml', 'TeamPage.qml', 'MemorySettings.qml', 'ToolGroupPage.qml')


class WrappingOptionsTests(unittest.TestCase):
    def test_option_rows_that_can_overflow_wrap_to_the_next_line(self):
        for page in PAGES:
            source = (ROOT / page).read_text()
            self.assertIn('WrapButtonGroup {', source, page)
            self.assertNotIn('Ui.ButtonGroup {', source, page)

    def test_the_wrapping_group_is_a_flow_that_keeps_the_shell_group_api(self):
        source = (ROOT / 'WrapButtonGroup.qml').read_text()
        self.assertIn('Flow {', source)
        for member in ('property var options', 'property string value', 'signal changed(string value)', 'Layout.fillWidth: true'):
            self.assertIn(member, source)

    def test_the_agent_selector_summarizes_a_long_selection_instead_of_clipping_the_names(self):
        source = (ROOT / 'AgentSelector.qml').read_text()
        self.assertIn('root.tr("{n} agentes", {n: root.selectedIds.length})', source)
        self.assertIn('root.selectedIds.length > 2', source)

    def test_every_scroll_area_reserves_a_gutter_for_an_always_visible_scrollbar(self):
        # Same rule as the RGB plugin: with more to see the bar is always shown, in its own column, never over the values.
        source = (ROOT / 'HarnessContent.qml').read_text()
        self.assertIn('readonly property bool overflowing', source)
        self.assertIn('policy: scroll.overflowing ? QQC.ScrollBar.AlwaysOn : QQC.ScrollBar.AlwaysOff', source)
        self.assertIn('width: scroll.availableWidth - (scroll.overflowing ? verticalBar.width + Style.space(8) : 0)', source)


if __name__ == '__main__':
    unittest.main()

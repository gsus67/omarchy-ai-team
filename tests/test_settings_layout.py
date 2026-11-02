import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SettingsLayoutTests(unittest.TestCase):
    def test_the_agent_buttons_wrap_instead_of_overflowing_the_card(self):
        source = (ROOT / 'AgentPage.qml').read_text()
        buttons = re.search(r'(\n  [A-Za-z]+ \{)\n(?:    Layout\.fillWidth: true\n)?(?:    spacing: [^\n]*\n)?    Ui\.Button \{\n      objectName: "authenticate-', source)
        self.assertTrue(buttons, 'the button row was not found')
        self.assertEqual(buttons.group(1).strip(), 'Flow {')
        self.assertIn('Layout.fillWidth: true', source[buttons.start():buttons.start() + 300])

    def test_no_row_of_labelled_buttons_is_a_plain_rowlayout(self):
        # A RowLayout never wraps: rows of several text buttons must be Flows (the launcher row is icon-sized and fixed on purpose).
        for page in ('AgentPage.qml', 'TeamPage.qml', 'MemorySettings.qml', 'ToolGroupPage.qml', 'PiProviders.qml', 'MemoryPage.qml'):
            path = ROOT / page
            if not path.exists():
                continue
            for block in re.finditer(r'\n( *)RowLayout \{\n((?:\1  .*\n)+)', path.read_text()):
                self.assertLess(len(re.findall(r'Ui\.Button \{', block.group(2))), 2, page)

    def test_settings_groups_the_agents_by_family_in_separate_blocks(self):
        source = (ROOT / 'SettingsPage.qml').read_text()
        self.assertIn('readonly property var agentGroups', source)
        for family in ('Claude Code', 'Codex', 'Pi'):
            self.assertIn('"' + family + '"', source)
        self.assertNotRegex(source, r'Repeater \{\n    model: root\.agents\n')
        self.assertIn('model: root.agentGroups', source)


if __name__ == '__main__':
    unittest.main()

"""The CLI that the main button opens is chosen in the Chief row of Settings > Team."""
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import harness


def source(name):
    return (ROOT / name).read_text()


class ChiefRowTests(unittest.TestCase):
    def test_settings_no_longer_owns_the_default_selector(self):
        text = source('SettingsPage.qml')
        for fragment in ('default-agent', 'set-default-agent', '"Abrir con"', 'defaultOptions'):
            self.assertNotIn(fragment, text)

    def test_team_page_has_one_chief_row_that_sets_the_default_agent(self):
        text = source('TeamPage.qml')
        self.assertEqual(text.count('objectName: "default-agent"'), 1)
        self.assertIn('objectName: "team-label-jefe"', text)
        self.assertIn('["set-default-agent", value]', text)
        self.assertRegex(text, r'text: root\.tr\("Jefe"\)')
        # Only installed, selected profiles can be opened; "none" clears the preference.
        self.assertIn('agent.enabled', text)
        self.assertRegex(text, r'value: "none"')

    def test_main_button_and_chief_row_share_the_saved_preference(self):
        content = source('HarnessContent.qml')
        self.assertIn('defaultAgent: root.launcher.defaultAgent || ""', content)
        self.assertIn('root.launcher.defaultAgent', content)  # the main button label still reads it
        self.assertEqual(len(re.findall(r'"set-default-agent"', ''.join(source(n) for n in ('TeamPage.qml', 'SettingsPage.qml', 'HarnessContent.qml')))), 1)

    def test_saved_default_is_untouched_by_the_move(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
                'HOME': temp, 'XDG_CONFIG_HOME': temp + '/config', 'PATH': '', 'LANG': 'en_US', 'LC_ALL': ''}, clear=True):
            config = harness.load_config()
            config.update(agents={'codex-2': True}, defaultAgent='codex-2')
            harness.save_config(config)
            before = harness.config_path().read_bytes()
            self.assertEqual(harness.status()['launcher']['defaultAgent'], 'codex-2')
            self.assertEqual(harness.config_path().read_bytes(), before)
            self.assertEqual(json.loads(before)['defaultAgent'], 'codex-2')


if __name__ == '__main__':
    unittest.main()

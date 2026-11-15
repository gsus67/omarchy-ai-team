"""The GitHub task workflow is an explicit, disabled-by-default Team preference."""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class GithubFlowUiTests(unittest.TestCase):
    def test_switch_is_strictly_opt_in_and_sends_the_new_value(self):
        page = (ROOT / 'TeamPage.qml').read_text()
        switch = re.search(r'Ui\.ToggleSwitch \{(?:(?!Ui\.ToggleSwitch).)*?objectName: "team-github-switch".*?\n  \}', page, re.S)
        self.assertIsNotNone(switch)
        self.assertIn('checked: root.info.githubFlow === true', switch[0])
        self.assertIn('["team-github", String(!checked)]', switch[0])


if __name__ == '__main__':
    unittest.main()

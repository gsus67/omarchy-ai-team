"""The team smoke must not re-enter its timer while it sends key presses."""
from pathlib import Path
import re
import unittest

SMOKE = (Path(__file__).resolve().parents[1] / 'tools' / 'preview.py').read_text()


class SmokeTeamKeysTest(unittest.TestCase):
    def test_every_team_key_burst_is_guarded(self):
        # keyClick spins the event loop; an unguarded burst lets the timer skip a step.
        bursts = re.findall(r'teamKeying=true\n(?:\s*pointer\.keyClick\([^)]*\)\n)+\s*teamKeying=false', SMOKE)
        self.assertEqual(len(bursts), 2)
        self.assertIn('if (teamKeying) return', SMOKE)
        section = SMOKE[SMOKE.index('if (teamStep === 4)'):SMOKE.index('if (teamStep === 7)')]
        self.assertEqual(section.count('pointer.keyClick'), sum(b.count('pointer.keyClick') for b in bursts))


if __name__ == '__main__':
    unittest.main()

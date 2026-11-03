import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BARE_COLOR = re.compile(r'(?<![\w.])Color\.\w+')


class QmlThemeColorsTest(unittest.TestCase):
    def test_theme_colors_use_the_commons_qualifier(self):
        # A bare Color.* resolves to undefined at runtime: "Unable to assign [undefined] to QColor".
        offenders = [f'{path.name}:{number}' for path in sorted(ROOT.glob('*.qml'))
                     for number, line in enumerate(path.read_text().splitlines(), 1)
                     if BARE_COLOR.search(line)]
        self.assertEqual(offenders, [])


if __name__ == '__main__':
    unittest.main()

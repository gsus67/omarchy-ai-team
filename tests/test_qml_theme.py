from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class QmlThemeTests(unittest.TestCase):
    def test_qml_has_no_literal_colors(self):
        pattern = re.compile(r'#[0-9a-fA-F]{3,8}\b|Qt\.rgba?\(|Qt\.hsla?\(|color:\s*"(?!transparent")')
        offenders = [f'{path.name}:{number}' for path in ROOT.glob('*.qml')
                     for number, line in enumerate(path.read_text().splitlines(), 1)
                     if pattern.search(line)]
        self.assertEqual(offenders, [])

    def test_qml_has_no_literal_font_sizes(self):
        pattern = re.compile(r'pixelSize:\s*\d')
        offenders = [f'{path.name}:{number}' for path in ROOT.glob('*.qml')
                     for number, line in enumerate(path.read_text().splitlines(), 1)
                     if pattern.search(line)]
        self.assertEqual(offenders, [])

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text_blocks(source):
    for match in re.finditer(r'\b(?:Text|Label)\s*\{', source):
        depth, index = 1, match.end()
        while depth and index < len(source):
            depth += {'{': 1, '}': -1}.get(source[index], 0)
            index += 1
        yield source.count('\n', 0, match.start()) + 1, source[match.start():index]


class QmlSecurityTests(unittest.TestCase):
    def test_every_text_is_plain_because_third_party_markup_can_fetch_remote_urls(self):
        for path in ROOT.glob('*.qml'):
            for line, block in text_blocks(path.read_text()):
                self.assertIn('Text.PlainText', block, f'{path.name}:{line}')

    def test_no_markup_format_or_remote_url(self):
        pattern = re.compile(r'RichText|StyledText|MarkdownText|AutoText|https?://')
        for path in ROOT.glob('*.qml'):
            self.assertIsNone(pattern.search(path.read_text()), path.name)


if __name__ == '__main__':
    unittest.main()

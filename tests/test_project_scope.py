import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import project_scope


class ProjectScopeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.settings = self.root / 'config/settings.json'

    def test_discovery_is_read_only_and_rejects_invalid_scopes(self):
        self.assertEqual(project_scope.project_options(self.settings),
                         [{'value': 'default', 'label': 'Preferencias generales'}])
        self.assertFalse(self.settings.parent.exists())
        for value in ('../elsewhere', 'default/other', None, 'A' * 64):
            with self.subTest(value=value), self.assertRaises(project_scope.ScopeError):
                project_scope.scope_key(value)
        self.assertEqual(project_scope.scope_key('a' * 64), 'a' * 64)

    def test_project_labels_reuse_metadata_and_preserve_unlabelled_preferences(self):
        key = 'a' * 64
        other = 'b' * 64
        folder = self.settings.parent / 'projects' / key
        folder.mkdir(parents=True)
        (folder / '.ai-team-project.json').write_text(json.dumps({'schemaVersion': 1, 'name': 'Proyecto A'}))
        options = project_scope.project_options(self.settings, projects=(key, other))
        self.assertEqual(options, [{'value': 'default', 'label': 'Preferencias generales'},
                                 {'value': key, 'label': 'Proyecto A · ' + key[:8]},
                                 {'value': other, 'label': 'Proyecto ' + other[:10]}])
        self.assertFalse(self.settings.exists())

    def test_project_label_drops_separators_and_bidi_without_trailing_space(self):
        key = 'a' * 64
        folder = self.settings.parent / 'projects' / key
        folder.mkdir(parents=True)
        name = 'Pro\u2028ye\u202ecto' + 'x' * 94 + ' y'
        (folder / '.ai-team-project.json').write_text(json.dumps({'schemaVersion': 1, 'name': name}))
        label = project_scope.project_options(self.settings, projects=(key,))[1]['label']
        self.assertEqual(label, 'Proyecto' + 'x' * 92 + ' · ' + key[:8])


if __name__ == '__main__':
    unittest.main()

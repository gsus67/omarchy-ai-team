"""Plugin-only persistence of context settings; HOME/XDG are synthetic."""
import re

import contextlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import harness


class ContextSettingsStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = {'HOME': self.temp.name, 'XDG_CONFIG_HOME': self.temp.name + '/config', 'PATH': '',
               'LANG': 'en_US', 'LC_ALL': '', 'LC_MESSAGES': ''}
        patcher = patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = harness.main(list(argv))
        return code, json.loads(out.getvalue() or 'null')

    def saved(self):
        return json.loads(harness.config_path().read_text())

    def test_set_persists_reopens_and_snapshot_shows_settings_and_resolved(self):
        code, _ = self.run_cli('context-set', 'read_window_lines', '200')
        self.assertEqual(code, 0)
        self.assertEqual(self.saved()['context']['read_window_lines'], 200)
        code, reply = self.run_cli('status')
        context = reply['data']['context']
        self.assertEqual(context['settings']['read_window_lines'], 200)
        self.assertEqual(context['resolved']['jefe']['compact_at_tokens'], '100000')
        self.assertEqual(context['resolved']['code']['read_window_lines'], 200)
        self.assertEqual(sorted(context['resolved']), ['code', 'jefe', 'plan', 'review', 'simple'])

    def test_status_never_materializes_defaults(self):
        self.run_cli('status')
        path = harness.config_path()
        self.assertTrue(not path.exists() or 'context' not in self.saved())

    def test_role_scope_inherits_and_only_changes_that_role(self):
        self.run_cli('context-set', 'bounded_reads', 'false', '--role', 'code')
        self.assertEqual(self.saved()['context']['roles']['code'], {'bounded_reads': False})
        context = self.run_cli('status')[1]['data']['context']['resolved']
        self.assertIs(context['code']['bounded_reads'], False)
        self.assertIs(context['plan']['bounded_reads'], True)

    def test_64bit_integers_travel_as_exact_decimal_text(self):
        self.run_cli('context-set', 'compact_at_tokens', '9223372036854775807')
        self.assertEqual(self.saved()['context']['compact_at_tokens'], 2**63 - 1)

    def test_invalid_values_are_rejected_without_touching_the_file(self):
        self.run_cli('context-set', 'read_window_lines', '150')
        before = harness.config_path().read_bytes()
        for argv in [('read_window_lines', '0'), ('read_window_lines', '"120"'), ('read_window_lines', 'true'),
                     ('bounded_reads', '1'), ('bounded_reads', 'nope'), ('unknown_key', 'true'),
                     ('apply', '"global"'), ('compact_at_tokens', '1000'),
                     ('compact_at_tokens', '9223372036854775808')]:
            with self.subTest(argv=argv):
                code, reply = self.run_cli('context-set', *argv)
                self.assertNotEqual(code, 0)
                self.assertIs(reply['ok'], False)
                self.assertEqual(harness.config_path().read_bytes(), before)
        code, _ = self.run_cli('context-set', 'enabled', 'false', '--role', 'code')
        self.assertNotEqual(code, 0)
        self.assertEqual(harness.config_path().read_bytes(), before)

    def test_reset_restores_defaults_globally_or_for_one_role(self):
        self.run_cli('context-set', 'read_window_lines', '200')
        self.run_cli('context-set', 'bounded_reads', 'false', '--role', 'code')
        self.run_cli('context-set', 'bounded_reads', 'false', '--role', 'plan')
        self.run_cli('context-reset', '--role', 'code')
        self.assertEqual(self.saved()['context']['roles'], {'plan': {'bounded_reads': False}})
        self.run_cli('context-reset', '--role', 'plan')
        self.assertNotIn('roles', self.saved()['context'])
        self.assertEqual(self.saved()['context']['read_window_lines'], 200)
        self.run_cli('context-reset')
        self.assertNotIn('context', self.saved())

    def test_snapshot_delivers_token_limits_as_decimal_text_without_losing_64_bit_precision(self):
        for big in ('9007199254740993', '9223372036854775807'):
            with self.subTest(value=big):
                self.assertEqual(self.run_cli('context-set', 'compact_at_tokens', big)[0], 0)
                self.assertEqual(self.run_cli('context-set', 'worker_report_max_tokens', big, '--role', 'code')[0], 0)
                code, reply = self.run_cli('status')
                context = reply['data']['context']
                self.assertEqual(context['settings']['compact_at_tokens'], big)
                self.assertEqual(context['resolved']['plan']['compact_at_tokens'], big)
                self.assertEqual(context['resolved']['code']['worker_report_max_tokens'], big)
                self.assertEqual(context['settings']['roles']['code']['worker_report_max_tokens'], big)
                for key in ('compact_floor_tokens', 'worker_report_max_tokens'):
                    self.assertIsInstance(context['settings'][key], str)
                self.assertEqual(self.saved()['context']['compact_at_tokens'], int(big))  # stored as a number

    def test_role_reset_that_would_break_the_floor_is_rejected_without_touching_the_file(self):
        self.assertEqual(self.run_cli('context-set', 'compact_at_tokens', '130000', '--role', 'jefe')[0], 0)
        self.assertEqual(self.run_cli('context-set', 'compact_floor_tokens', '120000')[0], 0)
        before = harness.config_path().read_bytes()
        code, reply = self.run_cli('context-reset', '--role', 'jefe')
        self.assertNotEqual(code, 0)
        self.assertIs(reply['ok'], False)
        self.assertIn('floor', reply['error'])
        self.assertEqual(harness.config_path().read_bytes(), before)
        self.assertEqual(self.run_cli('status')[0], 0)  # the stored file still loads

    def test_only_plugin_settings_change_with_private_mode_and_no_profile_sync(self):
        config = harness.load_config()
        config.update(foreign={'keep': [1, 2]}, language='es')
        harness.save_config(config)
        with patch.object(harness, 'manage_component') as manage, \
                patch.object(harness, 'select_agents') as select:
            self.assertEqual(self.run_cli('context-set', 'stable_prompt_prefix', 'false')[0], 0)
            self.assertEqual(self.run_cli('context-reset')[0], 0)
        for mocked in (manage, select):
            mocked.assert_not_called()
        saved = self.saved()
        self.assertEqual((saved['foreign'], saved['language']), ({'keep': [1, 2]}, 'es'))
        self.assertEqual(stat.S_IMODE(harness.config_path().stat().st_mode), 0o600)
        self.assertEqual(sorted(p.name for p in harness.config_path().parent.iterdir()),
                         [harness.config_path().name])


class ContextPageDefaultScopeTests(unittest.TestCase):
    @staticmethod
    def strip_comments(source):
        out, i, n = [], 0, len(source)
        while i < n:
            two = source[i:i + 2]
            if source[i] == '"':
                j = i + 1
                while j < n and source[j] not in '"\n':
                    j += 2 if source[j] == '\\' else 1
                out.append(source[i:j + 1])
                i = j + 1
            elif two == '//':
                while i < n and source[i] != '\n':
                    i += 1
            elif two == '/*':
                end = source.find('*/', i + 2)
                i = n if end < 0 else end + 2
                out.append(' ')
            else:
                out.append(source[i])
                i += 1
        return ''.join(out)

    @classmethod
    def default_scope(cls, source):
        found = re.findall(r'^[ \t]*property[ \t]+string[ \t]+scope[ \t]*:[ \t]*"([^"\n]*)"[ \t]*;?[ \t]*$',
                           cls.strip_comments(source), re.M)
        return found[0] if len(found) == 1 else None

    @classmethod
    def scope_options(cls, source):
        code = cls.strip_comments(source)
        block = re.search(r'objectName:\s*"context-scope".*?options:\s*\[(.*?)\]\s*\n\s*value:', code, re.S)
        return re.findall(r'value:\s*"([^"]+)"', block.group(1)) if block else []

    def test_the_context_tab_opens_with_the_boss_role_selected(self):
        source = (Path(__file__).resolve().parents[1] / 'ContextPage.qml').read_text()
        self.assertEqual(self.default_scope(source), 'jefe')
        self.assertIn(self.default_scope(source), self.scope_options(source))

    def test_a_commented_literal_is_not_a_declaration(self):
        source = '// property string scope: "jefe"\n/* property string scope: "jefe" */\nproperty string scope: ""\n'
        self.assertEqual(self.default_scope(source), '')
        self.assertIsNone(self.default_scope('// property string scope: "jefe"\n/* property string scope: "jefe" */\n'))

    def test_delimiters_inside_strings_do_not_hide_the_declaration(self):
        source = 'Item { text: "/*"; other: "// x"; label: "*/" }\nproperty string scope: "jefe" // trailing\n'
        self.assertEqual(self.default_scope(source), 'jefe')
        self.assertEqual(self.default_scope('x: "a\\"/*"\nproperty string scope: "jefe"\n'), 'jefe')

    def test_only_a_complete_literal_is_a_declaration(self):
        self.assertIsNone(self.default_scope('property string scope: "jefe-invalid" + x\n'))
        self.assertNotIn(self.default_scope('property string scope: "jefe-invalid"\n'), self.scope_options(
            'objectName: "context-scope"\n options: [{value: "jefe"}]\n value: x'))
        self.assertEqual(self.default_scope('property string scope: "jefe"; // ok\n'), 'jefe')


if __name__ == '__main__':
    unittest.main()

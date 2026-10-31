import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'omarchy-ai-team/tools/ecc-memory/node_modules/ecc-universal'
NODE = shutil.which('node')


def tool_text(reply):
    return json.loads(reply['result']['content'][0]['text'])


@unittest.skipUnless(NODE and (RUNTIME / 'scripts/memory-mcp.mjs').is_file(), 'Necesita el runtime oficial de ECC instalado')
class MemoryProjectsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.home = self.root / 'home'
        self.work = self.home / 'Work'
        self.store = self.root / 'store'
        for name in ('alpha', 'beta'):
            (self.work / name).mkdir(parents=True)
            subprocess.run(['git', 'init', '-q', str(self.work / name)], check=True)
        (self.work / 'no-es-repo').mkdir()

    def run_server(self, cwd, messages):
        result = subprocess.run(
            [sys.executable, '-B', str(ROOT / 'backend/memory_mcp.py'), '--agent', 'claude', '--runtime', str(RUNTIME),
             '--node', NODE, '--store', str(self.store)],
            input=''.join(json.dumps(message) + '\n' for message in messages), capture_output=True, text=True, timeout=60,
            cwd=cwd, env={'HOME': str(self.home), 'PATH': os.environ['PATH'], 'LC_ALL': 'C'})
        self.assertEqual(result.returncode, 0, result.stderr)
        return list(map(json.loads, result.stdout.splitlines()))

    def session(self, cwd, *calls):
        """Run one real server through the same launcher the agents use, and return its replies."""
        messages = [{'jsonrpc': '2.0', 'id': 0, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}}},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'}]
        for index, (name, arguments) in enumerate(calls, 1):
            messages.append({'jsonrpc': '2.0', 'id': index, 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}})
        messages.append({'jsonrpc': '2.0', 'id': 99, 'method': 'tools/list'})
        replies = {reply['id']: reply for reply in self.run_server(cwd, messages)}
        self.tools = replies[99]['result']['tools']
        return [replies[index] for index in range(1, len(calls) + 1)]

    def save(self, title, **extra):
        return ('memory_save', {'title': title, 'body': 'synthetic body ' + title, 'kind': 'decision', **extra})

    def found(self, reply):
        return [row['memory']['title'] for row in tool_text(reply)['results']]

    def test_one_session_keeps_each_project_in_its_own_memory(self):
        replies = self.session(self.home, self.save('dato alpha', project='alpha'), self.save('dato beta', project='beta'),
                               ('memory_search', {'query': 'dato', 'project': 'alpha'}),
                               ('memory_search', {'query': 'dato', 'project': 'beta'}))
        self.assertEqual(self.found(replies[2]), ['dato alpha'])
        self.assertEqual(self.found(replies[3]), ['dato beta'])

    def test_a_session_opened_in_a_repo_finds_what_another_session_saved_for_it(self):
        self.session(self.home, self.save('dato alpha', project='alpha'))
        replies = self.session(self.work / 'alpha', ('memory_search', {'query': 'dato'}))
        self.assertEqual(self.found(replies[0]), ['dato alpha'])

    def test_project_memory_uses_the_store_key_of_the_repo(self):
        self.session(self.home, self.save('dato alpha', project='alpha'))
        key = hashlib.sha256(os.fsencode((self.work / 'alpha/.git').resolve())).hexdigest()
        self.assertTrue((self.store / 'projects' / key / 'project/decisions').is_dir())
        label = json.loads((self.store / 'projects' / key / '.ai-team-project.json').read_text())
        self.assertEqual(label, {'schemaVersion': 1, 'name': 'alpha'})
        self.assertEqual((self.store / 'projects' / key / '.ai-team-project.json').stat().st_mode & 0o777, 0o600)

    def test_saving_outside_a_project_asks_which_one_and_stores_nothing(self):
        reply = self.session(self.home, self.save('sin dueño'))[0]
        self.assertTrue(reply['result']['isError'])
        text = reply['result']['content'][0]['text']
        for name in ('alpha', 'beta', 'compartido'):
            self.assertIn(name, text)
        self.assertEqual(list(self.store.rglob('mem_*.md')), [])

    def test_unknown_project_is_rejected_and_creates_no_memory(self):
        reply = self.session(self.home, self.save('errata', project='alhpa'))[0]
        self.assertTrue(reply['result']['isError'])
        self.assertIn('alpha', reply['result']['content'][0]['text'])
        self.assertEqual(list(self.store.rglob('mem_*.md')), [])

    def test_project_names_cannot_escape_the_store(self):
        for name in ('../alpha', '/etc', 'alpha/../beta', '.git', ''):
            with self.subTest(name=name):
                reply = self.session(self.home, self.save('fuga', project=name))[0]
                self.assertTrue(reply['result']['isError'])
        self.assertEqual(list(self.store.rglob('mem_*.md')), [])

    def test_folder_that_is_not_a_repo_is_not_offered_as_a_project(self):
        report = tool_text(self.session(self.home, ('memory_doctor', {}))[0])
        self.assertNotIn('no-es-repo', report['projects'])

    def test_a_symlinked_project_folder_is_not_offered(self):
        (self.work / 'enlace').symlink_to(self.work / 'alpha')
        report = tool_text(self.session(self.home, ('memory_doctor', {}))[0])
        self.assertNotIn('enlace', report['projects'])

    def test_shared_memory_is_found_from_anywhere_without_naming_a_project(self):
        self.session(self.work / 'alpha', self.save('norma común', project='compartido'))
        replies = self.session(self.home, ('memory_search', {'query': 'norma'}))
        self.assertEqual(self.found(replies[0]), ['norma común'])

    def test_shared_memory_does_not_leak_into_a_project_search(self):
        self.session(self.home, self.save('norma común', project='compartido'))
        replies = self.session(self.home, ('memory_search', {'query': 'norma', 'project': 'alpha'}))
        self.assertEqual(self.found(replies[0]), [])

    def test_doctor_reports_the_active_project_and_the_known_ones(self):
        report = tool_text(self.session(self.work / 'alpha', ('memory_doctor', {}))[0])
        self.assertEqual(report['activeProject'], 'alpha')
        self.assertEqual(report['projects'], ['alpha', 'beta', 'compartido'])
        self.assertTrue(report['ok'])

    def test_doctor_outside_a_project_reports_none(self):
        self.assertIsNone(tool_text(self.session(self.home, ('memory_doctor', {}))[0])['activeProject'])

    def test_a_worktree_shares_the_memory_of_its_repo(self):
        alpha = self.work / 'alpha'
        for command in (['commit', '-q', '--allow-empty', '-m', 'x'], ['worktree', 'add', '-q', str(self.root / 'wt')]):
            subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', '-C', str(alpha), *command], check=True)
        self.session(self.root / 'wt', self.save('dato worktree'))
        replies = self.session(alpha, ('memory_search', {'query': 'dato'}))
        self.assertEqual(self.found(replies[0]), ['dato worktree'])

    def test_session_in_a_plain_folder_keeps_its_own_memory(self):
        folder = self.root / 'carpeta'; folder.mkdir()
        self.session(folder, self.save('dato carpeta'))
        replies = self.session(folder, ('memory_search', {'query': 'dato'}))
        self.assertEqual(self.found(replies[0]), ['dato carpeta'])

    def test_every_tool_declares_the_project_option_and_the_tool_set_is_unchanged(self):
        self.session(self.home)
        self.assertEqual({tool['name'] for tool in self.tools}, {'memory_save', 'memory_search', 'memory_read', 'memory_doctor'})
        for tool in self.tools:
            self.assertIn('project', tool['inputSchema']['properties'])

    def test_read_finds_a_memory_in_the_named_project(self):
        saved = tool_text(self.session(self.home, self.save('dato alpha', project='alpha'))[0])['memory']['id']
        replies = self.session(self.home, ('memory_read', {'id': saved, 'project': 'alpha'}))
        self.assertEqual(tool_text(replies[0])['memory']['title'], 'dato alpha')

    def test_secrets_are_still_refused_by_ecc(self):
        secret = 'ghp_' + 'a' * 36
        reply = self.session(self.home, ('memory_save', {'title': 'x', 'body': secret, 'project': 'alpha'}))[0]
        self.assertTrue(reply['result']['isError'])
        self.assertEqual(list(self.store.rglob('mem_*.md')), [])

    def test_another_repo_with_a_project_name_keeps_its_own_memory(self):
        other = self.root / 'otra/alpha'; other.mkdir(parents=True)
        subprocess.run(['git', 'init', '-q', str(other)], check=True)
        self.session(other, self.save('dato otra'))
        replies = self.session(self.work / 'alpha', ('memory_search', {'query': 'dato'}))
        self.assertEqual(self.found(replies[0]), [])
        self.assertEqual(self.found(self.session(other, ('memory_search', {'query': 'dato'}))[0]), ['dato otra'])

    def test_a_repo_named_like_the_shared_memory_does_not_take_its_place(self):
        (self.work / 'compartido').mkdir()
        subprocess.run(['git', 'init', '-q', str(self.work / 'compartido')], check=True)
        self.session(self.work / 'compartido', self.save('dato del repo'))
        self.assertEqual(self.found(self.session(self.home, ('memory_search', {'query': 'dato', 'project': 'compartido'}))[0]), [])

    def test_a_folder_without_permission_does_not_break_the_memory(self):
        locked = self.work / 'bloqueada'; locked.mkdir(mode=0o000)
        self.addCleanup(locked.chmod, 0o700)
        reply = self.session(self.home, self.save('dato alpha', project='alpha'))[0]
        self.assertNotIn('isError', reply['result'])

    def test_a_git_file_or_link_is_not_offered_as_a_project(self):
        (self.work / 'gitfile').mkdir(); (self.work / 'gitfile/.git').write_text('gitdir: /elsewhere\n')
        (self.work / 'gitlink').mkdir(); (self.work / 'gitlink/.git').symlink_to(self.work / 'alpha/.git')
        report = tool_text(self.session(self.home, ('memory_doctor', {}))[0])
        self.assertNotIn('gitfile', report['projects']); self.assertNotIn('gitlink', report['projects'])

    def test_project_that_is_not_text_is_rejected(self):
        for value in (None, 5, ['alpha'], {'a': 1}):
            with self.subTest(value=value):
                reply = self.session(self.home, self.save('raro', project=value))[0]
                self.assertTrue(reply['result']['isError'])
        self.assertEqual(list(self.store.rglob('mem_*.md')), [])

    def test_calls_before_initializing_are_left_to_ecc(self):
        replies = self.run_server(self.home, [{'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                               'params': {'name': 'memory_save', 'arguments': {'title': 'x', 'body': 'y'}}}])
        self.assertEqual(replies[0]['error']['code'], -32002)

    def test_a_call_without_id_gets_no_reply_and_saves_nothing(self):
        start = {'jsonrpc': '2.0', 'id': 0, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}}}
        replies = self.run_server(self.home, [start, {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                                              {'jsonrpc': '2.0', 'method': 'tools/call',
                                               'params': {'name': 'memory_save', 'arguments': {'title': 'x', 'body': 'y', 'project': 'alpha'}}}])
        self.assertEqual([reply['id'] for reply in replies], [0])
        self.assertEqual(list(self.store.rglob('mem_*.md')), [])

    def test_arguments_of_the_wrong_type_are_rejected_by_ecc(self):
        reply = self.session(self.home, ('memory_search', 'texto'))[0]
        self.assertIn('error', reply)

    def test_user_scope_stays_disabled(self):
        reply = self.session(self.home, self.save('x', project='alpha', scope='user'))[0]
        self.assertIn('user memory scope is disabled', reply['error']['message'])
        self.assertEqual(list(self.store.rglob('mem_*.md')), [])


if __name__ == '__main__':
    unittest.main()

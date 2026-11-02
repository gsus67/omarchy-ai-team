import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import price_autofill
import price_table

PROPOSAL = {'models': {'gpt-6.1-sol': {'input': 2.0, 'output': 10.0, 'cacheRead': 0.1, 'cacheWrite': None,
                                       'source': 'https://developers.openai.com/api/docs/pricing'}}}


def agent(identifier='claude-2', installed=True, enabled=True):
    return {'id': identifier, 'name': identifier, 'binary': '/bin/' + identifier.split('-')[0], 'enabled': enabled,
            'installation': 'installed' if installed else 'missing'}


class AutofillTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        patcher = patch.dict(os.environ, {'HOME': str(self.home)}); patcher.start(); self.addCleanup(patcher.stop)

    def reply(self, text, code=0, codex=False):
        stdout = (json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': text}}) if codex
                  else json.dumps({'type': 'result', 'result': text}))
        return subprocess.CompletedProcess([], code, stdout=stdout, stderr='')

    def run_with(self, completed, config=None, agents=None):
        config = {'defaultAgent': 'claude-2', **(config or {})}
        snapshot = {'agents': agents or [agent('claude-2')]}
        with patch.object(price_autofill.subprocess, 'run', return_value=completed) as run:
            return price_autofill.run(config, snapshot), run

    def test_the_chief_is_the_default_agent_and_must_be_usable(self):
        self.assertEqual(price_autofill.chief({'defaultAgent': 'codex'}, {'agents': [agent('codex')]})['id'], 'codex')
        for config, agents in (({'defaultAgent': ''}, [agent('claude')]), ({'defaultAgent': 'pi'}, [agent('pi')]),
                               ({'defaultAgent': 'claude'}, [agent('claude', installed=False)]),
                               ({'defaultAgent': 'claude'}, [agent('claude', enabled=False)])):
            with self.assertRaises(ValueError):
                price_autofill.chief(config, {'agents': agents})

    def test_the_prompt_asks_for_official_prices_and_forbids_guessing(self):
        text = price_autofill.prompt(['gpt-6.1-sol', 'claude-opus-4-8'])
        for wanted in ('gpt-6.1-sol', 'claude-opus-4-8', 'null', 'official', 'USD', 'million', 'source', 'JSON'):
            self.assertIn(wanted, text)
        self.assertIn('Do not guess', text)

    def test_claude_gets_web_tools_only_and_codex_gets_search_in_a_read_only_sandbox(self):
        claude = price_autofill.command(agent('claude-2'), {})
        self.assertEqual(claude[claude.index('--allowedTools') + 1:], ['WebSearch', 'WebFetch'])
        self.assertEqual(claude[claude.index('--tools') + 1], 'WebSearch,WebFetch')
        self.assertIn('--strict-mcp-config', claude)
        self.assertIn('-p', claude)
        codex = price_autofill.command(agent('codex-2'), {})
        self.assertLess(codex.index('--search'), codex.index('exec'))
        self.assertEqual(codex[codex.index('-s') + 1], 'read-only')

    def test_a_valid_reply_fills_only_the_empty_prices_with_their_source(self):
        result, run = self.run_with(self.reply('Here you go:\n```json\n' + json.dumps(PROPOSAL) + '\n```'))
        self.assertEqual(result['error'], '')
        self.assertEqual({(row['model'], row['field']) for row in result['filled']},
                         {('gpt-6.1-sol', 'input'), ('gpt-6.1-sol', 'output'), ('gpt-6.1-sol', 'cacheRead')})
        self.assertEqual(result['filled'][0]['source'], 'https://developers.openai.com/api/docs/pricing')
        saved = json.loads(price_table.path().read_text())
        self.assertEqual(saved['gpt-6.1-sol']['output'], 10.0)
        self.assertIsNone(saved['gpt-6.1-sol']['cacheWrite'])
        self.assertIn('gpt-6.1-sol', result['pending'])
        self.assertEqual(run.call_args.kwargs['env']['CLAUDE_CONFIG_DIR'], str(self.home / '.claude-2'))

    def test_a_codex_chief_is_read_from_its_json_events(self):
        result, _ = self.run_with(self.reply(json.dumps(PROPOSAL), codex=True), config={'defaultAgent': 'codex'}, agents=[agent('codex')])
        self.assertEqual(result['error'], '')
        self.assertTrue(result['filled'])

    def test_a_failed_or_unreadable_reply_changes_nothing_and_says_why(self):
        for completed in (self.reply('no json here'), self.reply('{"models": 5}'), self.reply('x', code=1)):
            result, _ = self.run_with(completed)
            self.assertNotEqual(result['error'], '')
            self.assertEqual(result['filled'], [])
        self.assertFalse(price_table.path().exists())

    def test_a_timeout_is_reported_not_raised(self):
        with patch.object(price_autofill.subprocess, 'run', side_effect=subprocess.TimeoutExpired('x', 1)):
            result = price_autofill.run({'defaultAgent': 'claude-2'}, {'agents': [agent('claude-2')]})
        self.assertNotEqual(result['error'], '')

    def test_nothing_is_asked_when_every_price_is_already_set(self):
        for row in price_table.rows():
            for field in price_table.FIELDS:
                if row[field] is None:
                    price_table.set_value(row['model'], field, '1')
        result, run = self.run_with(self.reply('{}'))
        run.assert_not_called()
        self.assertEqual((result['filled'], result['error']), ([], ''))


if __name__ == '__main__':
    unittest.main()

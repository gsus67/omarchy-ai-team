from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import native_team
import team

FREE = ['nous/poolside/laguna-s-2.1:free', 'nous/stepfun/step-3.7-flash:free']
CATALOG = {'codex': [], 'pi': [{'id': 'xai/grok'}], 'nousEffort': ['nous/stepfun/step-3.7-flash:free']}


def models(rows):
    return [(row['agent'], row['model']) for row in rows]


class AlternativeTests(unittest.TestCase):
    def state(self, selected=('claude', 'pi'), paid_ok=True):
        config = {'piPaidApproved': paid_ok, 'team': {'roles': {}}}
        return team.effective(config, set(selected), CATALOG, FREE)

    def alternatives(self, key, **options):
        state = self.state(**options)
        return team.alternatives(key, state['roles'], state['options'])

    def test_review_prefers_a_strong_model_when_its_free_model_fails(self):
        self.assertEqual(models(self.alternatives('review'))[0], ('claude', 'sonnet'))

    def test_review_never_uses_the_model_that_writes_the_code(self):
        self.assertNotIn(('claude', 'opus'), models(self.alternatives('review')))

    def test_code_never_falls_back_to_the_review_model(self):
        state = self.state(selected=('claude',))
        rows = team.alternatives('code', state['roles'], state['options'])
        self.assertEqual(state['roles']['review']['model'], 'sonnet')
        self.assertNotIn(('claude', 'sonnet'), models(rows))

    def test_a_role_never_lists_its_own_model(self):
        self.assertNotIn(('pi', FREE[0]), models(self.alternatives('review')))

    def test_paid_models_are_never_alternatives_even_with_consent(self):
        for key in team.ROLES:
            self.assertNotIn(('pi', 'xai/grok'), models(self.alternatives(key, paid_ok=True)))

    def test_pi_is_not_an_alternative_for_writing_roles(self):
        for key in ('plan', 'code'):
            self.assertFalse(any(agent == 'pi' for agent, _ in models(self.alternatives(key))))

    def test_simple_stays_free_first(self):
        self.assertEqual(models(self.alternatives('simple'))[0], ('pi', FREE[1]))

    def test_free_model_with_levels_comes_before_one_without(self):
        state = self.state(selected=('claude', 'codex', 'pi'))
        rows = [row for row in team.alternatives('simple', state['roles'], state['options']) if row['agent'] == 'pi']
        self.assertEqual([row['model'] for row in rows], [FREE[1], FREE[0]])
        self.assertEqual((rows[0]['effort'], rows[1]['effort']), ('medium', ''))

    def test_older_codex_of_the_same_family_is_not_an_alternative(self):
        catalog = {**CATALOG, 'codex': [{'id': model, 'supportedReasoningEfforts': [{'reasoningEffort': 'high'}]}
                                        for model in ('gpt-6-sol', 'gpt-6.1-sol', 'gpt-6-luna')]}
        state = team.effective({'team': {'roles': {}}}, {'codex'}, catalog, [])
        rows = models(team.alternatives('code', state['roles'], state['options']))
        self.assertNotIn(('codex', 'gpt-6-sol'), rows)

    def test_review_never_uses_the_model_of_simple_either(self):
        state = self.state(selected=('claude', 'codex', 'pi'))
        rows = models(team.alternatives('review', state['roles'], state['options']))
        self.assertNotIn((state['roles']['simple']['agent'], state['roles']['simple']['model']), rows)

    def test_claude_alternative_uses_the_role_effort(self):
        self.assertEqual(self.alternatives('review')[0]['effort'], 'high')

    def test_only_one_free_model_leaves_no_alternative(self):
        state = team.effective({'team': {'roles': {}}}, {'pi'}, {'codex': [], 'pi': []}, FREE[:1])
        self.assertEqual(team.alternatives('simple', state['roles'], state['options']), [])

    def test_unassigned_role_has_no_alternatives(self):
        self.assertEqual(team.alternatives('review', {}, []), [])


class AlternativeSkillTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup); self.root = Path(temp.name)
        env = patch.dict(os.environ, {'HOME': str(self.root), 'XDG_CONFIG_HOME': str(self.root / 'config'),
                                      'XDG_DATA_HOME': str(self.root / 'data'), 'CLAUDE_CONFIG_DIR': str(self.root / '.claude')})
        env.start(); self.addCleanup(env.stop)
        selected = {'claude', 'pi'}
        self.state = {**team.effective({'team': {'roles': {}}}, selected, CATALOG, FREE), 'selected': sorted(selected)}
        self.paths = native_team.layout('claude')
        self.text = native_team.text(self.paths, self.root / 'settings.json', self.state)

    def section(self):
        start = self.text.index('Alternativas de Revisar')
        return self.text[start:self.text.index('```', self.text.index('```', start) + 3)]

    def test_skill_lists_read_only_commands_for_review_alternatives(self):
        section = self.section()
        self.assertIn("--model sonnet", section)
        self.assertIn('--permission-mode plan', section)
        self.assertNotIn('acceptEdits', section)

    def test_codex_alternative_lines_are_grouped_by_number(self):
        selected = {'codex-2', 'codex'}
        state = {**team.effective({'team': {'roles': {}}}, selected, {'codex': [], 'pi': []}, []), 'selected': sorted(selected)}
        text = native_team.text(self.paths, self.root / 'settings.json', state)
        self.assertIn('# 1: ', text[text.index('Alternativas de Planificar'):])

    def test_skill_treats_a_missing_model_as_a_reason_to_change(self):
        self.assertIn('modelo no encontrado', self.text)

    def test_skill_states_when_to_change_and_when_not_to(self):
        for wanted in ('reintenta una vez', 'siguiente alternativa', 'permisos denegados', 'encargo inválido',
                       'por qué se cambió', 'déjalo pendiente', 'nunca la hace el modelo'):
            self.assertIn(wanted, self.text)

    def test_startup_block_points_to_the_alternatives(self):
        block = native_team.instruction('', self.paths, True, team=self.state)
        self.assertIn('alternativas', block)


if __name__ == '__main__':
    unittest.main()

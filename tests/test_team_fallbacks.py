import copy
import itertools
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

    def review_rows(self):
        # Claude can only back up the review when another family plans.
        config = manual_config(plan=('codex', 'gpt-6.1-sol', 'high'), review=('pi', FREE[0], ''))
        state = team.effective(config, {'claude', 'codex', 'pi'}, CATALOG, FREE)
        return team.alternatives('review', state['roles'], state['options'])

    def test_review_prefers_a_strong_model_when_its_free_model_fails(self):
        self.assertEqual(models(self.review_rows())[0], ('claude', 'sonnet'))

    def test_review_never_falls_back_to_the_family_of_plan(self):
        self.assertEqual({agent for agent, _ in models(self.alternatives('review'))}, {'pi'})

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
        self.assertEqual(self.review_rows()[0]['effort'], 'high')

    def test_only_one_free_model_leaves_no_alternative(self):
        state = team.effective({'team': {'roles': {}}}, {'pi'}, {'codex': [], 'pi': []}, FREE[:1])
        self.assertEqual(team.alternatives('simple', state['roles'], state['options']), [])

    def test_unassigned_role_has_no_alternatives(self):
        self.assertEqual(team.alternatives('review', {}, []), [])


SELECTED = {'claude', 'codex', 'codex-2'}
ASTRA = {'id': 'gpt-6-astra', 'supportedReasoningEfforts': [{'reasoningEffort': effort} for effort in ('low', 'high')]}
CODEX_CATALOG = {'codex': [ASTRA, *({'id': model, 'supportedReasoningEfforts': [{'reasoningEffort': effort} for effort in ('medium', 'high')]}
                                    for model in ('gpt-6.1-sol', 'gpt-6-luna'))], 'pi': []}
COMBINATION = (('plan', 'claude', 'opus', 'high'), ('code', 'claude', 'sonnet', 'xhigh'),
               ('review', 'codex', 'gpt-6-astra', 'high'), ('simple', 'codex', 'gpt-6-luna', 'medium'))


def set_role(config, role, agent, model, effort, selected=SELECTED, catalog=CODEX_CATALOG, free_pi=()):
    team.set_role(config, role, agent, model, effort, selected=set(selected), catalog=catalog,
                  free_pi=list(free_pi), paid_ok=False)


def manual_config(**roles):
    return {'team': {'roles': {key: {'agent': agent, 'model': model, 'effort': effort, 'manual': True}
                               for key, (agent, model, effort) in roles.items()}}}


class CodexCatalogTests(unittest.TestCase):
    def test_astra_keeps_the_efforts_of_the_catalog(self):
        row = next(row for row in team.options(SELECTED, CODEX_CATALOG, [], False) if row['model'] == 'gpt-6-astra')
        self.assertEqual((row['agent'], row['efforts'], row['paid']), ('codex', ['low', 'high'], False))

    def test_astra_is_offered_without_a_catalog(self):
        self.assertIn(('codex', 'gpt-6-astra'), models(team.options(SELECTED, {}, [], False)))

    def test_astra_is_not_offered_when_the_catalog_lacks_it(self):
        catalog = {'codex': CODEX_CATALOG['codex'][1:]}
        self.assertNotIn(('codex', 'gpt-6-astra'), models(team.options(SELECTED, catalog, [], False)))

    def test_astra_is_never_recommended_by_itself(self):
        for selected in (SELECTED, {'codex'}, {'codex', 'claude', 'pi'}):
            roles, _ = team.recommend(selected, CODEX_CATALOG, FREE)
            self.assertNotIn('gpt-6-astra', [row['model'] for row in roles.values()])


class ReviewFamilyTests(unittest.TestCase):
    def test_review_in_the_family_of_plan_is_rejected(self):
        config = {'team': {'roles': {}}}
        set_role(config, 'plan', 'claude', 'opus', 'high')
        with self.assertRaisesRegex(team.TeamError, 'otra familia'):
            set_role(config, 'review', 'claude', 'sonnet', 'high')
        self.assertNotIn('review', config['team']['roles'])

    def test_plan_in_the_family_of_review_is_rejected(self):
        config = manual_config(review=('codex', 'gpt-6-astra', 'high'))
        with self.assertRaisesRegex(team.TeamError, 'otra familia'):
            set_role(config, 'plan', 'codex', 'gpt-6.1-sol', 'high')
        self.assertNotIn('plan', config['team']['roles'])

    def test_the_rejection_is_translatable(self):
        config = manual_config(review=('codex', 'gpt-6-astra', 'high'))
        with self.assertRaises(team.TeamError) as caught:
            set_role(config, 'plan', 'codex', 'gpt-6.1-sol', 'high')
        self.assertEqual(getattr(caught.exception.args[0], 'source', None), 'Revisar debe ser de otra familia que Planificar ({familia}).')

    def test_review_keeps_rejecting_the_model_of_code(self):
        config = manual_config(plan=('codex', 'gpt-6.1-sol', 'high'), code=('claude', 'sonnet', 'high'))
        with self.assertRaisesRegex(team.TeamError, 'distinto de Programar'):
            set_role(config, 'review', 'claude', 'sonnet', 'high')

    def test_set_roles_swaps_plan_and_review_atomically(self):
        config = manual_config(plan=('codex', 'gpt-6.1-sol', 'high'), review=('claude', 'opus', 'high'))
        with self.assertRaises(team.TeamError):
            set_role(config, 'plan', 'claude', 'sonnet', 'high')
        team.set_roles(config, [('plan', 'claude', 'opus', 'high'), ('review', 'codex', 'gpt-6.1-sol', 'high')],
                       selected=set(SELECTED), catalog=CODEX_CATALOG, free_pi=[], paid_ok=False)
        roles = config['team']['roles']
        self.assertEqual((roles['plan']['agent'], roles['review']['agent']), ('claude', 'codex'))

    def test_set_roles_changes_nothing_when_the_result_breaks_the_rule(self):
        config = manual_config(plan=('codex', 'gpt-6.1-sol', 'high'), review=('claude', 'opus', 'high'))
        before = copy.deepcopy(config)
        with self.assertRaisesRegex(team.TeamError, 'otra familia'):
            team.set_roles(config, [('plan', 'claude', 'opus', 'high'), ('review', 'claude', 'sonnet', 'high')],
                           selected=set(SELECTED), catalog=CODEX_CATALOG, free_pi=[], paid_ok=False)
        self.assertEqual(config, before)

    def test_one_family_warns_without_blocking(self):
        for selected, catalog, free, role_row in (
                ({'claude'}, {}, [], ('claude', 'sonnet', 'high')),
                ({'codex'}, CODEX_CATALOG, [], ('codex', 'gpt-6-astra', 'high')),
                ({'pi'}, {}, FREE, ('pi', FREE[1], ''))):
            with self.subTest(selected=selected):
                config = {'team': {'roles': {}}}
                set_role(config, 'review', *role_row, selected=selected, catalog=catalog, free_pi=free)
                warnings = ' '.join(team.effective(config, selected, catalog, free)['warnings'])
                self.assertIn('Solo hay una familia disponible', warnings)

    def test_paid_pi_does_not_count_as_another_family(self):
        catalog = {'codex': [], 'pi': [{'id': 'xai/grok'}]}
        config = {'team': {'roles': {}}}
        set_role(config, 'review', 'claude', 'sonnet', 'high', selected={'claude', 'pi'}, catalog=catalog)
        self.assertIn('Solo hay una familia disponible', ' '.join(team.effective(config, {'claude', 'pi'}, catalog, [])['warnings']))

    def test_manual_configuration_that_breaks_the_rule_warns_and_is_kept(self):
        config = manual_config(plan=('claude', 'opus', 'high'), review=('claude', 'sonnet', 'high'))
        state = team.effective(config, SELECTED, CODEX_CATALOG, [])
        self.assertEqual(state['roles']['review']['model'], 'sonnet')
        self.assertTrue(state['roles']['review']['manual'])
        self.assertIn('misma familia (Claude)', ' '.join(state['warnings']))

    def test_a_broken_configuration_does_not_block_other_roles(self):
        config = manual_config(plan=('claude', 'opus', 'high'), review=('claude', 'sonnet', 'high'))
        set_role(config, 'simple', 'codex', 'gpt-6-luna', 'medium')
        self.assertEqual(config['team']['roles']['simple']['model'], 'gpt-6-luna')

    def test_a_broken_configuration_can_be_repaired_from_review(self):
        config = manual_config(plan=('claude', 'opus', 'high'), review=('claude', 'sonnet', 'high'))
        set_role(config, 'review', 'codex', 'gpt-6-astra', 'high')
        self.assertEqual(team.effective(config, SELECTED, CODEX_CATALOG, [])['warnings'], [])

    def test_recommended_review_gives_way_to_the_plan_the_user_chose(self):
        config = {'team': {'roles': {}}}
        set_role(config, 'plan', 'codex', 'gpt-6.1-sol', 'high')
        state = team.effective(config, SELECTED, CODEX_CATALOG, [])
        self.assertEqual(state['roles']['review']['agent'], 'claude')
        self.assertFalse(state['roles']['review']['manual'])
        self.assertEqual(state['warnings'], [])
        set_role(config, 'review', 'claude', 'sonnet', 'high')
        self.assertEqual(config['team']['roles']['review']['agent'], 'claude')

    def test_recommendation_separates_review_from_plan_when_it_can(self):
        for selected, free in ((SELECTED, []), ({'codex', 'pi'}, FREE), ({'claude', 'pi'}, FREE), ({'claude', 'codex', 'pi'}, FREE)):
            with self.subTest(selected=selected):
                roles, _ = team.recommend(selected, CODEX_CATALOG, free)
                self.assertNotEqual(roles['plan']['agent'], roles['review']['agent'])
                self.assertEqual(team.effective({}, selected, CODEX_CATALOG, free)['warnings'], [])

    def test_review_alternatives_with_claude_planning_are_only_codex_or_pi(self):
        config = {'team': {'roles': {}}}
        for row in COMBINATION: set_role(config, *row, selected={*SELECTED, 'pi'}, free_pi=FREE)
        state = team.effective(config, {*SELECTED, 'pi'}, CODEX_CATALOG, FREE)
        rows = models(team.alternatives('review', state['roles'], state['options']))
        self.assertTrue(rows)
        self.assertEqual({agent for agent, _ in rows} - {'codex', 'pi'}, set())

    def test_plan_alternatives_stay_out_of_the_family_of_review(self):
        config = {'team': {'roles': {}}}
        for row in COMBINATION: set_role(config, *row)
        state = team.effective(config, SELECTED, CODEX_CATALOG, [])
        self.assertEqual({agent for agent, _ in models(team.alternatives('plan', state['roles'], state['options']))}, {'claude'})

    def test_review_without_another_family_left_has_no_alternative(self):
        config = manual_config(plan=('claude', 'opus', 'high'), code=('claude', 'sonnet', 'high'), review=('codex', 'gpt-6.1-sol', 'high'),
                               simple=('codex', 'gpt-6-luna', 'medium'))
        catalog = {'codex': [row for row in CODEX_CATALOG['codex'] if row['id'] != 'gpt-6-astra']}
        state = team.effective(config, SELECTED, catalog, [])
        self.assertEqual(team.alternatives('review', state['roles'], state['options']), [])


class CombinationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup); self.root = Path(temp.name)
        # A Codex-2 session is the one that leads: its CLI reads this home.
        env = patch.dict(os.environ, {'HOME': str(self.root), 'XDG_CONFIG_HOME': str(self.root / 'config'),
                                      'XDG_DATA_HOME': str(self.root / 'data'), 'CODEX_HOME': str(self.root / '.codex-2'),
                                      'CLAUDE_CONFIG_DIR': str(self.root / '.claude')})
        env.start(); self.addCleanup(env.stop)
        self.config = {'team': {'roles': {}}}
        for row in COMBINATION: set_role(self.config, *row)
        state = team.effective(self.config, SELECTED, CODEX_CATALOG, [])
        self.state = {**state, 'selected': sorted(SELECTED)}
        self.paths = native_team.layout('codex')

    def text(self):
        return native_team.text(self.paths, self.root / 'settings.json', self.state)

    def recipes(self, key, settings_file=None):
        return native_team.role_commands(key, self.state['roles'][key], ['codex-2', 'codex'], settings_file=settings_file)

    RUN = str(Path(native_team.__file__).resolve().parent / 'context_run.py')

    def claude(self, role, model, effort, mode):
        return (f"env -u CLAUDE_CONFIG_DIR /usr/bin/python3 {self.RUN} --agent claude --role {role} --cwd '<dir>' -- "
                f"claude -p '<encargo>' --model {model} --effort {effort} --permission-mode {mode} </dev/null")

    def codex(self, profile, sandbox, model, effort, role=None):
        role = role or ('review' if sandbox == 'read-only' else 'simple')
        return (f"CODEX_HOME={self.root / profile} /usr/bin/python3 {self.RUN} --agent {profile.lstrip('.')} --role {role} --cwd '<dir>' -- "
                f"codex exec -C '<dir>' -s {sandbox} -m {model} "
                f"-c 'model_reasoning_effort=\"{effort}\"' '<encargo>' </dev/null")

    def test_any_order_is_accepted(self):
        for order in itertools.permutations(COMBINATION):
            config = {'team': {'roles': {}}}
            for row in order: set_role(config, *row)
            roles = team.effective(config, SELECTED, CODEX_CATALOG, [])['roles']
            self.assertEqual({key: (row['agent'], row['model'], row['effort'], row['manual']) for key, row in roles.items()},
                             {key: (agent, model, effort, True) for key, agent, model, effort in COMBINATION})

    def test_no_warnings(self):
        self.assertEqual(self.state['warnings'], [])

    def test_plan_recipe_is_claude_read_only(self):
        self.assertEqual(self.recipes('plan'), [self.claude('plan', 'opus', 'high', 'plan')])

    def test_code_recipe_is_claude_with_edits(self):
        self.assertEqual(self.recipes('code'), [self.claude('code', 'sonnet', 'xhigh', 'acceptEdits')])

    def test_review_recipe_is_codex_read_only_with_codex_2_first(self):
        self.assertEqual(self.recipes('review'), [self.codex(profile, 'read-only', 'gpt-6-astra', 'high') for profile in ('.codex-2', '.codex')])

    def test_simple_recipe_is_codex_with_writes_and_codex_2_first(self):
        self.assertEqual(self.recipes('simple'), [self.codex(profile, 'workspace-write', 'gpt-6-luna', 'medium') for profile in ('.codex-2', '.codex')])

    def test_a_codex_2_session_gets_its_own_profile(self):
        self.assertEqual(self.paths['profile'], 'codex-2')

    def test_startup_block_of_a_codex_session_carries_the_read_only_review(self):
        block = native_team.instruction('', self.paths, True, team=self.state)
        self.assertIn(self.codex('.codex-2', 'read-only', 'gpt-6-astra', 'high'), block)
        for wanted in ('tú diriges', 'pide el permiso nativo', 'no cuenta como revisión independiente'):
            self.assertIn(wanted, block)

    def test_skill_of_a_codex_session_lists_every_role_and_the_codex_2_rule(self):
        text = self.text()
        for key, agent, model, effort in COMBINATION:
            self.assertIn(f'{team.LABELS[key]}: {agent} · {model} · {effort}', text)
            for recipe in self.recipes(key, self.root / 'settings.json'): self.assertIn(recipe, text)
        self.assertIn('Codex-2 primero', text)
        self.assertLess(text.index('CODEX_HOME=' + str(self.root / '.codex-2')), text.index('CODEX_HOME=' + str(self.root / '.codex') + ' /usr'))

    def test_skill_never_gives_write_permission_to_the_reviewer(self):
        self.assertNotIn('-s workspace-write -m gpt-6-astra', self.text())

    def test_skill_states_the_family_rule_for_fallbacks(self):
        self.assertIn('ni un modelo de la familia que planificó', self.text())

    def test_skill_says_when_review_has_no_alternative(self):
        catalog = {'codex': [row for row in CODEX_CATALOG['codex'] if row['id'] != 'gpt-6-astra']}
        config = manual_config(plan=('claude', 'opus', 'high'), code=('claude', 'sonnet', 'high'), review=('codex', 'gpt-6.1-sol', 'high'),
                               simple=('codex', 'gpt-6-luna', 'medium'))
        state = {**team.effective(config, SELECTED, catalog, []), 'selected': sorted(SELECTED)}
        self.assertIn('Revisar no tiene alternativa permitida', native_team.text(self.paths, self.root / 'settings.json', state))

    def test_skill_says_when_any_role_has_no_alternative(self):
        state = {**self.state, 'options': []}
        text = native_team.text(self.paths, self.root / 'settings.json', state)
        for key in team.ROLES:
            self.assertIn(f'{team.LABELS[key]} no tiene alternativa permitida: si su modelo falla, queda pendiente; dilo.', text)

    def test_skill_marks_a_review_alternative_that_is_the_leading_cli(self):
        rows = team.alternatives('review', self.state['roles'], self.state['options'])
        text = self.text()
        lead = [row for row in rows if row['agent'] == 'codex']
        other = [row for row in rows if row['agent'] != 'codex']
        self.assertTrue(lead)
        marker = 'no independiente'
        section = text[text.index('Alternativas de Revisar'):]
        section = section[:section.index('\n\n')]
        for row in lead: self.assertRegex(section, rf'# \d+: {row["model"]} \({marker}')
        for row in other: self.assertNotRegex(section, rf'# \d+: {row["model"]} \({marker}')


class OnlyPiTests(unittest.TestCase):
    A, B, C = 'nous/a:free', 'nous/b:free', 'nous/c:free'

    def state(self, free, catalog=None):
        return team.effective({'team': {'roles': {}}}, {'pi'}, catalog or {'codex': [], 'pi': []}, free)

    def test_only_pi_uses_distinct_free_models_for_code_and_review(self):
        roles, _ = team.recommend({'pi'}, {'codex': [], 'pi': []}, [self.A, self.B])
        self.assertEqual((roles['code']['model'], roles['review']['model']), (self.A, self.B))
        self.assertEqual({row['agent'] for row in roles.values()}, {'pi'})

    def test_two_models_give_no_independence_warning(self):
        warnings = ' '.join(self.state([self.A, self.B])['warnings'])
        self.assertNotIn('revisión independiente', warnings)

    def test_one_model_works_with_a_warning_about_review(self):
        state = self.state([self.A])
        self.assertEqual(state['roles']['code']['model'], self.A)
        self.assertEqual(state['roles']['review']['model'], self.A)
        self.assertIn('No hay revisión independiente', ' '.join(state['warnings']))

    def test_no_model_leaves_roles_pending(self):
        state = self.state([])
        self.assertEqual(state['roles'], {})
        self.assertIn('No hay modelos gratuitos', ' '.join(state['warnings']))

    def test_paid_models_are_never_used_even_if_the_catalog_lists_them(self):
        state = self.state([], {'codex': [], 'pi': [{'id': 'xai/grok'}]})
        self.assertEqual(state['roles'], {})
        state = self.state([self.A, self.B], {'codex': [], 'pi': [{'id': 'xai/grok'}]})
        for key in team.ROLES:
            self.assertNotIn(('pi', 'xai/grok'), models(team.alternatives(key, state['roles'], state['options'])))

    def test_alternatives_keep_code_and_review_independent(self):
        state = self.state([self.A, self.B, self.C])
        self.assertEqual(models(team.alternatives('code', state['roles'], state['options'])), [('pi', self.C)])
        self.assertEqual(models(team.alternatives('review', state['roles'], state['options'])), [('pi', self.C)])

    def test_alternatives_use_only_efforts_the_model_supports(self):
        catalog = {'codex': [], 'pi': [], 'nousEffort': [self.A]}
        state = self.state([self.B, self.C, self.A], catalog)
        rows = team.alternatives('code', state['roles'], state['options'])
        self.assertEqual((rows[0]['model'], rows[0]['effort']), (self.A, 'high'))

    def test_warning_states_the_missing_sandbox_only_when_pi_works_alone(self):
        self.assertIn('no tienen sandbox ni aprobación por herramienta', ' '.join(self.state([self.A, self.B])['warnings']))
        mixed = team.effective({'team': {'roles': {}}}, {'claude', 'pi'}, {'codex': [], 'pi': []}, [self.A])
        self.assertNotIn('no tienen sandbox', ' '.join(mixed['warnings']))
        self.assertNotIn('no tienen sandbox', ' '.join(self.state([])['warnings']))


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
        config = manual_config(plan=('codex', 'gpt-6.1-sol', 'high'), review=('pi', FREE[0], ''))
        selected = {'claude', 'codex', 'pi'}
        state = {**team.effective(config, selected, CATALOG, FREE), 'selected': sorted(selected)}
        self.text = native_team.text(self.paths, self.root / 'settings.json', state)
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

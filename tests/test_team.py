import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import team


def role(agent, model, effort=''):
    return dict(agent=agent, model=model, effort=effort)


CAT = {'codex': [dict(id=model, isDefault=i == 0, supportedReasoningEfforts=[
    {'reasoningEffort': effort} for effort in ('medium', 'high', 'xhigh')])
    for i, model in enumerate(('gpt-6.1-sol', 'gpt-6-luna'))]}


class TeamTests(unittest.TestCase):
    def test_newest_sol_and_luna_by_version(self):
        catalog = {'codex': [dict(id=model) for model in (
            'gpt-5.6-sol', 'gpt-6-sol', 'gpt-6.1-sol', 'gpt-6.2-luna', 'gpt-6-luna',
            'gpt-6.10-sol', 'gpt-99-sol-preview')]}
        roles, _ = team.recommend({'codex-2'}, catalog, [])
        self.assertEqual(roles['code']['model'], 'gpt-6.10-sol')
        self.assertEqual(roles['simple']['model'], 'gpt-6.2-luna')
        catalog['codex'] = catalog['codex'][:5]
        self.assertEqual(team.recommend({'codex'}, catalog, [])[0]['code']['model'], 'gpt-6.1-sol')

    def test_manual_role_with_vanished_model_falls_back_with_persistent_notice(self):
        for field, value in (('model', 'vanished'), ('effort', 'vanished')):
            with self.subTest(field=field):
                config = self.config()
                config['team']['roles']['plan'] = dict(
                    agent='codex', model='gpt-6.1-sol', effort='high', manual=True)
                config['team']['roles']['plan'][field] = value
                team.reconcile(config, {'codex'}, CAT, [])
                self.assertNotIn('plan', config['team']['roles'])
                self.assertIn('Planificar', ' '.join(config['team']['notices']))
                restored = copy.deepcopy(config)
                state = team.effective(restored, {'codex'}, CAT, [])
                self.assertFalse(state['roles']['plan']['manual'])
                self.assertTrue(set(config['team']['notices']).issubset(state['warnings']))
                team.set_role(restored, 'simple', 'codex', 'gpt-6-luna', 'medium',
                              selected={'codex'}, catalog=CAT, free_pi=[], paid_ok=False)
                self.assertFalse(restored['team'].get('notices'))
                team.reset(config)
                self.assertFalse(config['team'].get('notices'))

    def test_claude_and_codex(self):
        expected = dict(plan=role('codex', 'gpt-6.1-sol', 'xhigh'), code=role('claude', 'opus', 'high'),
                        review=role('codex', 'gpt-6.1-sol', 'high'), simple=role('codex', 'gpt-6-luna', 'medium'))
        for selected in ({'claude', 'codex-2'}, {'claude', 'codex'}, {'claude', 'codex', 'codex-2', 'pi'}):
            self.assertEqual(team.recommend(selected, CAT, ['nous/x:free'])[0], expected)

    def test_only_claude(self):
        self.assertEqual(team.recommend({'claude'}, CAT, [])[0],
                         {key: role('claude', model, effort) for key, model, effort in zip(team.ROLES, ('opus', 'opus', 'sonnet', 'haiku'), ('xhigh', 'high', 'high', 'medium'))})

    def test_only_codex(self):
        expected = {key: role('codex', model, effort) for key, model, effort in (
            ('plan', 'gpt-6.1-sol', 'xhigh'), ('code', 'gpt-6.1-sol', 'high'),
            ('review', 'gpt-6-luna', 'high'), ('simple', 'gpt-6-luna', 'medium'))}
        for selected in ({'codex'}, {'codex-2'}, {'codex', 'codex-2'}, {'codex', 'pi'}):
            self.assertEqual(team.recommend(selected, CAT, [])[0], expected)
        self.assertEqual(team.recommend({'codex', 'pi'}, CAT, ['nous/x:free'])[0]['simple'], role('pi', 'nous/x:free'))

    def test_claude_pi_free(self):
        for free, review, simple in ((['nous/x:free'], role('pi', 'nous/x:free'), role('pi', 'nous/x:free')),
                                     ([], role('claude', 'sonnet', 'high'), role('claude', 'haiku', 'medium'))):
            roles, _ = team.recommend({'claude', 'pi'}, CAT, free)
            self.assertEqual(roles['review'], review)
            self.assertEqual(roles['simple'], simple)

    def test_only_pi_warns(self):
        roles, warnings = team.recommend({'pi'}, CAT, ['nous/x:free'])
        self.assertTrue(all(value == role('pi', 'nous/x:free') for value in roles.values()))
        self.assertIn('revisión independiente', ' '.join(warnings))
        self.assertEqual(team.recommend({'pi'}, CAT, [])[0], {})
        self.assertEqual(team.recommend(set(), CAT, [])[0], {})

    def test_missing_catalog_uses_defaults_and_warns(self):
        roles, warnings = team.recommend({'codex-2'}, {}, [])
        self.assertTrue(all(row['model'] in team.CODEX_DEFAULTS for row in roles.values()))
        self.assertIn('catálogo', ' '.join(warnings))

    def test_model_missing_uses_cli_default_and_warns(self):
        cat = {'codex': [{'id': 'other', 'isDefault': True, 'supportedReasoningEfforts': [{'reasoningEffort': 'medium'}]}]}
        roles, warnings = team.recommend({'codex'}, cat, [])
        self.assertTrue(all(row == role('codex', 'other', 'medium') for row in roles.values()))
        self.assertIn('predeterminado', ' '.join(warnings))

    def test_claude_2_alone_selects_the_claude_family(self):
        self.assertEqual(team.families({'claude-2'}), ['claude'])
        self.assertEqual(team.families({'claude-2', 'codex-2', 'pi'}), ['claude', 'codex', 'pi'])
        roles, _ = team.recommend({'claude-2'}, {}, [])
        self.assertTrue(roles and all(row['agent'] == 'claude' for row in roles.values()))

    def test_options(self):
        cat = {**CAT, 'pi': [{'id': 'xai/grok'}]}
        options = team.options({'claude', 'codex-2', 'pi'}, cat, ['nous/x:free'], True)
        self.assertEqual(team.families({'codex-2', 'pi', 'claude'}), ['claude', 'codex', 'pi'])
        self.assertEqual({r['model'] for r in options if r['agent'] == 'claude'}, set(team.CLAUDE_MODELS))
        self.assertTrue(any('xhigh' in r['efforts'] for r in options))
        self.assertTrue(next(r for r in options if r['model'] == 'xai/grok')['paid'])
        self.assertFalse(next(r for r in options if r['model'] == 'nous/x:free')['paid'])

    def test_effective_options_expose_model_efforts(self):
        cat = {**CAT, 'pi': [{'id': 'xai/grok'}]}
        options = team.effective({}, {'claude', 'codex-2', 'pi'}, cat, ['nous/x:free'])['options']
        by_model = {(row['agent'], row['model']): row for row in options}
        self.assertEqual(len(options), len(by_model))
        self.assertEqual(by_model['claude', 'haiku']['efforts'], list(team.CLAUDE_EFFORTS))
        self.assertEqual(by_model['codex', 'gpt-6.1-sol']['efforts'], ['medium', 'high', 'xhigh'])
        self.assertEqual(by_model['pi', 'nous/x:free']['efforts'], [''])
        self.assertEqual(by_model['codex', 'gpt-6.1-sol']['label'], 'Codex · gpt-6.1-sol')
        self.assertEqual(by_model['codex', 'gpt-6.1-sol']['recommendedEfforts'],
                         dict(plan='xhigh', code='high', review='high', simple='medium'))

    def test_manual_ultra_allowed_with_warning(self):
        config = self.config()
        cat = copy.deepcopy(CAT)
        cat['codex'][0]['supportedReasoningEfforts'].append({'reasoningEffort': 'ultra'})
        for key in ('plan', 'simple'):
            team.set_role(config, key, 'codex', 'gpt-6.1-sol', 'ultra',
                          selected={'codex-2'}, catalog=cat, free_pi=[], paid_ok=False)
        result = team.effective(config, {'codex-2'}, cat, [])
        self.assertEqual(result['roles']['plan']['effort'], 'ultra')
        self.assertTrue(result['roles']['plan']['manual'])
        self.assertEqual(result['warnings'], [
            'Ultra hace que Codex reparta el trabajo por su cuenta: rompe el reparto del equipo y gasta tokens por partida doble.'])
        team.reset(config)
        self.assertEqual(team.effective(config, {'codex-2'}, cat, [])['warnings'], [])

    def config(self):
        return {'team': {'enabled': True, 'roles': {}}, 'foreign': 'keep'}

    def test_manual_kept_on_selection_change(self):
        config = self.config()
        team.set_role(config, 'plan', 'claude', 'haiku', '', selected={'claude', 'codex'}, catalog=CAT, free_pi=[], paid_ok=False)
        before = copy.deepcopy(config)
        value = team.effective(config, {'claude', 'pi'}, CAT, [])
        self.assertEqual(value['roles']['plan']['model'], 'haiku')
        self.assertTrue(value['roles']['plan']['manual'])
        self.assertEqual(value['roles']['plan']['recommended'], role('claude', 'opus', 'xhigh'))
        self.assertEqual(config, before)
        team.reset(config)
        self.assertEqual(config['team']['roles'], {})
        self.assertTrue(config['team']['enabled'])
        self.assertEqual(config['foreign'], 'keep')

    def test_manual_on_deselected_agent_falls_back(self):
        config = self.config()
        team.set_role(config, 'plan', 'claude', 'haiku', '', selected={'claude', 'codex'}, catalog=CAT, free_pi=[], paid_ok=False)
        result = team.effective(config, {'codex-2'}, CAT, [])
        self.assertEqual(result['roles']['plan']['agent'], 'codex')
        self.assertFalse(result['roles']['plan']['manual'])
        self.assertIn('seleccionado', ' '.join(result['warnings']))

    def test_review_equal_code_rejected(self):
        for changed, model in (('review', 'opus'), ('code', 'sonnet')):
            config = self.config()
            with self.assertRaisesRegex(team.TeamError, 'Revisar|revisión'):
                team.set_role(config, changed, 'claude', model, '', selected={'claude'}, catalog=CAT, free_pi=[], paid_ok=False)
            self.assertEqual(config['team']['roles'], {})

    def test_single_model_allowed_with_warning(self):
        config = self.config()
        team.set_role(config, 'review', 'pi', 'nous/x:free', '', selected={'pi'}, catalog={}, free_pi=['nous/x:free'], paid_ok=False)
        self.assertIn('revisión independiente', ' '.join(team.effective(config, {'pi'}, {}, ['nous/x:free'])['warnings']))

    def test_paid_pi_needs_consent(self):
        config = self.config(); cat = {'pi': [{'id': 'xai/grok'}]}
        with self.assertRaisesRegex(team.TeamError, 'pago'):
            team.set_role(config, 'simple', 'pi', 'xai/grok', '', selected={'pi'}, catalog=cat, free_pi=[], paid_ok=False)
        config['piPaidApproved'] = True
        team.set_role(config, 'simple', 'pi', 'xai/grok', '', selected={'pi'}, catalog=cat, free_pi=[], paid_ok=True)
        self.assertEqual(config['team']['roles']['simple']['model'], 'xai/grok')

    def test_invalid_role_model_effort_or_agent_rejected(self):
        for key, agent, model, effort in (('bad', 'claude', 'opus', ''), ('plan', 'pi', 'opus', ''),
                                         ('plan', 'claude', 'unknown', ''), ('plan', 'codex', 'gpt-6.1-sol', 'unknown')):
            with self.subTest(key=key, agent=agent, model=model), self.assertRaises(team.TeamError):
                team.set_role({}, key, agent, model, effort, selected={'claude', 'codex'}, catalog=CAT, free_pi=[], paid_ok=False)

    def test_never_recommends_max_or_ultra(self):
        cat = copy.deepcopy(CAT)
        for row in cat['codex']:
            row['defaultReasoningEffort'] = 'ultra'
            row['supportedReasoningEfforts'] = [{'reasoningEffort': effort} for effort in ('max', 'ultra', 'medium')]
        for selected in ({'claude'}, {'codex'}, {'claude', 'codex'}, {'claude', 'pi'}):
            roles, _ = team.recommend(selected, cat, [])
            for key, row in roles.items():
                self.assertNotIn(row['effort'], ('max', 'ultra'))
                if key != 'plan': self.assertNotEqual(row['effort'], 'xhigh')

    def test_effort_kept_or_reset_on_model_change(self):
        config = self.config(); selected = {'claude', 'codex'}
        cat = copy.deepcopy(CAT)
        cat['codex'].append({'id': 'medium-only', 'supportedReasoningEfforts': [{'reasoningEffort': 'medium'}]})
        def choose(agent, model, effort):
            team.set_role(config, 'simple', agent, model, effort, selected=selected, catalog=cat, free_pi=[], paid_ok=False)
            return config['team']['roles']['simple']['effort']
        self.assertEqual(choose('claude', 'haiku', 'high'), 'high')
        self.assertEqual(choose('codex', 'gpt-6.1-sol', ''), 'high')
        self.assertEqual(choose('codex', 'medium-only', 'high'), 'medium')

    def test_recommended_effort_kept_on_first_manual_model_change(self):
        config = self.config()
        cat = copy.deepcopy(CAT)
        cat['codex'][0]['supportedReasoningEfforts'] = [{'reasoningEffort': 'medium'}]
        self.assertEqual(team.effective(config, {'codex'}, cat, [])['roles']['plan']['effort'], 'medium')
        team.set_role(config, 'plan', 'codex', 'gpt-6-luna', '',
                      selected={'codex'}, catalog=cat, free_pi=[], paid_ok=False)
        self.assertEqual(config['team']['roles']['plan']['effort'], 'medium')

    def test_paid_options_visible_but_not_effective_without_consent(self):
        cat = {'pi': [{'id': 'xai/grok'}]}
        self.assertTrue(team.options({'pi'}, cat, [], False)[0]['paid'])
        config = {'team': {'roles': {'simple': dict(agent='pi', model='xai/grok', effort='', manual=True)}}}
        self.assertNotIn('simple', team.effective(config, {'pi'}, cat, [])['roles'])

    def test_single_codex_model_counts_efforts_as_one_model(self):
        cat = {'codex': [{'id': 'single', 'isDefault': True, 'supportedReasoningEfforts': [
            {'reasoningEffort': 'medium'}, {'reasoningEffort': 'high'}]}]}
        config = self.config()
        team.set_role(config, 'review', 'codex', 'single', 'high', selected={'codex-2'}, catalog=cat, free_pi=[], paid_ok=False)
        self.assertIn('revisión independiente', ' '.join(team.effective(config, {'codex-2'}, cat, [])['warnings']))

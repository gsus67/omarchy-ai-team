from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import harness
import native_pi
import native_plugins
import native_team
import team

FREE = ['nous/levels:free', 'nous/plain:free']
CATALOG = {'codex': [], 'pi': [], 'nousEffort': ['nous/levels:free']}


def row(identifier, *parameters):
    return {'id': identifier, 'name': identifier, 'context_length': 262144,
            'pricing': {'prompt': '0', 'completion': '0'}, 'top_provider': {},
            'supported_parameters': ['tools', *parameters]}


class NousEffortCatalogTests(unittest.TestCase):
    def test_catalog_lists_only_models_that_announce_effort_levels(self):
        catalog = native_pi.free_nous_catalog({'data': [
            row('levels:free', 'reasoning', 'reasoning_effort'), row('plain:free', 'reasoning'),
            row('unusable:free', 'reasoning_effort')]})
        self.assertEqual(catalog['effortModels'], ['levels:free'])

    def test_models_json_rows_keep_the_schema_pi_expects(self):
        catalog = native_pi.free_nous_catalog({'data': [row('levels:free', 'reasoning', 'reasoning_effort')]})
        self.assertEqual(set(catalog['models'][0]), {'id', 'name', 'contextWindow', 'maxTokens', 'reasoning', 'cost', 'input'})

    def test_saved_catalog_with_effort_models_is_valid(self):
        catalog = native_pi.free_nous_catalog({'data': [row('levels:free', 'reasoning', 'reasoning_effort')]})
        self.assertTrue(native_pi.provider_settings({'piNousCatalog': catalog}) is not None)

    def test_saved_catalog_from_an_earlier_version_stays_valid(self):
        catalog = native_pi.free_nous_catalog({'data': [row('levels:free', 'reasoning', 'reasoning_effort')]})
        del catalog['effortModels']
        self.assertTrue(native_pi.provider_settings({'piNousCatalog': catalog}) is not None)

    def test_saved_catalog_with_malformed_effort_models_is_rejected(self):
        catalog = native_pi.free_nous_catalog({'data': [row('levels:free', 'reasoning', 'reasoning_effort')]})
        catalog['effortModels'] = 'levels:free'
        with self.assertRaises(native_plugins.PluginError):
            native_pi.provider_settings({'piNousCatalog': catalog})

    def test_team_context_exposes_effort_models_with_their_provider(self):
        catalog = native_pi.free_nous_catalog({'data': [row('levels:free', 'reasoning', 'reasoning_effort')]})
        _, context, _ = harness.team_context({'agents': {'pi': True}, 'piNousCatalog': catalog})
        self.assertEqual(context['nousEffort'], ['nous/levels:free'])


class NousEffortTeamTests(unittest.TestCase):
    def efforts(self, model):
        options = team.options({'pi'}, CATALOG, FREE, False)
        return next(option['efforts'] for option in options if option['model'] == model)

    def test_only_models_with_levels_offer_effort(self):
        self.assertEqual(self.efforts('nous/levels:free'), ['low', 'medium', 'high'])
        self.assertEqual(self.efforts('nous/plain:free'), [''])

    def test_recommendation_uses_the_role_effort_when_the_model_has_levels(self):
        roles, _ = team.recommend({'claude', 'pi'}, CATALOG, FREE)
        self.assertEqual((roles['review']['effort'], roles['simple']['effort']), ('high', 'medium'))

    def test_recommendation_stays_empty_when_the_model_has_no_levels(self):
        roles, _ = team.recommend({'claude', 'pi'}, CATALOG, ['nous/plain:free'])
        self.assertEqual((roles['review']['effort'], roles['simple']['effort']), ('', ''))

    def test_manual_role_saved_before_the_model_had_levels_is_kept(self):
        config = {'team': {'roles': {'review': {'agent': 'pi', 'model': 'nous/levels:free', 'effort': '', 'manual': True}}}}
        result = team.effective(config, {'claude', 'pi'}, CATALOG, FREE)
        self.assertTrue(result['roles']['review']['manual'])
        self.assertEqual(result['roles']['review']['effort'], 'high')
        self.assertEqual(result['warnings'], [])

    def test_manual_role_on_a_model_that_lost_its_levels_is_kept(self):
        config = {'team': {'roles': {'review': {'agent': 'pi', 'model': 'nous/plain:free', 'effort': 'high', 'manual': True}}}}
        result = team.effective(config, {'claude', 'pi'}, CATALOG, FREE)
        self.assertTrue(result['roles']['review']['manual'])
        self.assertEqual(result['roles']['review']['effort'], '')

    def test_paid_pi_models_do_not_gain_effort(self):
        options = team.options({'pi'}, {**CATALOG, 'pi': [{'id': 'xai/grok'}]}, FREE, True)
        self.assertEqual(next(option['efforts'] for option in options if option['model'] == 'xai/grok'), [''])

    def test_chosen_level_is_kept(self):
        config = {'team': {'roles': {}}}
        team.set_role(config, 'review', 'pi', 'nous/levels:free', 'low',
                      selected={'claude', 'pi'}, catalog=CATALOG, free_pi=FREE, paid_ok=False)
        self.assertEqual(config['team']['roles']['review']['effort'], 'low')

    def test_unsupported_level_is_rejected(self):
        with self.assertRaises(team.TeamError):
            team.set_role({'team': {'roles': {}}}, 'review', 'pi', 'nous/plain:free', 'medium',
                          selected={'claude', 'pi'}, catalog=CATALOG, free_pi=FREE, paid_ok=False)


class NousEffortCommandTests(unittest.TestCase):
    def test_command_passes_the_level_to_pi(self):
        command = native_team.role_commands('review', {'agent': 'pi', 'model': 'nous/levels:free', 'effort': 'high'}, [])[0]
        self.assertIn('--thinking high', command)

    def test_command_stays_the_same_without_a_level(self):
        command = native_team.role_commands('review', {'agent': 'pi', 'model': 'nous/plain:free', 'effort': ''}, [])[0]
        self.assertNotIn('--thinking', command)


if __name__ == '__main__':
    unittest.main()

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


class LanguageTests(unittest.TestCase):
    def test_system_resolution_and_precedence(self):
        import i18n
        for locale, expected in [('es_MX.UTF-8', 'es'), ('en_US.UTF-8', 'en'), ('fr_FR.UTF-8', 'en'), ('', 'en')]:
            with self.subTest(locale=locale):
                self.assertEqual(i18n.resolve('system', {'LANG': locale}), expected)
        self.assertEqual(i18n.resolve('system', {'LC_ALL': 'en_US', 'LC_MESSAGES': 'es_MX', 'LANG': 'es_ES'}), 'en')
        self.assertEqual(i18n.resolve('system', {'LC_ALL': '', 'LC_MESSAGES': 'es_MX', 'LANG': 'en_US'}), 'es')
        self.assertEqual(i18n.resolve('es', {'LC_ALL': 'en_US'}), 'es')
        self.assertEqual(i18n.resolve('en', {'LC_ALL': 'es_MX'}), 'en')

    def test_lookup_templates_fallback_and_user_values(self):
        import i18n
        self.assertEqual(i18n.lookup('Idioma', 'en'), 'Language')
        self.assertEqual(i18n.lookup('Idioma', 'es'), 'Idioma')
        self.assertEqual(i18n.lookup('Clave ausente {nombre}', 'en', nombre='Ana {x}'), 'Clave ausente Ana {x}')
        self.assertEqual(i18n.lookup('Última comprobación: {fecha}', 'en', fecha='2026-10-30'), 'Last check: 2026-10-30')
        self.assertEqual(i18n.localize({'user': 'Idioma', 'name': 'Falta'}, 'en'), {'user': 'Idioma', 'name': 'Falta'})

    def test_set_language_status_and_persistence(self):
        import harness
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'HOME': temp, 'XDG_CONFIG_HOME': temp + '/config', 'PATH': '', 'LANG': 'fr_FR', 'LC_ALL': '', 'LC_MESSAGES': ''}, clear=True):
            config = harness.load_config()
            self.assertEqual(harness.status(config)['language'], 'en')
            self.assertEqual(harness.status(config)['languagePreference'], 'system')
            for language, label in [('es', 'Idioma'), ('en', 'Language'), ('system', 'Language')]:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(harness.main(['set-language', language]), 0)
                reply = json.loads(output.getvalue())
                self.assertEqual(harness.load_config()['language'], language)
                self.assertEqual(reply['data']['languagePreference'], language)
                self.assertEqual(reply['data']['translations'].get('Idioma', 'Idioma'), label)
                self.assertEqual(next(row['name'] for row in reply['data']['components'] if row['id'] == 'local-tools'),
                                 'Herramientas locales' if language == 'es' else 'Local tools')
                self.assertEqual(reply['data']['summary']['text'], 'Selecciona un agente' if language == 'es' else 'Select an agent')
            path = harness.config_path()
            original = path.read_text()
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                harness.main(['set-language', 'fr'])
            self.assertEqual(path.read_text(), original)

    def test_system_tools_have_distinct_status_and_no_uninstall(self):
        import local_catalog
        tool = {'installation': 'installed', 'binary': '/usr/bin/rg', 'version': '15.1', 'source': 'system', 'probeVerified': True}
        with patch.object(local_catalog.native_tools, 'mise_inventory', return_value=({}, '')), patch.object(local_catalog, 'inspect', return_value=tool):
            rows = local_catalog.snapshot({'schemaVersion': 1})
            rg = next(row for row in rows if row['id'] == 'rg')
            self.assertEqual(rg['status'], 'Del sistema')
            self.assertEqual(rg['ownershipDetail'], 'No se puede desinstalar aquí: es del sistema y otros programas pueden necesitarla')
            self.assertEqual(rg['uninstallItems'], [])
            owned = next(row for row in local_catalog.snapshot({'catalogInstalls': {'rg': {'deps': []}}}) if row['id'] == 'rg')
            self.assertNotEqual(owned['status'], 'Del sistema')

    def test_all_visible_sources_have_translations_without_orphans(self):
        import translation_guard
        self.assertEqual(translation_guard.check(ROOT), [])

    def test_guard_rejects_new_qml_backend_text_and_orphans(self):
        import translation_guard
        self.assertTrue(translation_guard.unwrapped('Text { text: "New visible label" }', 'qml'))
        self.assertTrue(translation_guard.unwrapped('raise ValueError("New backend error")', 'py'))
        self.assertTrue(translation_guard.unwrapped('message = "New backend message"', 'py'))
        self.assertIn('missing: Nuevo texto', translation_guard.compare({'Nuevo texto'}, {}))
        self.assertIn('orphan: Obsoleto', translation_guard.compare(set(), {'Obsoleto': 'Old'}))

    def test_error_response_for_broken_config_is_localized_and_preserves_file(self):
        import harness
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'XDG_CONFIG_HOME': temp, 'LANG': 'en_US', 'LC_ALL': '', 'LC_MESSAGES': ''}):
            path = Path(temp) / 'omarchy-ai-team/settings.json'
            path.parent.mkdir()
            path.write_text('{bad')
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(harness.main(['status']), 1)
            self.assertEqual(json.loads(output.getvalue())['error'], 'The configuration could not be read; it has been preserved unchanged.')
            self.assertEqual(path.read_text(), '{bad')

    def test_context_is_restored_and_messages_can_be_copied(self):
        import copy
        import i18n
        before = i18n.current_language()
        with i18n.using('en'):
            message = copy.deepcopy(i18n.tr('Idioma'))
            self.assertEqual(str(message), 'Language')
        self.assertEqual(i18n.current_language(), before)
        self.assertEqual(i18n.localize(message, 'es'), 'Idioma')

    def test_saved_backend_messages_switch_but_user_fields_stay_intact(self):
        import harness
        original = {'agents': [{'name': 'Idioma', 'id': 'Idioma'}],
                    'team': {'warnings': ['No hay revisión independiente: Programar y Revisar usan el mismo modelo.']},
                    'memory': {'backupState': {'lastError': 'La configuración no puede ser un enlace simbólico.'}},
                    'aiCheck': {'results': [{'failures': ['No se ve la skill test-skill.']} ]}}
        with patch.object(harness, '_status', return_value=original):
            english = harness.status({'language': 'en'})
            self.assertEqual(english['agents'], original['agents'])
            self.assertEqual(english['memory']['backupState']['lastError'], 'The configuration cannot be a symbolic link.')
            self.assertEqual(english['aiCheck']['results'][0]['failures'], ['The test-skill skill is not visible.'])
            self.assertIn('independent review', english['team']['warnings'][0])
            with patch.object(harness, '_status', return_value=english):
                spanish = harness.status({'language': 'es'})
            self.assertEqual(spanish['memory']['backupState']['lastError'], original['memory']['backupState']['lastError'])
            self.assertEqual(spanish['team']['warnings'], original['team']['warnings'])

    def test_guard_covers_single_quoted_qml_and_ascii_status(self):
        import translation_guard
        self.assertTrue(translation_guard.unwrapped("Text { text: 'Etiqueta nueva' }", 'qml'))
        self.assertTrue(translation_guard.unwrapped('CompactRow { status: "Listo" }', 'qml'))

    def test_agent_instructions_and_team_digest_ignore_panel_language(self):
        import ai_check
        import i18n
        import local_catalog
        import native_team
        import team
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        settings = Path(temporary.name) / 'settings.json'
        paths = {'profile': 'codex-2', 'source': Path(temporary.name) / 'skill'}
        results = []
        for language in ('es', 'en'):
            with i18n.using(language):
                state = team.effective({'team': {'enabled': True}}, ['codex-2'], {}, [])
                results.append((native_team.text(paths, settings, team=state),
                                native_team.digest(paths, settings, team=state),
                                ai_check.prompt_for({'tools': False, 'memory': False, 'codeModel': ''}, 'marker'),
                                [row['usage'] for row in local_catalog.CATALOG.values()]))
        self.assertEqual(results[0], results[1])

    def test_guard_covers_backend_display_helpers_and_single_quoted_sources(self):
        import translation_guard
        self.assertTrue(translation_guard.unwrapped('def detail():\n    return "Fallo nuevo"', 'py'))

    def test_saved_team_notice_translates_role_but_preserves_user_model(self):
        import i18n
        import team
        source = '{papel}: el agente, modelo o esfuerzo ya no está disponible; se usa el recomendado.'
        for language, role, target, expected in [('es', 'Programar', 'en', 'Code:'), ('en', 'Programar', 'es', 'Programar:')]:
            with i18n.using(language):
                text = json.loads(json.dumps(i18n.tr(source, papel=i18n.tr(role))))
            self.assertTrue(i18n.saved_message(text, target).startswith(expected))
        self.assertEqual(i18n.saved_message('El modelo Idioma no aparece en el catálogo; se usa el predeterminado Calidad.', 'en'),
                         'Model Idioma is not in the catalog; using the default Calidad.')

    def test_standalone_helpers_resolve_saved_preference_and_system(self):
        import i18n
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'XDG_CONFIG_HOME': temp, 'LC_ALL': '', 'LC_MESSAGES': '', 'LANG': 'en_US'}):
            path = Path(temp) / 'omarchy-ai-team/settings.json'
            path.parent.mkdir()
            path.write_text('{"language":"es"}')
            self.assertEqual(i18n.configured_language(), 'es')
            path.write_text('{bad')
            self.assertEqual(i18n.configured_language(), 'en')

    def test_guard_rejects_lowercase_single_word_at_visible_sinks(self):
        import translation_guard
        for source, language in [('Text { text: "hola" }', 'qml'), ('message = "hola"', 'py')]:
            with self.subTest(language=language):
                self.assertTrue(translation_guard.unwrapped(source, language))

    def test_component_status_translates_saved_error_before_joining_live_detail(self):
        import harness
        import i18n
        error = 'La configuración no puede ser un enlace simbólico.'
        live = 'Instalado; falta comprobar la carga nativa.'
        agent = {'id': 'codex', 'name': 'Codex', 'binary': '/fixture/codex', 'version': '1',
                 'installation': 'installed', 'enabled': True}
        for language, stored_language in [('en', 'es'), ('es', 'en')]:
            with self.subTest(language=language), i18n.using(language), patch.object(harness.native_plugins, 'inspect',
                    side_effect=lambda *args, **kwargs: {'verified': False, 'enabled': False,
                                                        'installed': True, 'detail': i18n.tr(live)}):
                config = {'superpowers': {'codex': {'lastError': i18n.lookup(error, stored_language)}}}
                result = harness.component_status('superpowers', config, [agent])
                self.assertEqual(result['targets'][0]['detail'],
                                 i18n.lookup(error, language) + '\n' + i18n.lookup(live, language))

    def test_component_error_prefix_translates_own_component_name(self):
        import harness
        import i18n
        source = 'La configuración no puede ser un enlace simbólico.'
        def blocked(config, key, action, **options):
            if key == 'memory':
                raise harness.HarnessError(i18n.tr(source))
        with i18n.using('en'), patch.object(harness, 'manage_component', side_effect=blocked), patch.object(harness, 'sync_launcher_menu'):
            with self.assertRaises(harness.HarnessError) as error:
                harness.select_agents({'agents': {}}, set(), reconcile=True)
            self.assertEqual(str(error.exception), i18n.lookup('Memoria compartida · ECC', 'en') + ': ' + i18n.lookup(source, 'en'))


    def test_guard_covers_backend_metadata_labels(self):
        import translation_guard
        for source in ('MANAGED = {"tools": ("Herramientas nuevas", object())}',
                       'GOALS = (("id", "hola", "component"),)',
                       'LABELS = dict(zip(ROLES, ("Hola",)))'):
            with self.subTest(source=source):
                self.assertTrue(translation_guard.unwrapped(source, 'py'))

    def test_remaining_catalog_notification_and_fallback_copy_is_english(self):
        import ai_check
        import i18n
        import local_catalog
        import memory_archive
        import memory_backups
        expected = {'jq': 'Extract only the required fields from JSON.',
                    'yq': 'The same as jq for YAML and TOML (Docker, CI, settings).',
                    'shellcheck': 'Detect common errors in shell scripts.',
                    'osv-scanner': 'Warn about vulnerable dependencies (Python, npm, Go…).'}
        for key, text in expected.items():
            self.assertEqual(i18n.localize(local_catalog.BRIEFS[key], 'en'), text)
        with i18n.using('en'):
            result = ai_check.run({}, {'agents': [{'id': 'pi', 'name': 'Pi', 'enabled': True, 'installation': 'installed'}]})
            self.assertEqual(result['results'][0]['warnings'], ['Pi skipped: no verified free model is available.'])
            with patch.object(memory_backups.shutil, 'which', return_value='/fixture/notify'), patch.object(memory_backups.subprocess, 'run') as notify:
                memory_backups.notify_failure()
            self.assertEqual(notify.call_args.args[0][3], 'Memory backup pending')
            with tempfile.TemporaryDirectory() as temp:
                store = Path(temp)
                document = store / 'projects' / ('a' * 64) / 'project/notes/mem_test.md'
                document.parent.mkdir(parents=True)
                document.write_text('fixture')
                self.assertEqual(memory_archive.inventory(store)[0]['label'], 'Project aaaaaaaaaa')

    def test_guard_covers_message_assignments_and_backend_message_lists(self):
        import translation_guard
        for source, kind in [('message = "hola"', 'qml'), ('root.errorText = "hola"', 'qml'),
                             ('row.update(warnings=["hola"])', 'py'), ('BRIEFS = {"jq": "hola"}', 'py')]:
            with self.subTest(source=source):
                self.assertTrue(translation_guard.unwrapped(source, kind))

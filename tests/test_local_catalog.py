import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import call, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import local_catalog as catalog
import native_local_tools as bridge
import native_plugins
import native_skills
import project_scope
import harness
import contextlib
import io


class LocalCatalogTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        env=patch.dict(os.environ,{'HOME':str(self.root),'XDG_CONFIG_HOME':str(self.root/'config'),
                                  'XDG_DATA_HOME':str(self.root/'data'),'CLAUDE_CONFIG_DIR':str(self.root/'.claude')})
        env.start();self.addCleanup(env.stop)
        self.config={'schemaVersion':1,'agents':{},'foreign':'keep'}
        self.project=self.root/'project';self.project.mkdir()
        self.settings=self.root/'config/omarchy-ai-team/settings.json'

    def binary(self,version='ripgrep 15.2.0',code=0):
        file=self.root/'rg';file.write_text('#!/bin/sh\nprintf "%s\\n" '+repr(version)+'\nexit '+str(code)+'\n');file.chmod(0o700)
        return file

    def test_project_selectors_inherit_without_touching_other_preferences(self):
        scope=project_scope.project_key(self.project)
        catalog.set_preference(self.config,'rg','default','off')
        catalog.set_preference(self.config,'rg',scope,'on')
        self.assertEqual(catalog.preference(self.config,'rg',scope),'on')
        self.assertEqual(catalog.preference(self.config,'rg'),'off')
        catalog.set_preference(self.config,'rg',scope,'inherit')
        self.assertEqual(catalog.preference(self.config,'rg',scope),'off')
        self.assertEqual(catalog.preference(self.config,'jq',scope),'auto')
        self.assertEqual(self.config['foreign'],'keep')
        for invalid in ('../project','',None):
            with self.assertRaises(native_plugins.PluginError):catalog.set_preference(self.config,'rg',invalid,'on')
        with self.assertRaises(catalog.CatalogError):catalog.validate({'localToolsSettings':{'defaults':{'unknown':'on'}}})

    def test_detection_never_installs_and_requires_both_probe_and_native_bridge(self):
        binary=self.binary()
        with patch.object(catalog.native_tools,'mise_inventory',return_value=({},'')), patch.object(catalog.native_tools.shutil,'which',side_effect=lambda name:str(binary) if name=='rg' else None), patch.object(catalog.subprocess,'run') as run, patch.object(catalog.native_tools,'ensure_mise') as install:
            rows=catalog.snapshot(self.config)
        self.assertEqual(next(r for r in rows if r['id']=='rg')['installation'],'installed')
        self.assertFalse(any(r['available'] for r in rows));run.assert_not_called();install.assert_not_called()
        with patch.object(catalog.native_tools.shutil,'which',side_effect=lambda name:str(binary) if name=='rg' else None):
            receipt=catalog.probe('rg',{})
            self.config['localToolsSettings']={'probes':{'rg':receipt}}
            with patch.object(catalog.native_tools,'mise_inventory',return_value=({},'')):
                self.assertFalse(catalog.context(self.config,self.project)['tools'])
                self.assertEqual([r['id'] for r in catalog.context(self.config,self.project,True)['tools']],['rg'])
                catalog.set_preference(self.config,'rg',project_scope.project_key(self.project),'off')
                self.assertFalse(catalog.context(self.config,self.project,True)['tools'])
            self.assertTrue(binary.exists())

    def test_probe_finds_helpers_next_to_the_binary(self):
        # semgrep's wrapper execs osemgrep from its own bin directory by name.
        tools=self.root/'install/bin';tools.mkdir(parents=True)
        helper=tools/'rg-helper';helper.write_text('#!/bin/sh\necho "ripgrep 15.2.0"\n');helper.chmod(0o700)
        binary=tools/'rg';binary.write_text('#!/bin/sh\nexec rg-helper\n');binary.chmod(0o700)
        with patch.object(catalog.native_tools.shutil,'which',return_value=str(binary)), \
             patch.dict(os.environ,{'PATH':'/usr/bin:/bin'}):
            self.assertEqual(catalog.probe('rg',{})['version'],'15.2.0')

    def test_changed_binary_or_failed_version_invalidates_previous_probe(self):
        binary=self.binary()
        with patch.object(catalog.native_tools.shutil,'which',return_value=str(binary)):
            receipt=catalog.probe('rg',{});self.assertTrue(catalog.inspect('rg',{},receipt)['probeVerified'])
            self.binary('unrecognized version')
            self.assertFalse(catalog.inspect('rg',{},receipt)['probeVerified'])
            with self.assertRaises(catalog.CatalogError):catalog.probe('rg',{})
            self.binary('ripgrep 15.2.0',1)
            with self.assertRaises(catalog.CatalogError):catalog.probe('rg',{})
        with patch.object(catalog.native_tools,'ensure_mise') as install:
            with self.assertRaises(catalog.CatalogError):catalog.install('git')
            install.assert_not_called()

    def test_native_skill_enable_verify_disable_preserves_foreign_instructions_and_tools(self):
        home=self.root/'.claude';home.mkdir();startup=home/'CLAUDE.md';startup.write_text('Keep my project rules.\n')
        with patch.object(native_skills,'loaded',return_value=True) as loaded:
            receipt=bridge.apply('claude','/native/claude','enable',settings_file=self.settings)
        loaded.assert_called_once()
        state=bridge.inspect('claude',receipt,settings_file=self.settings)
        self.assertTrue(state['verified']);self.assertIn('Keep my project rules.',startup.read_text())
        paths=bridge.layout('claude');body=(paths['source']/'SKILL.md').read_bytes()
        self.assertTrue(paths['target'].is_symlink())
        bridge.apply('claude','/native/claude','disable',receipt,settings_file=self.settings)
        self.assertFalse(paths['target'].exists());self.assertEqual(startup.read_text(),'Keep my project rules.\n')
        self.assertEqual((paths['source']/'SKILL.md').read_bytes(),body)

    def test_skill_text_follows_enabled_groups(self):
        paths=bridge.layout('claude')
        security=('gitleaks detect --no-banner --redact -v --source .',
                  'osv-scanner scan --recursive .', 'shellcheck -f gcc <scripts>',
                  'ruff check --select S --output-format concise <py>', 'hadolint <Dockerfile>')
        tokens=('rg -n -m 40 PATTERN', 'yq <expr> file', 'jq -c <expr> file',
                'ast-grep run')
        for groups in ((),('security',),('tokens',),('security','tokens')):
            with self.subTest(groups=groups):
                skill=bridge.text(paths,self.settings,groups)
                description=json.loads(skill.split('description: ',1)[1].split('\n',1)[0])
                body=skill.split('---\n',2)[2]
                self.assertLessEqual(len(description),300)
                for group,trigger,commands in (('security','revisar seguridad',security),
                                               ('tokens','antes de leer archivos completos',tokens)):
                    self.assertEqual(trigger in description,group in groups)
                    for command in commands:
                        self.assertEqual(command in body,group in groups,command)
                self.assertIn('Opcional',body)
                self.assertIn('catalog-context',body)
                self.assertIn('Desactivada',body)
                self.assertIn('Nunca imprimas valores secretos',body)
                self.assertIn('estados de salida',body)
                self.assertNotIn('consulta las capacidades mediante',body)
                if groups:
                    self.assertLess(body.index('```sh'),body.index('catalog-context'))
                if 'security' in groups:
                    self.assertIn('semgrep solo si está instalado',body)
                    self.assertIn('sin login ni subidas',body)

    def test_startup_instruction_triggers_only_enabled_groups(self):
        paths=bridge.layout('claude');original='Keep my project rules.\n'
        for groups in ((),('security',),('tokens',),('security','tokens')):
            with self.subTest(groups=groups):
                startup=bridge.instruction(original,paths,True,groups=groups)
                self.assertIn(str(paths['source']/'SKILL.md'),startup)
                for phrase in ('antes de terminar un cambio de código','revisión de seguridad',
                               'archivos modificados o pertinentes','Informa los hallazgos',
                               'gitleaks','osv-scanner','shellcheck','ruff check --select S','hadolint'):
                    self.assertEqual(phrase in startup,'security' in groups,phrase)
                for phrase in ('antes de leer archivos completos','rg, yq, jq o ast-grep','salidas acotadas'):
                    self.assertEqual(phrase in startup,'tokens' in groups,phrase)
                self.assertIn('indicación, no un bloqueo',startup)
                self.assertEqual(bridge.instruction(startup,paths,True,groups=groups),startup)
                self.assertEqual(bridge.instruction(startup,paths,False,groups=groups),original)
        self.assertEqual(bridge.instruction(original,paths,True,groups=('tokens','security')),
                         bridge.instruction(original,paths,True,groups=('security','tokens','security')))

    def test_skill_and_digest_are_deterministic_for_the_same_enabled_groups(self):
        paths=bridge.layout('claude')
        for render in (bridge.text,bridge.digest):
            with self.subTest(render=render.__name__):
                both=render(paths,self.settings,groups=('tokens','security'))
                self.assertEqual(both,render(paths,self.settings,groups=('tokens','security')))
                self.assertEqual(both,render(paths,self.settings,groups=('security','tokens','security')))
                self.assertNotEqual(both,render(paths,self.settings,groups=('tokens',)))

    def test_group_change_invalidates_verified_skill(self):
        home=self.root/'.claude';home.mkdir();startup=home/'CLAUDE.md';startup.write_text('Keep my project rules.\n')
        with patch.object(native_skills,'loaded',return_value=True):
            receipt=bridge.apply('claude','/native/claude','enable',settings_file=self.settings,groups=('tokens',))
        self.assertTrue(bridge.inspect('claude',receipt,settings_file=self.settings,groups=('tokens',))['verified'])
        self.assertFalse(bridge.inspect('claude',receipt,settings_file=self.settings,groups=('tokens','security'))['verified'])
        self.assertNotIn('gitleaks',startup.read_text())
        groups=('tokens','security')
        bridge.prepare(bridge.layout('claude'),self.settings,groups=groups)
        receipt['digest']=bridge.digest(bridge.layout('claude'),self.settings,groups=groups)
        # Even an up-to-date skill and receipt cannot verify an obsolete startup block.
        self.assertFalse(bridge.inspect('claude',receipt,settings_file=self.settings,groups=groups)['verified'])
        with patch.object(native_skills,'loaded',return_value=True):
            receipt=bridge.apply('claude','/native/claude','verify',receipt,settings_file=self.settings,groups=groups)
        self.assertIn('gitleaks',startup.read_text())
        self.assertTrue(bridge.inspect('claude',receipt,settings_file=self.settings,groups=groups)['verified'])

    def test_component_options_include_enabled_local_tool_groups(self):
        self.config.update(tokensEnabled=True,securityEnabled=False)
        self.assertEqual(harness.component_options('superpowers',self.config),{})
        self.assertEqual(harness.component_options('local-tools',self.config),
                         {'settings_file':str(harness.config_path()),'groups':('tokens',)})

    def test_group_tools_have_short_briefs(self):
        with patch.object(catalog.native_tools,'mise_inventory',return_value=({},'')):
            rows={row['id']:row for row in catalog.snapshot(self.config)}
        for key in (*catalog.GROUPS['tokens'],*catalog.GROUPS['security']):
            self.assertTrue(rows[key]['brief'])
            self.assertLessEqual(len(rows[key]['brief']),80)
        self.assertEqual(rows['git']['brief'],'')
        for key, brief in {
                'gitleaks':'Detecta contraseñas, tokens y claves en el código y en Git.',
                'osv-scanner':'Avisa de dependencias vulnerables (Python, npm, Go…).',
                'hadolint':'Revisa errores y malas prácticas en Dockerfiles.',
                'semgrep':'Seguridad del código en TS, Go y Python. Pesado: instala uv.'}.items():
            with self.subTest(key=key):
                self.assertIn(key,rows)
                self.assertEqual(rows[key]['brief'],brief)

    def test_new_security_tools_probe_versions_and_expose_metadata(self):
        expected = (
            ('gitleaks', 'Gitleaks', 'MIT', 'https://github.com/gitleaks/gitleaks', False),
            ('osv-scanner', 'OSV-Scanner', 'Apache-2.0', 'https://github.com/google/osv-scanner', False),
            ('hadolint', 'hadolint', 'GPL-3.0-only', 'https://github.com/hadolint/hadolint', False),
            ('semgrep', 'Semgrep', 'LGPL-2.1-only', 'https://github.com/semgrep/semgrep', True))
        for key, name, license, source, optional in expected:
            with self.subTest(key=key):
                self.assertIn(key,catalog.CATALOG)
                binary=self.binary(name+' 1.2.3')
                with patch.object(catalog.native_tools.shutil,'which',side_effect=lambda n:str(binary) if n==key else None), patch.object(catalog.native_tools,'mise_inventory',return_value=({},'')):
                    receipt=catalog.probe(key,{})
                    self.config.setdefault('localToolsSettings',{}).setdefault('probes',{})[key]=receipt
                    row=next(row for row in catalog.snapshot(self.config,bridge_verified=True) if row['id']==key)
                self.assertEqual(receipt['version'],'1.2.3')
                self.assertEqual((row['name'],row['category'],row['license'],row['source']),
                                 (name,'Seguridad',license,source))
                self.assertTrue(row['available']);self.assertTrue(row['installSupported'])
                self.assertIs(row['optional'],optional)
                self.assertTrue(row['purpose'])

    def test_snapshot_marks_only_semgrep_optional(self):
        with patch.object(catalog.native_tools,'mise_inventory',return_value=({},'')):
            rows=catalog.snapshot(self.config)
        self.assertTrue(all('optional' in row for row in rows))
        self.assertEqual({row['id'] for row in rows if row['optional']},{'semgrep'})

    def test_install_security_tools_with_mise_and_uv_before_semgrep(self):
        for key, calls in (
                ('gitleaks', [call('gitleaks','gitleaks')]),
                ('osv-scanner', [call('osv-scanner','osv-scanner')]),
                ('hadolint', [call('hadolint','hadolint')]),
                ('semgrep', [call('uv','uv'),call('semgrep','semgrep')])):
            self.assertIn(key,catalog.CATALOG)
            with self.subTest(key=key), patch.object(catalog.native_tools,'mise_inventory',return_value=({},'')), patch.object(catalog.native_tools,'ensure_mise',return_value={'installation':'installed'}) as ensure:
                self.assertEqual(catalog.install(key),{'installation':'installed'})
                self.assertEqual(ensure.call_args_list,calls)

    def test_failed_uv_prevents_semgrep_install(self):
        self.assertIn('semgrep',catalog.CATALOG)
        with patch.object(catalog.native_tools,'mise_inventory',return_value=({},'')), patch.object(catalog.native_tools,'ensure_mise',side_effect=native_plugins.PluginError('uv pendiente')) as ensure:
            with self.assertRaisesRegex(native_plugins.PluginError,'uv pendiente'):
                catalog.install('semgrep')
        ensure.assert_called_once_with('uv','uv')

    def test_security_context_includes_scoped_safe_usage(self):
        expected = {'gitleaks': ('gitleaks', 'detect/git', 'valores secretos'),
                    'osv-scanner': ('osv-scanner scan --recursive', 'lockfiles'),
                    'hadolint': ('hadolint', 'Dockerfiles modificados'),
                    'semgrep': ('semgrep scan', 'locales', 'explícitas', 'sin login', 'subidas'),
                    'ruff': ('ruff check --select S',)}
        for key, phrases in expected.items():
            with self.subTest(key=key):
                self.assertIn(key,catalog.CATALOG)
                binary=self.binary('1.2.3' if key!='ruff' else 'ruff 1.2.3')
                with patch.object(catalog.native_tools.shutil,'which',side_effect=lambda n:str(binary) if n==key else None), patch.object(catalog.native_tools,'mise_inventory',return_value=({},'')):
                    receipt=catalog.probe(key,{})
                    config={'localToolsSettings':{'probes':{key:receipt}}}
                    tools=catalog.context(config,self.project,True)['tools']
                self.assertEqual([tool['id'] for tool in tools],[key])
                for phrase in phrases:self.assertIn(phrase,tools[0]['usage'])

    def test_foreign_or_edited_skill_is_not_overwritten_and_failed_loader_stays_pending(self):
        paths=bridge.layout('claude');paths['target'].mkdir(parents=True);foreign=paths['target']/'SKILL.md';foreign.write_text('foreign')
        with self.assertRaises(native_plugins.PluginError):bridge.apply('claude','/native/claude','enable',settings_file=self.settings)
        self.assertEqual(foreign.read_text(),'foreign');foreign.unlink();paths['target'].rmdir()
        with patch.object(native_skills,'loaded',return_value=False):
            with self.assertRaises(native_plugins.PluginError):bridge.apply('claude','/native/claude','enable',settings_file=self.settings)
        self.assertFalse(bridge.inspect('claude',settings_file=self.settings)['verified'])
        skill=paths['source']/'SKILL.md';skill.write_text('user edit')
        with patch.object(native_skills,'loaded',return_value=True):
            with self.assertRaises(native_plugins.PluginError):bridge.apply('claude','/native/claude','enable',settings_file=self.settings)
        self.assertEqual(skill.read_text(),'user edit')

    def test_metadata_query_has_no_prompt_and_suppresses_hooks_mcp_and_persistence(self):
        response={'type':'control_response','response':{'request_id':'ai-team-metadata','subtype':'success','response':{'commands':[{'name':bridge.ID}]}}}
        with patch.object(native_skills.subprocess,'run',return_value=subprocess.CompletedProcess([],0,json.dumps(response),'') ) as run:
            self.assertEqual(native_skills.claude_commands('/native/claude'),{bridge.ID})
        args=run.call_args.args[0];request=json.loads(run.call_args.kwargs['input'])
        self.assertEqual(request['type'],'control_request');self.assertEqual(request['request'],{'subtype':'initialize'})
        self.assertEqual(json.loads(args[args.index('--settings')+1]),{'disableAllHooks':True})
        self.assertEqual(json.loads(args[args.index('--mcp-config')+1]),{'mcpServers':{}})
        self.assertIn('--strict-mcp-config',args);self.assertIn('--no-session-persistence',args)
        with patch.object(native_skills.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'[]','')):
            with self.assertRaises(native_plugins.PluginError):native_skills.claude_commands('/native/claude')

    def test_context_is_small_read_only_and_requires_selected_verified_profile(self):
        self.config.update(agents={'claude':True}, **{'local-toolsEnabled':True, 'local-tools':{'claude':{'agentBinary':'/native/claude','agentVersion':'1'}}})
        harness.save_config(self.config)
        tool={'installation':'installed','binary':'/native/claude','version':'1'}
        before=self.settings.read_bytes()
        with patch.object(harness,'mise_inventory',return_value=({},'')), patch.object(harness,'agent_tool',return_value=tool), patch.object(bridge,'inspect',return_value={'verified':True}), patch.object(catalog,'context',return_value={'tools':[{'id':'rg'}]}) as context, patch.object(harness,'status') as status:
            self.assertEqual(harness.catalog_context(str(self.settings),'claude')['tools'],[{'id':'rg'}])
            self.assertTrue(context.call_args.args[2]);status.assert_not_called()
            tool['version']='2';harness.catalog_context(str(self.settings),'claude')
            self.assertFalse(context.call_args.args[2])
            self.assertEqual(harness.catalog_context(str(self.settings),'codex')['tools'],[])
        self.assertEqual(self.settings.read_bytes(),before)
        link=self.root/'link';link.symlink_to(self.settings)
        output=io.StringIO()
        with patch.object(harness,'status') as status, contextlib.redirect_stdout(output):
            self.assertEqual(harness.main(['catalog-context','--settings',str(link),'--agent','claude']),1)
        status.assert_not_called();self.assertEqual(json.loads(output.getvalue())['tools'],[])
        self.assertLess(len(output.getvalue()),256)

    def test_global_bridge_toggle_does_not_select_profiles_or_install_catalog(self):
        harness.save_config(self.config)
        output=io.StringIO()
        with patch.object(catalog,'install') as install, patch.object(harness,'status',return_value={'agents':[], 'components':[{'id':'local-tools','enabled':False,'targets':[]}]}), contextlib.redirect_stdout(output):
            self.assertEqual(harness.main(['local-tools-all','enable']),0)
        install.assert_not_called()
        value=harness.load_config();self.assertTrue(value['local-toolsEnabled']);self.assertEqual(value['agents'],{})
        value['localToolsSettings']={'defaults':{'rg':'invalid'}};harness.save_config(value)
        with self.assertRaises(native_plugins.PluginError):harness.load_config()

    def test_tool_update_reuses_mise_no_prune_and_checks_actual_binary(self):
        binary=self.binary()
        inventory={'ripgrep':[{'active':True,'installed':True,'install_path':str(self.root),'version':'15.2.0'}]}
        with patch.object(catalog.native_tools,'mise_inventory',return_value=(inventory,'')), patch.object(catalog.native_tools.shutil,'which',side_effect=lambda n:str(binary) if n=='rg' else '/native/mise' if n=='mise' else None), patch.object(catalog.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'','')) as run, patch.object(catalog,'probe',return_value={'binary':str(binary),'version':'15.2.0'}):
            result=catalog.update('rg');self.assertFalse(result['changed'])
            self.assertEqual(run.call_args.args[0],['/native/mise','upgrade','--global','--no-prune','ripgrep'])
            def corrupt(*args,**kwargs):
                binary.write_text('changed');return subprocess.CompletedProcess([],0,'','')
            run.side_effect=corrupt
            with self.assertRaises(catalog.CatalogError):catalog.update('rg')
        with patch.object(catalog.native_tools,'mise_inventory',return_value=({},'')), patch.object(catalog.native_tools.shutil,'which',side_effect=lambda n:str(binary) if n=='rg' else None), patch.object(catalog.subprocess,'run') as run:
            with self.assertRaises(catalog.CatalogError):catalog.update('rg')
            run.assert_not_called()


if __name__=='__main__':unittest.main()

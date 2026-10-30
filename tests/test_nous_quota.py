import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import nous_quota as quota
import native_pi
import native_plugins


class NousQuotaTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        env=patch.dict(os.environ,{'HOME':str(self.root),'PI_CODING_AGENT_DIR':str(self.root/'agent'),
                                  'XDG_DATA_HOME':str(self.root/'data')})
        env.start();self.addCleanup(env.stop)
        self.config={'piProviders':{'nous':True},'piNousQuota':{'requestsPerMinute':60,'marginPercent':25}}
        self.file=self.root/'state.json'
        guard=patch.object(native_pi,'nous_model_allowed');guard.start();self.addCleanup(guard.stop)

    def admit(self,now,boot='fixture'):
        return quota.operate(self.config,self.file,'admit','free/model:free',now=now,boot=boot)

    def test_margin_shared_deadline_and_retry_after_preserve_native_controls(self):
        self.assertTrue(self.admit(100)['allowed'])
        self.assertEqual(self.admit(100),{'allowed':False,'waitMs':1334})
        self.assertTrue(self.admit(101.334)['allowed'])
        quota.operate(self.config,self.file,'response',status=429,retry_ms=4000,now=101.5,boot='fixture')
        self.assertFalse(self.admit(105)['allowed']);self.assertTrue(self.admit(105.5)['allowed'])
        value=json.loads(self.file.read_text());self.assertEqual(value['lastStatus'],429)
        self.assertEqual(set(value),{'boot','next','cooldown','cooldownUntil','lastStatus'})

    def test_unknown_limit_blocks_without_guessing_and_disabled_does_not_intercept(self):
        self.config['piNousQuota']['requestsPerMinute']=0
        with self.assertRaises(native_plugins.PluginError):self.admit(1)
        self.assertFalse(self.file.exists())
        self.config['piProviders']['nous']=False
        self.assertEqual(self.admit(1),{'allowed':True,'managed':False})
        self.assertEqual(quota.operate(self.config,self.file,'response',status=200,boot='fixture'),{'recorded':False})
        self.assertFalse(self.file.exists())

    def test_reboot_cooldown_is_persisted_and_foreign_or_corrupt_state_is_not_reset(self):
        self.admit(100)
        self.assertEqual(self.admit(0,'newboot')['waitMs'],60000)
        self.assertTrue(self.admit(60,'newboot')['allowed'])
        self.file.write_text('{invalid');before=self.file.read_bytes()
        with self.assertRaises(ValueError):self.admit(100)
        self.assertEqual(self.file.read_bytes(),before)
        self.file.unlink();target=self.root/'foreign';target.write_text('keep');self.file.symlink_to(target)
        with self.assertRaises(native_plugins.PluginError):self.admit(100)
        self.assertEqual(target.read_text(),'keep')

    def test_extension_install_withdrawal_preserves_foreign_resources_and_edited_source(self):
        native_pi.home().mkdir();(native_pi.home()/'settings.json').write_text('{"extensions":["/user/extension.ts"],"defaultProjectTrust":"never"}')
        settings_file=self.root/'settings.json';quota.configure(settings_file,True)
        self.assertTrue(quota.inspect(settings_file));source,state=quota.paths(settings_file)
        text=source.read_text();self.assertIn('ctx.abort()',text);self.assertNotIn('event.payload',text)
        self.assertIn('getSettings().retry?.provider?.maxRetries',text)
        source.write_text(text+'// user edit\n')
        with self.assertRaises(native_plugins.PluginError):quota.configure(settings_file,True)
        quota.configure(settings_file,False)
        self.assertEqual(native_pi.read('settings.json'),{'extensions':['/user/extension.ts'],'defaultProjectTrust':'never'})
        self.assertTrue(source.read_text().endswith('// user edit\n'));self.assertFalse(state.exists())

    def test_unknown_provider_error_pauses_all_sessions_until_declared_official_reset(self):
        self.admit(100)
        quota.operate(self.config,self.file,'error',now=100,boot='fixture')
        with self.assertRaises(native_plugins.PluginError):self.admit(1000)
        import time
        wall=time.time()
        quota.operate(self.config,self.file,'resume',now=1000,boot='fixture',reset_at=wall+3)
        self.assertFalse(self.admit(1001)['allowed']);self.assertTrue(self.admit(1004)['allowed'])
        with self.assertRaises(native_plugins.PluginError):quota.operate(self.config,self.file,'resume',now=1000,boot='fixture',reset_at=wall-1000)

    def test_known_long_cooldowns_survive_reboot(self):
        with patch.object(quota.time,'time',return_value=1000):
            self.admit(100)
            quota.operate(self.config,self.file,'response',status=429,retry_ms=3600000,now=100,boot='fixture')
        with patch.object(quota.time,'time',return_value=1010):
            self.assertEqual(self.admit(0,'newboot')['waitMs'],3590000)
            self.assertFalse(self.admit(60,'newboot')['allowed'])
            self.assertTrue(self.admit(3590,'newboot')['allowed'])

    def test_official_reset_and_low_rpm_are_not_shortened_by_reboot(self):
        with patch.object(quota.time,'time',return_value=1000):
            self.admit(100)
            quota.operate(self.config,self.file,'resume',now=100,boot='fixture',reset_at=8200)
        with patch.object(quota.time,'time',return_value=1010):
            self.assertEqual(self.admit(0,'newboot')['waitMs'],7190000)
        self.file.unlink();self.config['piNousQuota']['requestsPerMinute']=1
        self.admit(100)
        self.assertEqual(self.admit(0,'anotherboot')['waitMs'],80000)

    def test_legacy_cooldown_without_recoverable_clock_pauses_after_reboot(self):
        self.file.write_text(json.dumps({'boot':'oldboot','next':100,'cooldown':4000}))
        with self.assertRaises(native_plugins.PluginError):self.admit(0,'newboot')
        with patch.object(quota.time,'time',return_value=1000):
            quota.operate(self.config,self.file,'resume',now=0,boot='newboot',reset_at=1100)
        self.assertFalse(self.admit(99,'newboot')['allowed'])
        self.assertTrue(self.admit(100,'newboot')['allowed'])

    def test_corrupt_utc_cooldown_is_preserved_and_blocks_admission(self):
        for invalid in ('unknown',-1,float('nan'),True):
            with self.subTest(invalid=invalid):
                self.file.write_text(json.dumps({'boot':'fixture','next':0,'cooldown':0,'cooldownUntil':invalid}))
                before=self.file.read_bytes()
                with self.assertRaises(native_plugins.PluginError):self.admit(100)
                self.assertEqual(self.file.read_bytes(),before)

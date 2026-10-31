import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import native_pi
import nous_quota as quota

def node_runs_typescript():
    if not shutil.which('node'):return False
    version=subprocess.run(['node','--version'],capture_output=True,text=True).stdout.lstrip('v').split('.')
    return (int(version[0]),int(version[1]))>=(22,6)


HARNESS=r'''
import { pathToFileURL } from "node:url";
const [extension,scenario]=process.argv.slice(2);
const {action,credentials,responses}=JSON.parse(scenario);
const requests=[];const provider={};
globalThis.fetch=async (url,init)=>{
  requests.push({url:String(url),headers:init.headers,body:Object.fromEntries(new URLSearchParams(init.body))});
  const next=responses.shift();
  return new Response(JSON.stringify(next.body),{status:next.status});
};
const module=await import(pathToFileURL(extension));
module.default({on(){},exec(){},getSettings(){return {}},registerProvider(name,config){provider[name]=config}});
const oauth=provider.nous?.oauth;
const auth=[];const output={requests,auth,provider:Object.keys(provider),fields:Object.keys(provider.nous??{})};
try {
  if (action==="login") output.result=await oauth.login({onAuth(value){auth.push(value)}});
  if (action==="login-cancelled") {
    const controller=new AbortController();
    output.result=await oauth.login({signal:controller.signal,onAuth(){controller.abort()}});
  }
  if (action==="refresh") output.result=await oauth.refreshToken(credentials);
  if (action==="key") output.result=oauth.getApiKey(credentials);
} catch (error) { output.error=error.message }
process.stdout.write(JSON.stringify(output));
'''
FAKE_KEYRING=r'''#!/bin/sh
# Records every call; the stored value only ever arrives through stdin.
echo "$*" >> "$FAKE_KEYRING_DIR/calls"
case "$1" in
  store) [ -e "$FAKE_KEYRING_DIR/broken" ] && exit 1; cat > "$FAKE_KEYRING_DIR/secret" ;;
  lookup) [ -e "$FAKE_KEYRING_DIR/secret" ] || exit 1; cat "$FAKE_KEYRING_DIR/secret" ;;
esac
'''
DEVICE={'status':200,'body':{'device_code':'synthetic-device','user_code':'ABCD','expires_in':60,'interval':1,
                             'verification_uri_complete':'https://portal.nousresearch.com/activate?user_code=ABCD'}}


@unittest.skipUnless(node_runs_typescript(),'Node 22.6 o superior ejecuta la extensión generada')
class NousOAuthExtensionTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        env=patch.dict(os.environ,{'HOME':str(self.root),'PI_CODING_AGENT_DIR':str(self.root/'agent'),
                                   'XDG_DATA_HOME':str(self.root/'data')})
        env.start();self.addCleanup(env.stop)
        self.extension=self.root/'extension.ts';self.extension.write_text(quota.extension(self.root/'settings.json'))
        self.harness=self.root/'harness.mjs';self.harness.write_text(HARNESS)
        self.keyring=self.root/'keyring';self.keyring.mkdir()
        tool=self.keyring/'secret-tool';tool.write_text(FAKE_KEYRING);tool.chmod(0o700)
        self.secret=self.keyring/'secret'

    def run_extension(self,action,responses=(),credentials=None):
        scenario=json.dumps({'action':action,'responses':list(responses),'credentials':credentials})
        result=subprocess.run(['node','--experimental-strip-types','--no-warnings',str(self.harness),str(self.extension),scenario],
                              capture_output=True,text=True,timeout=30,
                              env={**os.environ,'PATH':f"{self.keyring}:{os.environ['PATH']}",'FAKE_KEYRING_DIR':str(self.keyring)})
        self.assertEqual(result.returncode,0,result.stderr)
        return json.loads(result.stdout)

    def test_login_adds_only_authentication_to_the_free_provider(self):
        output=self.run_extension('none')
        self.assertEqual(output['provider'],['nous'])
        # The free catalog and endpoint stay in models.json, where the panel verifies them.
        self.assertEqual(output['fields'],['oauth'])

    def test_login_waits_for_approval_and_uses_the_access_token_as_credential(self):
        output=self.run_extension('login',[DEVICE,{'status':400,'body':{'error':'authorization_pending'}},
                                           {'status':200,'body':{'access_token':'synthetic-access','refresh_token':'synthetic-refresh','expires_in':3600}}])
        device,pending,token=output['requests']
        self.assertEqual(device['url'],'https://portal.nousresearch.com/api/oauth/device/code')
        self.assertEqual(device['body'],{'client_id':'hermes-cli','scope':'inference:invoke'})
        self.assertEqual(pending['body']['device_code'],'synthetic-device')
        self.assertEqual(token['url'],'https://portal.nousresearch.com/api/oauth/token')
        self.assertEqual(output['auth'][0]['url'],DEVICE['body']['verification_uri_complete'])
        self.assertEqual(json.loads(self.secret.read_text()),{'access':'synthetic-access','refresh':'synthetic-refresh'})

    def test_what_pi_writes_to_disk_holds_no_token(self):
        output=self.run_extension('login',[DEVICE,{'status':200,'body':{'access_token':'synthetic-access','refresh_token':'synthetic-refresh','expires_in':3600}}])
        self.assertEqual(set(output['result']),{'access','refresh','expires'})
        self.assertNotIn('synthetic',json.dumps(output['result']))
        self.assertNotIn('synthetic',(self.keyring/'calls').read_text())

    def test_login_fails_without_falling_back_to_plain_text_when_the_keyring_is_unavailable(self):
        (self.keyring/'broken').touch()
        output=self.run_extension('login',[DEVICE,{'status':200,'body':{'access_token':'synthetic-access','refresh_token':'synthetic-refresh','expires_in':3600}}])
        self.assertNotIn('result',output);self.assertIn('keyring',output['error'])
        self.assertNotIn('synthetic',output['error'])

    def test_credentials_expire_before_the_token_does(self):
        output=self.run_extension('login',[DEVICE,{'status':200,'body':{'access_token':'a','refresh_token':'r','expires_in':3600}}])
        remaining=(output['result']['expires']-__import__('time').time()*1000)/1000
        self.assertLess(remaining,3600-60);self.assertGreater(remaining,3600-300)

    def test_short_lived_tokens_do_not_expire_on_arrival(self):
        output=self.run_extension('login',[DEVICE,{'status':200,'body':{'access_token':'a','refresh_token':'r','expires_in':100}}])
        self.assertGreater(output['result']['expires']-__import__('time').time()*1000,0)

    def test_cancelled_login_stops_before_waiting_for_the_interval(self):
        output=self.run_extension('login-cancelled',[DEVICE])
        self.assertIn('cancelled',output['error']);self.assertEqual(len(output['requests']),1)

    def test_denied_login_stops_waiting(self):
        output=self.run_extension('login',[DEVICE,{'status':400,'body':{'error':'access_denied'}}])
        self.assertIn('access_denied',output['error']);self.assertEqual(len(output['requests']),2)

    def stored(self,access='old-access',refresh='old-refresh'):
        self.secret.write_text(json.dumps({'access':access,'refresh':refresh}))
        return {'access':'keyring','refresh':'keyring','expires':1}

    def test_refresh_sends_the_single_use_token_in_its_header_and_stores_the_rotated_one(self):
        output=self.run_extension('refresh',[{'status':200,'body':{'access_token':'new-access','refresh_token':'new-refresh','expires_in':3600}}],self.stored())
        request=output['requests'][0]
        self.assertEqual(request['headers']['x-nous-refresh-token'],'old-refresh')
        self.assertEqual(request['body'],{'grant_type':'refresh_token','client_id':'hermes-cli'})
        self.assertEqual(json.loads(self.secret.read_text()),{'access':'new-access','refresh':'new-refresh'})
        self.assertNotIn('new-',json.dumps(output['result']))

    def test_refresh_keeps_the_token_when_the_portal_does_not_rotate_it(self):
        self.run_extension('refresh',[{'status':200,'body':{'access_token':'new-access','expires_in':3600}}],self.stored(refresh='kept-refresh'))
        self.assertEqual(json.loads(self.secret.read_text())['refresh'],'kept-refresh')

    def test_errors_never_expose_tokens_or_response_bodies(self):
        output=self.run_extension('refresh',[{'status':400,'body':{'error':'refresh_token_reused','error_description':'synthetic-body-secret'}}],self.stored(refresh='synthetic-refresh-secret'))
        self.assertIn('refresh_token_reused',output['error'])
        self.assertNotIn('synthetic-body-secret',output['error']);self.assertNotIn('synthetic-refresh-secret',output['error'])

    def test_refresh_without_a_stored_session_asks_for_login_and_calls_nothing(self):
        output=self.run_extension('refresh',[],{'access':'keyring','refresh':'keyring','expires':1})
        self.assertIn('keyring',output['error']);self.assertEqual(output['requests'],[])

    def test_api_key_is_read_from_the_keyring(self):
        self.assertEqual(self.run_extension('key',credentials=self.stored(access='synthetic-access'))['result'],'synthetic-access')


RESPONSE_HARNESS=r'''
import { pathToFileURL } from "node:url";
const [extension,status]=process.argv.slice(2);
const handlers={};const calls=[];
const module=await import(pathToFileURL(extension));
module.default({registerProvider(){},getSettings(){return {}},on(name,handler){handlers[name]=handler},
  exec(_command,args){calls.push(args);return args.includes("admit") ? Promise.resolve({code:0,stdout:'{"allowed":true,"managed":true}'}) : new Promise(()=>{})}});
const ctx={model:{provider:"nous",id:"m",baseUrl:"https://inference-api.nousresearch.com/v1"},abort(){}};
await handlers.before_provider_request({},ctx);
const finished=await Promise.race([handlers.after_provider_response({status:Number(status),headers:{}},ctx).then(()=>true),
  new Promise(resolve=>setTimeout(()=>resolve(false),200))]);
process.stdout.write(JSON.stringify({finished,recorded:calls.some(args=>args.includes("response"))}));
process.exit(0);
'''


ERROR_HARNESS=r'''
import { pathToFileURL } from "node:url";
const [extension,message]=process.argv.slice(2);
const handlers={};const calls=[];
const module=await import(pathToFileURL(extension));
module.default({registerProvider(){},getSettings(){return {}},on(name,handler){handlers[name]=handler},
  exec(_command,args){calls.push(args);return Promise.resolve({code:0,stdout:args.includes("admit") ? '{"allowed":true,"managed":true}' : '{"paused":true}'})}});
const ctx={model:{provider:"nous",id:"m",baseUrl:"https://inference-api.nousresearch.com/v1"},abort(){}};
await handlers.before_provider_request({},ctx);
await handlers.message_end({message:{role:"assistant",stopReason:"error",errorMessage:message}},ctx);
process.stdout.write(JSON.stringify({paused:calls.some(args=>args.includes("error"))}));
process.exit(0);
'''


@unittest.skipUnless(node_runs_typescript(),'Node 22.6 o superior ejecuta la extensión generada')
class NousErrorPauseTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        env=patch.dict(os.environ,{'HOME':str(self.root),'PI_CODING_AGENT_DIR':str(self.root/'agent'),'XDG_DATA_HOME':str(self.root/'data')})
        env.start();self.addCleanup(env.stop)
        self.extension=self.root/'extension.ts';self.extension.write_text(quota.extension(self.root/'settings.json'))
        self.harness=self.root/'harness.mjs';self.harness.write_text(ERROR_HARNESS)

    def pauses(self,message):
        result=subprocess.run(['node','--experimental-strip-types','--no-warnings',str(self.harness),str(self.extension),message],
                              capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        return json.loads(result.stdout)['paused']

    def test_a_saturated_free_model_does_not_pause_every_session(self):
        self.assertFalse(self.pauses('429: {"status":429,"message":"The requested model is temporarily at capacity upstream. This is not your API key\'s rate limit — please retry shortly."}'))

    def test_an_unavailable_free_model_does_not_pause_every_session(self):
        self.assertFalse(self.pauses('503: {"status":503,"message":"The requested model is currently unavailable."}'))

    def test_an_account_rate_limit_still_pauses(self):
        self.assertTrue(self.pauses('429: {"status":429,"message":"Rate limit exceeded"}'))

    def test_an_unknown_error_still_pauses(self):
        self.assertTrue(self.pauses('undefined is not a function'))


@unittest.skipUnless(node_runs_typescript(),'Node 22.6 o superior ejecuta la extensión generada')
class NousResponseRecordingTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        env=patch.dict(os.environ,{'HOME':str(self.root),'PI_CODING_AGENT_DIR':str(self.root/'agent'),'XDG_DATA_HOME':str(self.root/'data')})
        env.start();self.addCleanup(env.stop)
        self.extension=self.root/'extension.ts';self.extension.write_text(quota.extension(self.root/'settings.json'))
        self.harness=self.root/'harness.mjs';self.harness.write_text(RESPONSE_HARNESS)

    def respond(self,status):
        result=subprocess.run(['node','--experimental-strip-types','--no-warnings',str(self.harness),str(self.extension),str(status)],
                              capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        return json.loads(result.stdout)

    def test_success_is_recorded_without_holding_the_stream(self):
        self.assertEqual(self.respond(200),{'finished':True,'recorded':True})

    def test_rate_limit_keeps_the_response_handler_waiting_for_the_record(self):
        self.assertEqual(self.respond(429),{'finished':False,'recorded':True})


class NousOAuthCheckTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        env=patch.dict(os.environ,{'HOME':str(self.root),'PI_CODING_AGENT_DIR':str(self.root/'agent'),'XDG_DATA_HOME':str(self.root/'data')})
        env.start();self.addCleanup(env.stop)
        (self.root/'agent').mkdir(mode=0o700)
        self.settings=self.root/'settings.json'

    def check(self,provider='nous',auth=None,stored=True):
        if auth is not None:(self.root/'agent/auth.json').write_text(auth if isinstance(auth,str) else json.dumps(auth))
        # `pi auth check` does not load extensions, so it reports OAuth sessions as not ready.
        def run(args,**options):
            if args[0]=='secret-tool':
                self.assertEqual(options.get('stdout'),subprocess.DEVNULL)
                return subprocess.CompletedProcess(args,0 if stored else 1)
            return subprocess.CompletedProcess(args,1,json.dumps({'status':'not_ready'}),'')
        with patch.object(native_pi.subprocess,'run',side_effect=run):
            return native_pi.check_provider('/native/pi',provider,self.settings)

    def test_oauth_session_counts_only_while_the_keyring_holds_it(self):
        session={'nous':{'type':'oauth','access':'keyring','refresh':'keyring','expires':1}}
        result=self.check(auth=session)
        self.assertTrue(result['ready']);self.assertFalse(result['remoteVerified'])
        self.assertFalse(self.check(auth=session,stored=False)['ready'])

    def test_plain_text_oauth_tokens_are_not_accepted(self):
        self.assertFalse(self.check(auth={'nous':{'type':'oauth','access':'synthetic','refresh':'synthetic'}})['ready'])

    def test_mixed_entry_with_a_plain_text_access_token_is_not_accepted(self):
        self.assertFalse(self.check(auth={'nous':{'type':'oauth','access':'synthetic','refresh':'keyring'}})['ready'])

    def test_disabling_nous_removes_the_session_from_the_keyring(self):
        with patch.object(native_pi.subprocess,'run',return_value=subprocess.CompletedProcess([],0)) as run:
            config={'piProviders':{'nous':True}}
            native_pi.provider_choice(config,'nous',False,settings_file=self.settings)
        self.assertEqual(run.call_args.args[0],['secret-tool','clear',*quota.keyring_attributes(self.settings)])
        self.assertFalse(config['piProviders']['nous'])

    def test_disabling_nous_without_a_keyring_still_disables(self):
        with patch.object(native_pi.subprocess,'run',side_effect=FileNotFoundError):
            config={'piProviders':{'nous':True}}
            native_pi.provider_choice(config,'nous',False,settings_file=self.settings)
        self.assertFalse(config['piProviders']['nous'])

    def test_missing_session_is_not_ready(self):
        self.assertFalse(self.check(auth={})['ready']);self.assertFalse(self.check()['ready'])

    def test_unreadable_auth_file_is_not_ready(self):
        self.assertFalse(self.check(auth='{not json')['ready'])

    def test_missing_keyring_tool_is_not_ready(self):
        (self.root/'agent/auth.json').write_text(json.dumps({'nous':{'type':'oauth','refresh':'keyring'}}))
        with patch.object(native_pi.subprocess,'run',side_effect=FileNotFoundError):
            self.assertFalse(quota.oauth_stored(self.settings))

    def test_oauth_entry_only_applies_to_nous(self):
        self.assertFalse(self.check('xai',{'xai':{'type':'oauth','access':'keyring','refresh':'keyring'}})['ready'])


if __name__=='__main__':
    unittest.main()

#!/usr/bin/env python3
"""One local request deadline shared by managed Pi sessions; no proxy or payload."""

import i18n
from i18n import tr

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

import memory_archive
import native_plugins
import native_pi
import runtime_guard
import shared_memory
from atomic_files import atomic_write


def settings(config):
    value=config.get('piNousQuota',{'requestsPerMinute':0,'marginPercent':25})
    if (not isinstance(value,dict) or set(value)!={'requestsPerMinute','marginPercent'}
            or type(value['requestsPerMinute']) is not int or not 0<=value['requestsPerMinute']<=100000
            or type(value['marginPercent']) is not int or not 0<=value['marginPercent']<=90):
        raise native_plugins.PluginError(tr('El límite Nous debe ser un RPM oficial conocido (0 desconocido) y margen de 0 a 90 %.'))
    return dict(value)


def paths(settings_file):
    root=shared_memory.data_root()
    key=hashlib.sha256((str(native_pi.home())+str(settings_file)).encode()).hexdigest()[:24]
    return root/'tools/pi-nous'/key/'extension.ts',root/'state/nous-quota.json'


# Nous publishes no client for third parties: this is the Hermes device flow and client id.
# The fixed portal keeps tokens from being redirected; errors carry only the OAuth code.
# Tokens live only in the encrypted keyring; what Pi writes to auth.json holds placeholders.
KEYRING_SERVICE='ai-team-pi-nous'
OAUTH_LOGIN='''type NousCredentials = { access: string; refresh: string; expires: number };
type NousTokens = { access: string; refresh: string };
function nousSave(tokens: NousTokens) {
  try {
    execFileSync("secret-tool",["store","--label=AI Team Pi Nous",...NOUS_KEYRING],
      {input:JSON.stringify(tokens),timeout:30000,stdio:["pipe","ignore","ignore"]});
  } catch { throw new Error("Nous: the encrypted keyring is unavailable; nothing was stored in plain text."); }
}
function nousLoad(): NousTokens {
  try {
    const tokens = JSON.parse(execFileSync("secret-tool",["lookup",...NOUS_KEYRING],
      {encoding:"utf8",timeout:30000,stdio:["ignore","pipe","ignore"]}));
    if (typeof tokens.access === "string" && typeof tokens.refresh === "string") return tokens;
  } catch {}
  throw new Error("Nous: no session in the encrypted keyring; unlock it or log in again.");
}
const NOUS_PORTAL = "https://portal.nousresearch.com";
const NOUS_CLIENT = "hermes-cli";
async function nousPost(path: string, body: Record<string,string>, headers: Record<string,string> = {}, signal?: AbortSignal) {
  const timeout = AbortSignal.timeout(30000);
  const response = await fetch(NOUS_PORTAL+path, {method:"POST",
    headers:{Accept:"application/json","Content-Type":"application/x-www-form-urlencoded",...headers},
    body:new URLSearchParams(body).toString(), signal:signal ? AbortSignal.any([signal,timeout]) : timeout});
  const data: any = await response.json().catch(() => ({}));
  return {ok:response.ok, status:response.status, data, code:typeof data.error === "string" ? data.error : ""};
}
function nousCredentials(data: any, previous = ""): NousCredentials {
  const refresh = typeof data.refresh_token === "string" ? data.refresh_token : previous;
  if (typeof data.access_token !== "string" || !refresh) throw new Error("Nous token response is incomplete.");
  nousSave({access:data.access_token,refresh});
  const ttl = Number(data.expires_in);
  const seconds = Number.isFinite(ttl) && ttl > 0 ? ttl : 900;
  return {access:"keyring", refresh:"keyring", expires:Date.now()+Math.max(seconds-120,seconds/2)*1000};
}
function nousPause(ms: number, signal?: AbortSignal) {
  return new Promise<void>((resolve,reject) => {
    if (signal?.aborted) return reject(new Error("Nous login cancelled."));
    const timer = setTimeout(resolve,ms);
    signal?.addEventListener("abort",() => { clearTimeout(timer); reject(new Error("Nous login cancelled.")); },{once:true});
  });
}
async function nousLogin(callbacks: any): Promise<NousCredentials> {
  const signal: AbortSignal | undefined = callbacks.signal;
  const device = await nousPost("/api/oauth/device/code",{client_id:NOUS_CLIENT,scope:"inference:invoke"},{},signal);
  if (!device.ok || typeof device.data.device_code !== "string") throw new Error(`Nous device code failed (${device.status} ${device.code})`);
  callbacks.onAuth({url:device.data.verification_uri_complete,instructions:`Code: ${device.data.user_code}`});
  const deadline = Date.now()+Number(device.data.expires_in || 600)*1000;
  let wait = Math.max(1,Number(device.data.interval) || 5);
  while (Date.now() < deadline) {
    await nousPause(wait*1000,signal);
    const token = await nousPost("/api/oauth/token",{grant_type:"urn:ietf:params:oauth:grant-type:device_code",client_id:NOUS_CLIENT,device_code:device.data.device_code},{},signal);
    if (token.ok) return nousCredentials(token.data);
    if (token.code === "authorization_pending") continue;
    if (token.code === "slow_down") { wait += 5; continue; }
    throw new Error(`Nous login failed (${token.status} ${token.code})`);
  }
  throw new Error("Nous login timed out.");
}
async function nousRefresh(): Promise<NousCredentials> {
  // Single-use token: Pi serializes refreshes and the rotated value goes back to the keyring.
  const {refresh} = nousLoad();
  const result = await nousPost("/api/oauth/token",{grant_type:"refresh_token",client_id:NOUS_CLIENT},{"x-nous-refresh-token":refresh});
  if (!result.ok) throw new Error(`Nous refresh failed (${result.status} ${result.code})`);
  return nousCredentials(result.data,refresh);
}
'''


def keyring_attributes(settings_file):
    return ['service',KEYRING_SERVICE,'account',paths(settings_file)[0].parent.name]


def oauth_stored(settings_file):
    try:
        return native_pi.subprocess.run(['secret-tool','lookup',*keyring_attributes(settings_file)],stdin=native_pi.subprocess.DEVNULL,
                                        stdout=native_pi.subprocess.DEVNULL,stderr=native_pi.subprocess.DEVNULL,timeout=30).returncode==0
    except (OSError,native_pi.subprocess.SubprocessError):
        return False


def clear_oauth(settings_file):
    try:
        native_pi.subprocess.run(['secret-tool','clear',*keyring_attributes(settings_file)],stdin=native_pi.subprocess.DEVNULL,
                                 stdout=native_pi.subprocess.DEVNULL,stderr=native_pi.subprocess.DEVNULL,timeout=30)
    except (OSError,native_pi.subprocess.SubprocessError):
        pass  # Without a keyring tool there is no stored session to remove.


def extension(settings_file):
    source,state=paths(settings_file)
    command=[sys.executable,str(Path(__file__).resolve()),'--settings',str(settings_file),'--state',str(state)]
    # Provider/model metadata only: never read or replace event.payload/messages.
    return '''import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { execFileSync } from "node:child_process";
const NOUS_KEYRING = '''+json.dumps(keyring_attributes(settings_file))+''';
'''+OAUTH_LOGIN+'''export default function(pi: ExtensionAPI) {
  // Authentication only: the free catalog and endpoint stay in models.json.
  pi.registerProvider("nous",{oauth:{name:"Nous Portal",login:nousLogin,refreshToken:nousRefresh,getApiKey:() => nousLoad().access}});
  const command = '''+json.dumps(command)+''';
  async function call(action: string, args: string[], signal?: AbortSignal) {
    const result = await pi.exec(command[0], [...command.slice(1), action, ...args], {timeout:5000, signal});
    if (result.code !== 0) throw new Error("Nous: configuración/cuota pendiente; no se sustituye por pago.");
    return JSON.parse(result.stdout);
  }
  let retryAfterKnown=false;
  let managedRequest=false;
  pi.on("before_provider_request", async (_event, ctx) => {
    if (ctx.model?.provider !== "nous") return;
    retryAfterKnown=false; managedRequest=false;
    try {
      while (!ctx.signal?.aborted) {
        const result = await call("admit", ["--model",ctx.model.id], ctx.signal);
        if (result.managed !== false && ctx.model.baseUrl.replace(/\\/+$/, "") !== '''+json.dumps(native_pi.NOUS_URL)+''')
          throw new Error("Nous: el endpoint de esta sesión no coincide con la conexión gratuita comprobada.");
        if (result.managed !== false && (pi.getSettings().retry?.provider?.maxRetries ?? 0) !== 0)
          throw new Error("Nous: retries internos de proveedor no están coordinados; se conserva tu configuración y se detiene esta petición.");
        if (result.allowed) { managedRequest=result.managed === true; return; }
        await new Promise<void>(resolve => {
          const signal=ctx.signal;
          const finish=() => { clearTimeout(timer); signal?.removeEventListener("abort",finish); resolve(); };
          const timer=setTimeout(finish, Math.min(result.waitMs,60000));
          signal?.addEventListener("abort",finish,{once:true});
        });
      }
    } catch (error) { ctx.abort(); throw error; }
  });
  pi.on("after_provider_response", async (event,ctx) => {
    if (ctx.model?.provider !== "nous" || !managedRequest) return;
    const raw=event.headers["retry-after"] ?? event.headers["Retry-After"];
    let delay=raw ? Number(raw)*1000 : 0;
    if (raw && !Number.isFinite(delay)) delay=Math.max(0,Date.parse(raw)-Date.now());
    retryAfterKnown=event.status === 429 && !!raw && Number.isFinite(delay) && delay >= 0;
    const recording=call("response",["--status",String(event.status),"--retry-ms",String(Number.isFinite(delay) ? Math.max(0,delay) : 0)]);
    // Holding a successful response races Pi closing its stream; only a failure must land before the next request.
    if (event.status >= 200 && event.status < 300) { recording.catch(() => {}); return; }
    try { await recording; }
    catch (error) { ctx.abort(); throw error; }
  });
  pi.on("message_end", async (event,ctx) => {
    if (ctx.model?.provider !== "nous" || !managedRequest || event.message.role !== "assistant" || event.message.stopReason !== "error" || retryAfterKnown) return;
    // Only the account can stop every session. Anything else, including a saturated model, fails this request alone.
    const failure = event.message.errorMessage ?? "";
    if (!/^(401|402|403|429)\\b/.test(failure) || /at capacity upstream|model is currently unavailable/i.test(failure)) return;
    try { const result=await call("error",[]); if (result.paused) ctx.abort(); }
    catch (error) { ctx.abort(); throw error; }
  });
}
'''


def inspect(settings_file):
    source,_=paths(settings_file);expected=extension(settings_file)
    entries=native_pi.read('settings.json').get('extensions',[])
    return (isinstance(entries,list) and str(source) in entries and source.is_file()
            and memory_archive.read_file(source,32*1024).decode()==expected)


def connection(settings_file):
    try:
        result={'connected':bool(inspect(settings_file)),'detail':tr('RPM de sesiones gestionadas; no contabiliza tráfico ajeno ni TPM.'),'paused':False}
        _,state=paths(settings_file)
        if state.exists():
            value=json.loads(memory_archive.read_file(state,4096))
            result['paused']=value.get('paused') is True
            if type(value.get('lastStatus')) is int:result['lastStatus']=value['lastStatus']
            if type(value.get('lastSuccessAt')) in (int,float):result['lastSuccessAt']=value['lastSuccessAt']
        return result
    except (OSError,ValueError,TypeError,native_plugins.PluginError):
        return {'connected':False,'paused':True,'detail':tr('Conexión de cuota pendiente o recursos nativos modificados; se conservan.')}


def configure(settings_file,enabled):
    source,_=paths(settings_file);marker=source.with_suffix('.sha256')
    original=native_pi.read('settings.json');entries=original.get('extensions',[])
    if not isinstance(entries,list):raise native_plugins.PluginError(tr('Los recursos Pi no tienen formato compatible; se conservan.'))
    if enabled:
        memory_archive.safe_path(source)
        if source.exists() and (not marker.exists() or memory_archive.read_file(marker,128).decode()!=hashlib.sha256(memory_archive.read_file(source,32*1024)).hexdigest()):
            raise native_plugins.PluginError(tr('La extensión de cuota está modificada o es ajena; se conserva.'))
        source.parent.mkdir(parents=True,mode=0o700,exist_ok=True);content=extension(settings_file)
        atomic_write(source,content);atomic_write(marker,hashlib.sha256(content.encode()).hexdigest())
        selected=[*entries] if str(source) in entries else [*entries,str(source)]
    else:selected=[entry for entry in entries if entry!=str(source)]
    native_pi.write('settings.json',original,{**original,'extensions':selected})


def persist(state,value):
    atomic_write(state,json.dumps(value))
    for path,flags in ((state,os.O_RDONLY|os.O_NOFOLLOW),(state.parent,os.O_RDONLY|os.O_DIRECTORY)):
        descriptor=os.open(path,flags)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)


def operate(config,state,action,model='',status=0,retry_ms=0,now=None,boot=None,reset_at=None):
    budget=settings(config);now=time.monotonic() if now is None else now;wall=time.time()
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip() if boot is None else boot
    if not native_pi.provider_settings(config)['nous']:
        return {'allowed':True,'managed':False} if action=='admit' else {'recorded':False}
    if action=='admit':
        native_pi.nous_model_allowed(config,'nous/'+model,require_connection=False)
        if not budget['requestsPerMinute']:raise native_plugins.PluginError(tr('Cuota Nous desconocida: introduce el RPM oficial de tu cuenta; no se inventa un límite.'))
    state=memory_archive.safe_path(Path(state));state.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    with runtime_guard.lease(state.with_suffix('.lock'),exclusive=True,wait=True):
        value=json.loads(memory_archive.read_file(state,4096)) if state.exists() else {'boot':boot,'next':0,'cooldown':0}
        if (not isinstance(value,dict) or set(value)-{'boot','next','cooldown','cooldownUntil','lastStatus','lastSuccessAt','paused','lastErrorAt'} or not isinstance(value.get('boot'),str)
                or any(type(value.get(key)) not in (int,float) or not math.isfinite(value[key]) or value[key]<0 for key in ('next','cooldown'))
                or ('paused' in value and type(value['paused']) is not bool)
                or ('lastStatus' in value and (type(value['lastStatus']) is not int or not 100<=value['lastStatus']<=599))
                or any(type(value[key]) not in (int,float) or not math.isfinite(value[key]) or value[key]<0 for key in ('lastErrorAt','lastSuccessAt','cooldownUntil') if key in value)):
            raise native_plugins.PluginError(tr('El estado de cuota Nous no es válido; no se reinicia para permitir tráfico.'))
        reboot=value['boot']!=boot
        if reboot:
            # Monotonic deadlines cannot be compared across boots. Preserve
            # known UTC resets; a legacy cooldown without UTC fails closed.
            if value['cooldown'] and 'cooldownUntil' not in value:value['paused']=True
            interval=60/(budget['requestsPerMinute']*(1-budget['marginPercent']/100)) if budget['requestsPerMinute'] else 60
            value.update(boot=boot,next=now+max(60,interval),cooldown=now+max(60,value.get('cooldownUntil',wall)-wall))
        if action=='error':
            value.update(paused=True,lastErrorAt=wall);result={'paused':True}
        elif action=='resume':
            if reset_at is None or not math.isfinite(reset_at) or reset_at<wall-300:
                raise native_plugins.PluginError(tr('Indica un reset oficial UTC vigente; no se inventa un tiempo de espera.'))
            value['cooldown']=max(value['cooldown'],now+max(0,reset_at-wall))
            value['cooldownUntil']=max(value.get('cooldownUntil',0),reset_at)
            value['paused']=False;result={'scheduled':True,'resetAt':reset_at}
        elif action=='response':
            if type(status) is not int or not 100<=status<=599 or not math.isfinite(retry_ms) or retry_ms<0:
                raise native_plugins.PluginError(tr('Respuesta de cuota no válida.'))
            value['lastStatus']=status
            if 200<=status<300:value['lastSuccessAt']=wall
            if status==429:
                value['cooldown']=max(value['cooldown'],now+retry_ms/1000)
                value['cooldownUntil']=max(value.get('cooldownUntil',0),wall+retry_ms/1000)
            result={'recorded':True}
        else:
            if value.get('paused'):
                if reboot:persist(state,value)
                raise native_plugins.PluginError(tr('Nous en pausa por error sin espera verificable; introduce el reset oficial antes de reintentar.'))
            deadline=max(value['next'],value['cooldown'])
            if deadline>now:
                result={'allowed':False,'waitMs':math.ceil((deadline-now)*1000)}
                if not reboot:return result
            else:
                value['next']=now+60/(budget['requestsPerMinute']*(1-budget['marginPercent']/100))
                result={'allowed':True,'managed':True}
        persist(state,value)
        return result


def main():
    with i18n.using(i18n.configured_language()):
        return _main()


def _main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings',required=True);parser.add_argument('--state',required=True)
    parser.add_argument('action',choices=('admit','response','error'));parser.add_argument('--model',default='')
    parser.add_argument('--status',type=int,default=0);parser.add_argument('--retry-ms',type=float,default=0)
    args=parser.parse_args()
    try:
        config=json.loads(memory_archive.read_file(Path(args.settings),1024*1024))
        if not isinstance(config,dict):raise ValueError()
        print(json.dumps(operate(config,args.state,args.action,args.model,args.status,args.retry_ms)));return 0
    except (OSError,ValueError,TypeError,KeyError,native_plugins.PluginError):
        print(json.dumps({'error':tr('Nous: límite/preferencia pendiente o estado no válido; no se autorizó otra petición.')}));return 1


if __name__=='__main__':sys.exit(main())

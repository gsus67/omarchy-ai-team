"""Pi's native CLI, resources and MCP configuration; no model proxy or router."""

from i18n import tr

import json
import os
from pathlib import Path
import subprocess
import tempfile

import memory_archive
import native_plugins
from atomic_files import atomic_write


def home():
    path=Path(os.environ.get('PI_CODING_AGENT_DIR', str(Path.home()/'.pi/agent')))
    return memory_archive.safe_path(path)


def read(name):
    path=memory_archive.safe_path(home()/name)
    if not path.exists():return {}
    value=json.loads(memory_archive.read_file(path,1024*1024))
    if not isinstance(value,dict):raise native_plugins.PluginError(tr('La configuración de Pi no es un objeto válido; se conserva.'))
    return value


def write(name, original, value):
    path=home()/name
    if read(name)!=original:raise native_plugins.PluginError(tr('La configuración de Pi cambió durante la operación; se conserva.'))
    memory_archive.safe_path(path)
    path.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    if read(name)!=original:raise native_plugins.PluginError(tr('La configuración de Pi cambió; se conserva.'))
    atomic_write(path,json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def metadata(binary, commands=('get_commands',), *, skills=(), extensions=(), catalog_providers=(), nous_catalog=None):
    allowed={'get_commands','get_available_models','get_state'}
    if any(command not in allowed for command in commands):
        raise native_plugins.PluginError(tr('La consulta de Pi solo admite metadatos, sin prompt.'))
    args=[binary,'--mode','rpc','--no-session','--offline','--no-extensions','--no-context-files',
          '--no-approve','--no-tools','--no-prompt-templates','--no-themes']
    args+=['--no-mcp']
    for path in skills:args+=['--skill',str(path)]
    for path in extensions:args+=['--extension',str(path)]
    request=''.join(json.dumps({'id':command,'type':command})+'\n' for command in commands)
    with tempfile.TemporaryDirectory(prefix='ai-team-pi-metadata-') as directory:
        base=Path(directory)
        environment={'HOME':directory,'PATH':os.environ.get('PATH',os.defpath),'LANG':'C.UTF-8',
                     'PI_OFFLINE':'1','PI_CODING_AGENT_DIR':str(base/'agent'),
                     **{'XDG_'+key+'_HOME':str(base/key.lower()) for key in ('CONFIG','DATA','CACHE','STATE')}}
        # Bundled catalog only. Placeholders are synthetic and never used in a
        # request; readiness is checked separately in the user's native profile.
        for provider in catalog_providers:
            if provider!='xai':raise native_plugins.PluginError(tr('Proveedor de catálogo no admitido.'))
            environment['XAI_API_KEY']='ai-team-offline-catalog-placeholder'
        if nous_catalog and nous_catalog.get('models'):
            agent=base/'agent';agent.mkdir()
            (agent/'models.json').write_text(json.dumps({'providers':{'nous':{'baseUrl':NOUS_URL,'api':'openai-completions','apiKey':'ai-team-offline-catalog-placeholder','models':nous_catalog['models']}}}))
        result=subprocess.run(args,cwd=directory,input=request,capture_output=True,text=True,timeout=25,
                              env=environment)
    try:
        rows={row['command']:row['data'] for line in result.stdout.splitlines()
              if (row:=json.loads(line)).get('type')=='response' and row.get('success') and row.get('id') in commands}
        if result.returncode or any(command not in rows for command in commands):raise ValueError()
    except (ValueError,KeyError,TypeError) as error:
        raise native_plugins.PluginError(tr('Pi no confirmó la consulta nativa sin inferencia; sigue pendiente.')) from error
    return rows


def model_catalog(binary,nous_catalog=None):
    rows=metadata(binary,('get_available_models',),catalog_providers=('xai',),nous_catalog=nous_catalog)['get_available_models']['models']
    result=[]
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('id'),str) or not isinstance(row.get('provider'),str):
            raise native_plugins.PluginError(tr('El catálogo de Pi no es compatible.'))
        # Never retain apiKey, headers, base URLs or credential resolver bodies.
        cost=row.get('cost',{})
        cost={key:value for key,value in cost.items() if key in ('input','output','cacheRead','cacheWrite') and type(value) in (int,float) and value>=0} if isinstance(cost,dict) else {}
        result.append({'id':(row['provider']+'/'+row['id'])[:240],'provider':row['provider'][:80],
                       'label':str(row.get('name',row['id']))[:160],'cost':cost,
                       'reasoning':bool(row.get('reasoning'))})
    return {'models':result,'source':'pi-native-offline','detail':tr('Catálogo incluido en Pi, sin conexión ni inferencia. No acredita autenticación, acceso ni precio vigente del proveedor.')}


def startup_path():
    for name in ('AGENTS.override.md','AGENTS.md','AGENTS.MD','CLAUDE.md','CLAUDE.MD'):
        path=home()/name
        if path.exists() or path.is_symlink():return memory_archive.safe_path(path)
    return home()/'AGENTS.md'


def startup():
    path=startup_path()
    return memory_archive.read_file(path,1024*1024).decode() if path.exists() else ''


def write_startup(original, updated):
    path=startup_path()
    if startup()!=original:raise native_plugins.PluginError(tr('Las instrucciones de Pi cambiaron; se conservan.'))
    if original!=updated:
        path.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
        atomic_write(path,updated)


def superpowers_source():
    settings=read('settings.json');packages=settings.get('packages',[])
    if not isinstance(packages,list):raise native_plugins.PluginError(tr('Los paquetes de Pi no tienen formato compatible.'))
    entries=[(index,row) for index,row in enumerate(packages)
             if (row.get('source') if isinstance(row,dict) else row)=='git:github.com/obra/superpowers']
    if len(entries)>1:raise native_plugins.PluginError(tr('Hay varias declaraciones Superpowers; se conservan.'))
    path=home()/'git/github.com/obra/superpowers'
    return settings,entries,path


def superpowers_state(receipt=None):
    import hashlib
    settings,entries,path=superpowers_source();receipt=receipt or {}
    installed=(path/'package.json').is_file();version='';digest='';enabled=False
    if installed:
        value=json.loads(memory_archive.read_file(path/'package.json',64*1024))
        if (value.get('name')!='superpowers' or value.get('pi',{}).get('extensions')!=['./.pi/extensions/superpowers.ts']
                or not isinstance(value.get('version'),str) or not value['version']
                or not memory_archive.read_file(path/'LICENSE',16*1024).startswith(b'MIT License\n')):
            raise native_plugins.PluginError(tr('El paquete de Pi no identifica la integración oficial Superpowers.'))
        origin=subprocess.run(['git','-C',str(path),'remote','get-url','origin'],capture_output=True,text=True,timeout=5)
        if origin.returncode or native_plugins.normalize_repo(origin.stdout.strip())!='obra/superpowers':
            raise native_plugins.PluginError(tr('El origen de Superpowers no es el repositorio oficial.'))
        version=value['version'];digest=hashlib.sha256(memory_archive.read_file(path/'.pi/extensions/superpowers.ts',128*1024)
                                                     +memory_archive.read_file(path/'skills/using-superpowers/SKILL.md',128*1024)).hexdigest()
    if entries:
        row=entries[0][1]
        disabled={'source':'git:github.com/obra/superpowers',**{key:[] for key in ('extensions','skills','prompts','themes')}}
        if row!='git:github.com/obra/superpowers' and row!=disabled:
            raise native_plugins.PluginError(tr('Superpowers tiene filtros ajenos; se conservan para revisión.'))
        enabled=isinstance(row,str)
    return {'installed':installed,'enabled':enabled,'version':version,'digest':digest,'path':str(path),
            'verified':bool(installed and receipt.get('digest')==digest and receipt.get('home')==str(home())),
            'detail':tr('Paquete Pi oficial; los cambios se cargan en sesiones nuevas. No aporta sandbox ni aprobaciones por herramienta.')}


def superpowers(binary,action,receipt=None):
    state=superpowers_state(receipt);wanted=action=='enable' if action in ('enable','disable') else state['enabled']
    if action=='update':
        import runtime_guard
        runtime_guard.assert_idle('pi')
        if not state['installed']:raise native_plugins.PluginError(tr('Actualizar no instala Superpowers si falta.'))
        args=[binary,'update','git:github.com/obra/superpowers']
    elif wanted and not state['installed']:
        args=[binary,'install','git:github.com/obra/superpowers']
    else:args=None
    if args:
        result=subprocess.run(args,cwd=Path.home(),capture_output=True,text=True,timeout=180,
                              env={**os.environ,'PI_CODING_AGENT_DIR':str(home())})
        if result.returncode:raise native_plugins.PluginError(tr('Pi no completó su gestor oficial de Superpowers.'))
    settings,entries,path=superpowers_source();updated=json.loads(json.dumps(settings))
    packages=updated.setdefault('packages',[])
    choice='git:github.com/obra/superpowers' if wanted else {'source':'git:github.com/obra/superpowers',**{key:[] for key in ('extensions','skills','prompts','themes')}}
    if entries:packages[entries[0][0]]=choice
    elif wanted:packages.append(choice)
    write('settings.json',settings,updated)
    state=superpowers_state()
    if wanted:
        commands=metadata(binary,extensions=[path/'.pi/extensions/superpowers.ts'])['get_commands']['commands']
        if not any(row.get('name')=='skill:using-superpowers' for row in commands):
            raise native_plugins.PluginError(tr('Pi no confirmó las skills oficiales de Superpowers.'))
    return {'id':'superpowers','version':state['version'],'digest':state['digest'],'home':str(home())}


def memory_state(receipt=None):
    import shared_memory
    receipt=receipt or {};runtime=shared_memory.inspect_runtime();config=read('mcp.json')
    server=config.get('mcpServers',{}).get(shared_memory.SERVER)
    command=shared_memory.mcp_command(runtime,'pi') if runtime['installed'] else []
    expected={'command':command[0],'args':command[1:]} if command else None
    enabled=bool(server and server.get('enabled',True))
    owned=bool(server and expected and all(server.get(key)==value for key,value in expected.items()))
    shared_memory.check_owned(server,runtime,'pi',receipt)
    if shared_memory.other_memory(config,'pi'):
        raise native_plugins.PluginError(tr('Hay otra memoria declarada en Pi; no se conectará un segundo motor.'))
    digest=shared_memory.connection_digest(runtime,server)
    available=set(shared_memory.TOOLS) if not server or server.get('exposure')!='hidden' else set()
    for tool,exposure in (server or {}).get('toolExposure',{}).items():
        if exposure=='hidden':available.discard(tool)
    ready=owned and {'memory_search','memory_read'}.issubset(available) and startup()==shared_memory.instructions(startup(),enabled)
    return {'id':shared_memory.SERVER,'installed':runtime['installed'],'enabled':enabled,'version':runtime['version'],
            'verified':bool(ready and receipt.get('digest')==digest and receipt.get('node')==runtime['node'] and receipt.get('home')==str(home())),
            'detail':tr('MCP oficial ECC compartido por proyecto; no se captura la conversación ni se añade otro motor.'), 'digest':digest,'availableTools':sorted(available)}


def memory(binary,action,receipt=None):
    import shared_memory
    from datetime import datetime,timezone
    state=memory_state(receipt);wanted=action=='enable' if action in ('enable','disable') else state['enabled']
    runtime=shared_memory.ensure_runtime() if wanted else shared_memory.inspect_runtime()
    config=read('mcp.json');updated=json.loads(json.dumps(config));servers=updated.setdefault('mcpServers',{})
    if wanted:
        command=shared_memory.mcp_command(runtime,'pi');servers[shared_memory.SERVER]={**servers.get(shared_memory.SERVER,{}),'command':command[0],'args':command[1:]}
        servers[shared_memory.SERVER].setdefault('exposure','direct')
        if action=='enable':servers[shared_memory.SERVER]['enabled']=True
    else:servers.pop(shared_memory.SERVER,None)
    write('mcp.json',config,updated)
    original=startup();write_startup(original,shared_memory.instructions(original,wanted))
    if wanted:
        shared_memory.probe(runtime,'pi')
        with tempfile.TemporaryDirectory(prefix='ai-team-pi-mcp-') as directory:
            base=Path(directory);(base/'mcp.json').write_text(json.dumps({'mcpServers':{shared_memory.SERVER:servers[shared_memory.SERVER]}}))
            result=subprocess.run([binary,'mcp','list','--json'],cwd=directory,capture_output=True,text=True,timeout=25,
                                  env={**os.environ,'PI_CODING_AGENT_DIR':directory,'PI_OFFLINE':'1'})
            if result.returncode:raise native_plugins.PluginError(tr('Pi no confirmó su conexión MCP; sigue pendiente.'))
            # Retain no server output or credential metadata.
            response=json.loads(result.stdout)
            rows=response.get('servers',[])
            if (len(rows)!=1 or rows[0].get('name')!=shared_memory.SERVER or rows[0].get('state')!='connected'
                    or set(rows[0].get('tools',[]))!=shared_memory.TOOLS):
                raise native_plugins.PluginError(tr('Pi no confirmó las cuatro herramientas MCP de ECC.'))
    state=memory_state()
    return {'id':shared_memory.SERVER,'home':str(home()),'version':runtime['version'],'digest':state['digest'],
            'node':runtime['node'],'server':shared_memory.server_identity(servers.get(shared_memory.SERVER)),'checkedAt':datetime.now(timezone.utc).isoformat()}


def inspect_component(component,receipt=None):
    if component=='superpowers':return superpowers_state(receipt)
    if component=='memory':return memory_state(receipt)
    raise native_plugins.PluginError(tr('Componente no admitido para Pi.'))


def apply_component(component,binary,action,receipt=None):
    if component=='superpowers':return superpowers(binary,action,receipt)
    if component=='memory':return memory(binary,action,receipt)
    raise native_plugins.PluginError(tr('Componente no admitido para Pi.'))


def provider_settings(config):
    import nous_quota
    nous_quota.settings(config)
    providers=config.get('piProviders',{})
    if (not isinstance(providers,dict) or set(providers)-{'xai','nous'}
            or any(type(value) is not bool for value in providers.values())
            or type(config.get('piPaidApproved',False)) is not bool):
        raise native_plugins.PluginError(tr('Las preferencias de proveedores Pi no son válidas; se conservan.'))
    checks=config.get('piProviderChecks',{})
    if (not isinstance(checks,dict) or set(checks)-{'xai','nous'} or any(not isinstance(row,dict)
            or set(row)-{'ready','remoteVerified','checkedAt','detail','binary','version','model'}
            or any(type(row.get(key,False)) is not bool for key in ('ready','remoteVerified'))
            or any(not isinstance(row.get(key,''),str) for key in ('checkedAt','detail','binary','version','model')) for row in checks.values())):
        raise native_plugins.PluginError(tr('El estado guardado de proveedores Pi no tiene formato compatible.'))
    catalog=config.get('piNousCatalog')
    if catalog is not None and (not isinstance(catalog,dict) or catalog.get('accountVerified') is not False
            or catalog.get('quotaVerified') is not False or not isinstance(catalog.get('models'),list)
            or len(catalog['models'])>100 or not isinstance(catalog.get('checkedAt'),str)):
        raise native_plugins.PluginError(tr('El catálogo Nous no es compatible; no acredita una conexión real.'))
    if catalog:
        expected={'id','name','contextWindow','maxTokens','reasoning','cost','input'}
        for row in catalog['models']:
            if (not isinstance(row,dict) or set(row)!=expected or not isinstance(row['id'],str)
                    or not row['id'].endswith(':free') or len(row['id'])>160
                    or not isinstance(row['name'],str) or len(row['name'])>160
                    or type(row['contextWindow']) is not int or not 1024<=row['contextWindow']<=10_000_000
                    or type(row['maxTokens']) is not int or not 1<=row['maxTokens']<=row['contextWindow']
                    or type(row['reasoning']) is not bool or row['input']!=['text']
                    or not isinstance(row['cost'],dict) or set(row['cost'])!={'input','output','cacheRead','cacheWrite'}
                    or any(type(value) not in (int,float) or value!=0 for value in row['cost'].values())):
                raise native_plugins.PluginError(tr('El catálogo Nous contiene un modelo sin precio cero comprobado.'))
    record=config.get('piNousManaged')
    if record is not None and (not isinstance(record,dict) or set(record)!={'digest'} or not isinstance(record['digest'],str)):
        raise native_plugins.PluginError(tr('El registro Nous gestionado no es válido.'))
    managed=config.get('piManagedModel')
    if managed is not None and (not isinstance(managed,dict) or set(managed)!={'model','previous'}
            or not isinstance(managed['model'],str) or not isinstance(managed['previous'],dict)
            or set(managed['previous'])-{'defaultProvider','defaultModel'}
            or any(not isinstance(value,str) for value in managed['previous'].values())):
        raise native_plugins.PluginError(tr('El modelo gestionado de Pi no tiene formato válido.'))
    return {'xai':providers.get('xai',False),'nous':providers.get('nous',False)}


def provider_choice(config,provider,enabled,confirmed=False,settings_file=None):
    providers=provider_settings(config)
    if provider not in providers or type(enabled) is not bool:
        raise native_plugins.PluginError(tr('Proveedor Pi no admitido.'))
    if enabled and provider=='xai' and not (confirmed or config.get('piPaidApproved')):
        raise native_plugins.PluginError(tr('xAI requiere consentimiento explícito para utilizar una API externa de pago.'))
    if provider=='nous' and not enabled:
        remove_nous(config)
        if settings_file:
            import nous_quota
            nous_quota.configure(settings_file,False)
    if provider=='xai' and not enabled:
        record=config.get('piManagedModel')
        if record:
            original=read('settings.json');expected={'defaultProvider':'xai','defaultModel':record['model'].split('/',1)[1]}
            if all(original.get(key)==value for key,value in expected.items()):
                updated=dict(original)
                for key in expected:
                    if key in record['previous']:updated[key]=record['previous'][key]
                    else:updated.pop(key,None)
                write('settings.json',original,updated)
            # A changed native default belongs to the user; disabling only
            # retires our integration and does not block their own CLI choice.
            config.pop('piManagedModel',None)
    providers[provider]=enabled;config['piProviders']=providers
    if provider=='xai':config['piPaidApproved']=enabled and (confirmed or config.get('piPaidApproved',False))
    # A newly configured connection needs its own check, even if the CLI has
    # not changed. Native credentials remain exclusively in Pi.
    config.get('piProviderChecks',{}).pop(provider,None)


def default_model(config,model):
    if not provider_settings(config)['xai'] or not config.get('piPaidApproved') or not model.startswith('xai/'):
        raise native_plugins.PluginError(tr('Activa xAI con consentimiento antes de elegir su modelo.'))
    catalog=config.get('agentCatalogs',{}).get('pi',{})
    if not any(row.get('id')==model for row in catalog.get('models',[])):
        raise native_plugins.PluginError(tr('Consulta el catálogo de Pi antes de elegir ese modelo.'))
    original=read('settings.json');record=config.get('piManagedModel')
    if record and (original.get('defaultProvider')!='xai' or original.get('defaultModel')!=record['model'].split('/',1)[1]):
        raise native_plugins.PluginError(tr('El predeterminado nativo cambió fuera del panel; se conserva.'))
    previous=record['previous'] if record else {key:original[key] for key in ('defaultProvider','defaultModel') if key in original}
    updated={**original,'defaultProvider':'xai','defaultModel':model.split('/',1)[1]}
    write('settings.json',original,updated)
    config['piManagedModel']={'model':model,'previous':previous}


def check_provider(binary,provider):
    if provider not in ('xai','nous'):raise native_plugins.PluginError(tr('Proveedor Pi no admitido.'))
    result=subprocess.run([binary,'auth','check','--provider',provider,'--json','--no-refresh'],cwd=Path.home(),
                          env={**os.environ,'PI_CODING_AGENT_DIR':str(home()),'PI_OFFLINE':'1'},capture_output=True,text=True,timeout=20)
    try:value=json.loads(result.stdout)
    except ValueError as error:raise native_plugins.PluginError(tr('Pi no devolvió el estado de autenticación esperado.')) from error
    from datetime import datetime,timezone
    # No --credentials, no bearer/API-key printing, no automatic refresh.
    ready=result.returncode==0 and value.get('status')=='ready'
    return {'ready':ready,'remoteVerified':False,'checkedAt':datetime.now(timezone.utc).isoformat(),
            'detail':tr('Credenciales locales disponibles; conexión remota pendiente.') if ready else tr('Autenticación necesaria en el menú /login de Pi. No se copian claves ni cuentas.')}


NOUS_URL='https://inference-api.nousresearch.com/v1'


def free_nous_catalog(value):
    from decimal import Decimal,InvalidOperation
    from datetime import datetime,timezone
    rows=[]
    if not isinstance(value,dict) or not isinstance(value.get('data'),list):
        raise native_plugins.PluginError(tr('Nous no devolvió un catálogo compatible.'))
    for row in value['data']:
        if not isinstance(row,dict):continue
        identifier=row.get('id','');pricing=row.get('pricing',{})
        if (not isinstance(identifier,str) or not identifier.endswith(':free') or len(identifier)>160
                or any(ord(char)<32 for char in identifier) or not isinstance(pricing,dict)
                or not {'prompt','completion'}.issubset(pricing) or not isinstance(row.get('supported_parameters'),list) or 'tools' not in row['supported_parameters']):
            continue
        try:
            prices=[Decimal(str(price)) for price in pricing.values()]
            if not all(price.is_finite() and price==0 for price in prices):continue
        except (InvalidOperation,TypeError,ValueError):continue
        context=row.get('context_length')
        if type(context) is not int or not 1024<=context<=10_000_000:continue
        top=row.get('top_provider',{})
        if not isinstance(top,dict):continue
        maximum=top.get('max_completion_tokens',4096)
        if type(maximum) is not int or maximum<1:continue
        rows.append({'id':identifier,'name':str(row.get('name',identifier))[:160],
                     'contextWindow':context,'maxTokens':min(maximum,context,16384),
                     'reasoning':'reasoning' in row.get('supported_parameters',[]),
                     'cost':{'input':0,'output':0,'cacheRead':0,'cacheWrite':0},'input':['text']})
    if not rows:raise native_plugins.PluginError(tr('Nous no confirmó modelos :free con todos los precios cero y herramientas; no habrá sustitución de pago.'))
    return {'models':rows,'checkedAt':datetime.now(timezone.utc).isoformat(),'url':NOUS_URL+'/models',
            'accountVerified':False,'quotaVerified':False,'safetyMarginPercent':25,
            'detail':tr('Catálogo público de precio cero; autenticación, acceso y cuota efectivos pendientes. Margen objetivo 25 %, todavía sin límite numérico al que aplicarlo.')}


def refresh_nous(config,settings_file=None):
    import urllib.request,hashlib
    with urllib.request.urlopen(NOUS_URL+'/models',timeout=20) as response:
        content=response.read(4*1024*1024+1)
    if len(content)>4*1024*1024:raise native_plugins.PluginError(tr('El catálogo Nous supera el límite de lectura.'))
    catalog=free_nous_catalog(json.loads(content))
    original=read('models.json');providers=original.get('providers',{})
    if not isinstance(providers,dict):raise native_plugins.PluginError(tr('Los proveedores Pi nativos no son compatibles; se conservan.'))
    existing=providers.get('nous');record=config.get('piNousManaged')
    digest=lambda value:hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
    own=bool(existing and record and record.get('digest')==digest(existing))
    if existing and not own:
        if existing.get('baseUrl','').rstrip('/')!=NOUS_URL or existing.get('api')!='openai-completions':
            raise native_plugins.PluginError(tr('El proveedor nous ya existe con otra conexión; se conserva sin sobrescribir.'))
        known={row.get('id') for row in existing.get('models',[]) if isinstance(row,dict)}
        catalog['models']=[row for row in catalog['models'] if row['id'] in known]
        if not catalog['models']:raise native_plugins.PluginError(tr('La conexión Nous existente no declara los modelos gratuitos comprobados; se conserva.'))
    else:
        definition={'baseUrl':NOUS_URL,'api':'openai-completions','apiKey':'${NOUS_API_KEY}','models':catalog['models']}
        updated={**original,'providers':{**providers,'nous':definition}}
        write('models.json',original,updated)
        config['piNousManaged']={'digest':digest(definition)}
    if settings_file:
        import nous_quota
        nous_quota.configure(settings_file,True)
    config['piNousCatalog']=catalog
    return catalog


def remove_nous(config):
    import hashlib
    record=config.get('piNousManaged')
    if not record:return
    original=read('models.json');providers=original.get('providers',{});existing=providers.get('nous')
    if existing and hashlib.sha256(json.dumps(existing,sort_keys=True).encode()).hexdigest()==record.get('digest'):
        updated=json.loads(json.dumps(original));updated['providers'].pop('nous');write('models.json',original,updated)
    config.pop('piNousManaged',None)  # Foreign edits belong to the user.


def nous_model_allowed(config,model,require_connection=True,settings_file=None):
    from datetime import datetime,timezone
    if not provider_settings(config)['nous']:raise native_plugins.PluginError(tr('Nous no está habilitado.'))
    catalog=config.get('piNousCatalog',{})
    try:age=(datetime.now(timezone.utc)-datetime.fromisoformat(catalog['checkedAt'])).total_seconds()
    except (ValueError,KeyError,TypeError):age=-1
    if not 0<=age<=24*3600 or not any('nous/'+row['id']==model for row in catalog.get('models',[])):
        raise native_plugins.PluginError(tr('El catálogo gratuito Nous no está vigente; actualízalo antes de asignar la etapa. No habrá fallback de pago.'))
    provider=read('models.json').get('providers',{}).get('nous',{})
    if (provider.get('baseUrl','').rstrip('/')!=NOUS_URL or provider.get('api')!='openai-completions'
            or not any('nous/'+row.get('id','')==model for row in provider.get('models',[]) if isinstance(row,dict))):
        raise native_plugins.PluginError(tr('La declaración Nous no coincide con el endpoint/modelo gratuito comprobado; se conserva sin enviar datos.'))
    if require_connection:
        import nous_quota
        if not nous_quota.settings(config)['requestsPerMinute']:
            raise native_plugins.PluginError(tr('Falta el RPM oficial conocido de la cuenta Nous; no se inventa una cuota.'))
        if not config.get('piProviderChecks',{}).get('nous',{}).get('ready'):
            raise native_plugins.PluginError(tr('Comprueba la autenticación Nous en el panel antes de asignar una etapa.'))
        if not settings_file or not nous_quota.inspect(settings_file):
            raise native_plugins.PluginError(tr('La extensión nativa de cuota Nous no está comprobada; reconecta y abre sesiones nuevas.'))


def probe_provider(binary,config,provider,model,settings_file):
    if not provider_settings(config).get(provider) or not model.startswith(provider+'/'):
        raise native_plugins.PluginError(tr('El proveedor/modelo no está habilitado.'))
    if provider=='xai' and (not config.get('piPaidApproved') or not any(row.get('id')==model for row in config.get('agentCatalogs',{}).get('pi',{}).get('models',[]))):
        raise native_plugins.PluginError(tr('Falta consentimiento o catálogo para probar ese modelo externo de pago.'))
    if provider=='nous':nous_model_allowed(config,model,settings_file=settings_file)
    args=[binary,'--print','--mode','json','--no-session','--offline','--no-tools','--no-extensions',
          '--no-mcp','--no-context-files','--no-skills','--no-prompt-templates','--no-themes','--no-approve',
          '--system-prompt','Connection check only. Respond with OK.', '--append-system-prompt','',
          '--thinking','off','--model',model]
    if provider=='nous':
        import nous_quota
        args+=['--extension',str(nous_quota.paths(settings_file)[0])]
    with tempfile.TemporaryDirectory(prefix='ai-team-pi-connection-') as directory:
        result=subprocess.run(args,cwd=directory,input='Respond with OK.\n',capture_output=True,text=True,timeout=120,
                              env={**os.environ,'PI_CODING_AGENT_DIR':str(home()),'PI_OFFLINE':'1'})
    try:
        if result.returncode or len(result.stdout)>4*1024*1024:raise ValueError()
        messages=[row['message'] for line in result.stdout.splitlines() if (row:=json.loads(line)).get('type')=='message_end' and row.get('message',{}).get('role')=='assistant']
        final=messages[-1]
        if final.get('stopReason') in ('error','aborted') or not any(part.get('type')=='text' and part.get('text','').strip() for part in final.get('content',[])):
            raise ValueError()
    except (ValueError,KeyError,TypeError,IndexError) as error:
        raise native_plugins.PluginError(tr('Pi no completó la comprobación remota. No se guardó su respuesta ni se cambió de proveedor.')) from error
    from datetime import datetime,timezone
    return {'ready':True,'remoteVerified':True,'checkedAt':datetime.now(timezone.utc).isoformat(),'model':model,
            'detail':tr('Conexión nativa comprobada mediante consulta fija sin herramientas, MCP ni contexto del proyecto. No acredita calidad de tareas ni todas las cuotas.')}

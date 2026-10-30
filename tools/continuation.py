#!/usr/bin/env python3
"""One-shot native continuation: verify quota before starting any model."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
DIRECTORY=ROOT/'artifacts/continuity'
STATE=DIRECTORY/'handoff.json'


def check_paths():
    if STATE.is_symlink() or any(p.is_symlink() for p in (DIRECTORY,*DIRECTORY.parents)):
        raise ValueError('El destino del relevo contiene un enlace; se conserva sin modificar.')


def read_state():
    check_paths()
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def write_state(value):
    check_paths()
    DIRECTORY.mkdir(mode=0o700,parents=True,exist_ok=True)
    os.chmod(DIRECTORY,0o700)
    temporary=STATE.with_name('handoff-'+uuid.uuid4().hex+'.tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as out:json.dump(value,out,indent=2,ensure_ascii=False)
    os.replace(temporary,STATE)


def quota(profile):
    result=subprocess.run([sys.executable,str(ROOT/'tools/session_usage.py'),'--profile',profile],
                          text=True,capture_output=True,timeout=30)
    value=json.loads(result.stdout)
    if result.returncode or not value.get('ok'):raise ValueError('No se confirmó la cuota de '+profile)
    return value


def choose(profile,windows,now=None):
    now=time.time() if now is None else now
    order=[profile,'codex-2' if profile=='codex' else 'codex']
    for name in order:
        if name in windows and windows[name].get('availableForContinuation',windows[name]['usedPercent']<90):return name,None
    candidates=[(value.get('continuationResetsAt',value.get('resetsAt')),name) for name,value in windows.items()
                if type(value.get('continuationResetsAt',value.get('resetsAt'))) in (int,float)
                and value.get('continuationResetsAt',value.get('resetsAt'))>now]
    if not candidates:raise ValueError('No hay cuota ni hora oficial futura verificada; no se inició un modelo.')
    reset,name=min(candidates)
    return name,reset+60


def schedule(profile,at,dry_run=False):
    if at<=time.time():raise ValueError('La hora de continuación debe ser futura.')
    run_id=uuid.uuid4().hex[:12];unit='ai-team-continuation-'+run_id
    calendar=datetime.fromtimestamp(at,timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    command=['systemd-run','--user','--collect','--unit='+unit,'--on-calendar='+calendar,
             '--timer-property=Persistent=true','--timer-property=RemainAfterElapse=false',
             '--expand-environment=no','--working-directory='+str(ROOT),'--property=UMask=0077',
             '--setenv=PATH='+os.environ.get('PATH',os.defpath),sys.executable,str(Path(__file__).resolve()),
             'run','--profile',profile,'--run-id',run_id]
    if dry_run:return {'dryRun':True,'profile':profile,'atUtc':calendar,'command':command}
    previous=read_state()
    value={'status':'scheduling','profile':profile,'runId':run_id,'unit':unit,
           'scheduledAt':at,'scheduledUtc':calendar,'createdAt':time.time(),'previousRunId':previous.get('runId')}
    write_state(value)
    result=subprocess.run(command,text=True,capture_output=True,timeout=15)
    value.update(status='scheduled' if result.returncode==0 else 'schedule-failed')
    write_state(value)
    if result.returncode:raise ValueError('systemd no confirmó el timer: '+result.stderr[:300])
    return value


def run(profile,run_id):
    original=read_state()
    if original.get('runId')!=run_id or original.get('status') not in ('scheduled','scheduling'):return {'status':'superseded','modelStarted':False}
    windows={}
    for name in ('codex','codex-2'):
        try:windows[name]=quota(name)
        except (OSError,ValueError,subprocess.TimeoutExpired):pass
    target,at=choose(profile,windows)
    if at is not None or target!=profile:
        return schedule(target,at or time.time()+5)
    point=(ROOT/'docs/CONTINUIDAD.md').read_text()
    if len(point.encode())>128*1024:raise ValueError('El punto de continuación supera el límite seguro de contexto.')
    prompt=('Continúa el proyecto omarchy-ai-team hasta terminar las implementaciones autorizadas. '
            'Lee AGENTS.md; no repitas investigación o pruebas ya cerradas salvo cambios o fallos. '
            'Usa herramientas locales y modelos ligeros en pruebas cuando basten. '
            'Al90% de5h guarda todo y pasa al otro perfil; si ambos agotados usa su reset oficial y '
            'tools/continuation.py schedule para otra continuación. No escriban dos agentes a la vez. '
            'No copies credenciales, cambies cuentas o eludas aprobaciones. '
            'La cuota histórica del punto puede haber cambiado; esta medición es actual: '+json.dumps(windows[profile])+
            '\nPunto incluido íntegro, además del archivo del repositorio:\n'+point)
    sys.path.insert(0,str(ROOT/'backend'));import harness
    inventory,_=harness.mise_inventory();tool=harness.detect_tool('codex',inventory)
    if tool['installation']!='installed':raise ValueError('No hay una CLI instalada; no se usará un instalador automático.')
    env={**os.environ,'CODEX_HOME':str(Path.home()/('.codex' if profile=='codex' else '.codex-2'))}
    events=DIRECTORY/(run_id+'-events.jsonl');errors=DIRECTORY/(run_id+'-stderr.log')
    original.update(status='starting',actualQuota=windows[profile],pointSha256=hashlib.sha256(point.encode()).hexdigest(),
                    pointIncludedInPrompt=True,events=str(events),stderr=str(errors))
    write_state(original)
    for path in (events,errors):
        descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(descriptor)
    with events.open('a') as out,errors.open('a') as err:
        process=subprocess.Popen([tool['binary'],'exec','--approve-for-me','--json','-C',str(ROOT),prompt],
                                 cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=err,text=True)
        for line in process.stdout:
            out.write(line);out.flush()
            try:event=json.loads(line)
            except ValueError:continue
            if event.get('type')=='thread.started' and read_state().get('runId')==run_id:
                original.update(status='running',threadId=event.get('thread_id'),startedAt=time.time())
                write_state(original)
        process.stdout.close();code=process.wait()
    if read_state().get('runId')==run_id:
        original.update(status='finished' if code==0 else 'failed',exitCode=code,endedAt=time.time());write_state(original)
    return {'exitCode':code,'modelStarted':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    timer=commands.add_parser('schedule');timer.add_argument('--profile',choices=('codex','codex-2'),required=True)
    timer.add_argument('--at',type=float,required=True);timer.add_argument('--dry-run',action='store_true')
    worker=commands.add_parser('run');worker.add_argument('--profile',choices=('codex','codex-2'),required=True)
    worker.add_argument('--run-id',required=True)
    args=parser.parse_args()
    try:
        result=schedule(args.profile,args.at,args.dry_run) if args.command=='schedule' else run(args.profile,args.run_id)
        print(json.dumps(result,ensure_ascii=False));return result.get('exitCode',0)
    except (OSError,ValueError,subprocess.TimeoutExpired) as error:
        current=read_state()
        if args.command=='run' and current.get('runId')==args.run_id:
            current.update(status='preflight-failed',error=str(error));write_state(current)
        print(json.dumps({'ok':False,'error':str(error)},ensure_ascii=False));return 1


if __name__=='__main__':sys.exit(main())

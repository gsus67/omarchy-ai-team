"""On-demand verified ECC backups and an optional native systemd user timer."""

from i18n import tr

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

import memory_archive
import shared_memory


class BackupError(memory_archive.ArchiveError):
    pass


MARKER='.ai-team-backup-target'
UNIT='omarchy-ai-team-backup'


def target_marker(folder):
    return memory_archive.safe_path(folder/MARKER)


def settings(config):
    if not isinstance(config.get('backup',{}),dict):raise BackupError(tr('Los ajustes de respaldos no son válidos.'))
    value={'destination':'','hours':24,'keep':7,'enabled':False,**config.get('backup',{})}
    if (not isinstance(value['destination'],str) or type(value['enabled']) is not bool
            or type(value['hours']) is not int or value['hours'] not in (1,6,12,24,168)
            or type(value['keep']) is not int or not 1<=value['keep']<=365):
        raise BackupError(tr('Los ajustes de respaldos no son válidos; se conservan.'))
    if value['destination'] and not Path(value['destination']).is_absolute():raise BackupError(tr('El destino guardado debe ser absoluto.'))
    if not isinstance(value.get('unitHashes',{}),dict):raise BackupError(tr('El registro de programación no es válido.'))
    return value


def state_path():
    return shared_memory.data_root()/'backups/state.json'


def state():
    path=memory_archive.safe_path(state_path())
    if not path.exists():return {}
    value=json.loads(memory_archive.read_file(path,2*1024*1024))
    if not isinstance(value,dict) or any(type(value[k]) not in (int,float) or not math.isfinite(value[k]) or value[k]<0 for k in ('lastSuccess','lastAttempt') if k in value):raise BackupError(tr('El estado de respaldos no es válido.'))
    return value


def _write(path,content,mode=0o600):
    path=memory_archive.safe_path(path);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.ai-team-',delete=False) as out:
            temporary=Path(out.name);os.chmod(temporary,mode);out.write(content);out.flush();os.fsync(out.fileno())
        os.replace(temporary,path)
    finally:
        if temporary:temporary.unlink(missing_ok=True)


def save_state(value):
    _write(state_path(),json.dumps(value,ensure_ascii=False,indent=2).encode())


@contextmanager
def lock():
    path=memory_archive.safe_path(state_path().with_name('operation.lock'));path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error:raise BackupError(tr('Hay otra operación de memoria en curso; reintenta al terminar.')) from error
        yield
    finally:os.close(fd)


def configure(config,destination,hours,keep,enabled):
    destination=str(Path(destination).expanduser()) if destination else ''
    updated={**config.get('backup',{}),'destination':destination,'hours':hours,'keep':keep,'enabled':enabled}
    value=settings({'backup':updated})
    if destination:
        folder=memory_archive.safe_path(Path(destination).expanduser().resolve())
        if folder.is_relative_to(shared_memory.data_root()/'memory'):
            raise BackupError(tr('Elige una carpeta fuera del almacén de memoria.'))
        # Existing settings can be changed while the volume is disconnected.
        previous=config.get('backup',{})
        if str(folder)!=previous.get('destination') or not previous.get('targetId'):
            if not folder.is_dir():raise BackupError(tr('Elige una carpeta de destino existente.'))
            marker=target_marker(folder)
            if marker.exists():
                token=memory_archive.read_file(marker,128).decode().strip()
                try:token=str(uuid.UUID(token))
                except ValueError as error:raise BackupError(tr('El identificador del destino es ajeno; se conserva.')) from error
            else:
                token=str(uuid.uuid4())
                fd=os.open(marker,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
                with os.fdopen(fd,'w') as out:out.write(token+'\n');out.flush();os.fsync(out.fileno())
            value.update(destination=str(folder),targetId=token)
    elif enabled:
        raise BackupError(tr('Configura el destino antes de activar respaldos automáticos.'))
    value.pop('timerCheckedAt',None)
    config['backup']=value
    return value


def _destination(value):
    if not value['destination'] or not value.get('targetId'):
        raise BackupError(tr('El destino de respaldos aún no está configurado.'))
    folder=memory_archive.safe_path(value['destination']);marker=target_marker(folder)
    if not folder.is_dir() or not marker.exists() or memory_archive.read_file(marker,128).decode().strip()!=value['targetId']:
        raise BackupError(tr('El destino está desconectado o no coincide; no se escribió en una carpeta alternativa.'))
    return folder


def prune(folder,runtime,target_id,keep,newest):
    candidates=[*folder.glob('ai-team-memory-*.zip')]
    if len(candidates)>1000:raise BackupError(tr('Demasiados archivos para aplicar retención; se conservan.'))
    valid=[]
    for path in candidates:
        if not re.fullmatch(r'ai-team-memory-\d{8}T\d{6}Z-[a-f0-9]{12}\.zip',path.name) or path==newest:continue
        try:
            _,fingerprint,manifest=memory_archive.load(path,runtime,include_manifest=True)
            if manifest.get('backupTarget')==target_id:
                try:created=datetime.fromisoformat(manifest['createdAt']).timestamp()
                except (ValueError,KeyError,TypeError):continue
                valid.append((path,fingerprint,created))
        except (OSError,memory_archive.ArchiveError):continue
    valid.sort(key=lambda item:item[2],reverse=True)
    removed=0
    for path,fingerprint,_ in valid[max(0,keep-1):]:
        if memory_archive.digest(memory_archive.read_file(path,memory_archive.MAX_BYTES+4*1024*1024))==fingerprint:
            path.unlink();removed+=1
    return removed


def notify_failure():
    binary=shutil.which('notify-send')
    if not binary:return
    try:
        subprocess.run([binary,'-a','AI Team',tr('Respaldo de memoria pendiente'),
                        tr('No se eliminaron copias anteriores. Revisa el motivo en AI Team.')],
                       capture_output=True,timeout=3)
    except (OSError,subprocess.TimeoutExpired):pass


def run(config,runtime,scheduled=False):
    value=settings(config)
    if scheduled and not value['enabled']:return {'skipped':'disabled'}
    with lock():
        previous=state()
        if scheduled and 0<=time.time()-previous.get('lastSuccess',0)<value['hours']*3600:
            return {'skipped':'not-due'}
        try:
            folder=_destination(value)
            name='ai-team-memory-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:12]+'.zip'
            result=memory_archive.export(shared_memory.data_root()/'memory',folder/name,runtime,target_id=value['targetId'])
            current={**previous,'lastSuccess':time.time(),'lastArchive':result['file'],'lastSha256':result['sha256'],
                     'lastError':'','retentionError':'','memoryCount':result['memoryCount'],'lastAttempt':time.time()}
            save_state(current)
        except (OSError,ValueError,subprocess.TimeoutExpired,memory_archive.ArchiveError) as error:
            try:save_state({**previous,'lastAttempt':time.time(),'lastError':str(error)})
            except OSError:pass  # Preserve the old state if its filesystem is unavailable.
            if scheduled and previous.get('lastError')!=str(error):notify_failure()
            raise BackupError(str(error)) from error
        if not result.get('directorySynced',True):
            result['warning']=tr('Se conservaron las copias anteriores: el destino no confirmó su persistencia.')
            save_state({**current,'retentionError':result['warning']})
            return result
        try:
            result['removedBackups']=prune(folder,runtime,value['targetId'],value['keep'],Path(result['file']))
        except (OSError,memory_archive.ArchiveError) as error:
            result['warning']=tr('La copia está verificada; la retención quedó pendiente.')
            save_state({**current,'retentionError':str(error)})
        return result


def _quote(value):
    if any(c in value for c in '\n\r\0'):raise BackupError(tr('La ruta no es válida para systemd.'))
    return '"'+value.replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'


def _units(script):
    command=' '.join(_quote(str(v)) for v in (sys.executable,script,'memory-backup','--scheduled'))
    env=' '.join(_quote(k+'='+str(v)) for k,v in {'XDG_CONFIG_HOME':os.environ.get('XDG_CONFIG_HOME',Path.home()/'.config'),
             'XDG_DATA_HOME':os.environ.get('XDG_DATA_HOME',Path.home()/'.local/share')}.items())
    prefix='# AI Team managed backup v1\n'
    return {'.service':prefix+'[Unit]\nDescription=Verified ECC memory backup\n[Service]\nType=oneshot\nUMask=0077\nNoNewPrivileges=true\nTimeoutStartSec=10min\nEnvironment='+env+'\nExecStart=:'+command+'\n',
            '.timer':prefix+'[Unit]\nDescription=Retry due ECC memory backups\n[Timer]\nOnCalendar=hourly\nPersistent=true\nRandomizedDelaySec=5m\n[Install]\nWantedBy=timers.target\n'}


def apply_timer(config,script):
    value=settings(config);folder=memory_archive.safe_path(Path(os.environ.get('XDG_CONFIG_HOME',Path.home()/'.config'))/'systemd/user')
    expected=_units(script);previous=value.get('unitHashes',{});entries=[]
    for suffix,text in expected.items():
        path=memory_archive.safe_path(folder/(UNIT+suffix));data=text.encode();original=path.read_bytes() if path.exists() else None
        if original is not None and original!=data and memory_archive.digest(original)!=previous.get(suffix):
            raise BackupError(tr('Una unidad systemd contiene cambios ajenos; se conserva sin modificar.'))
        entries.append((suffix,path,original,data))
    if value['enabled']:
        for _,path,original,data in entries:
            if (path.read_bytes() if path.exists() else None)!=original:raise BackupError(tr('La unidad cambió durante la operación.'))
            _write(path,data)
    commands=[]
    if value['enabled'] or any(original is not None for _,_,original,_ in entries):
        commands=[['systemctl','--user','daemon-reload'],['systemctl','--user',*(['enable','--now'] if value['enabled'] else ['disable','--now']),UNIT+'.timer']]
    for command in commands:
        result=subprocess.run(command,capture_output=True,text=True,timeout=15)
        if result.returncode:raise BackupError(tr('systemd no confirmó el cambio; la programación sigue pendiente.'))
    if not value['enabled']:
        for _,path,original,_ in entries:
            if original is not None:
                if path.read_bytes()!=original:raise BackupError(tr('La unidad cambió; se conserva.'))
                path.unlink()
        if commands:
            result=subprocess.run(['systemctl','--user','daemon-reload'],capture_output=True,text=True,timeout=15)
            if result.returncode:raise BackupError(tr('systemd no confirmó retirar las unidades; vuelve a comprobar.'))
    value['unitHashes']={suffix:memory_archive.digest(data) for suffix,_,_,data in entries} if value['enabled'] else {}
    value['timerCheckedAt']=time.time();config['backup']=value
    return value


def timer_status(config,script):
    value=settings(config)
    folder=Path(os.environ.get('XDG_CONFIG_HOME',Path.home()/'.config'))/'systemd/user'
    try:
        if not value['enabled']:
            ready=not any((folder/(UNIT+suffix)).exists() for suffix in ('.timer','.service'))
        else:
            ready=bool(value.get('timerCheckedAt')) and all(
                memory_archive.read_file(folder/(UNIT+suffix),65536)==text.encode()
                and value.get('unitHashes',{}).get(suffix)==memory_archive.digest(text.encode())
                for suffix,text in _units(script).items())
        return {'prepared':ready,'detail':tr('Programación registrada.') if value['enabled'] and ready else
                tr('Respaldos automáticos desactivados.') if ready else tr('Programación pendiente de aplicar.')}
    except (OSError,memory_archive.ArchiveError):
        return {'prepared':False,'detail':tr('Programación pendiente de comprobar.')}

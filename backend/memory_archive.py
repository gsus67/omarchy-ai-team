"""Portable snapshots of native ECC documents, validated by the official runtime."""

from i18n import tr

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile

import native_plugins


class ArchiveError(native_plugins.PluginError):
    pass


FORMAT = 'ai-team-ecc-backup-v1'
SHARED_KEY = hashlib.sha256(b'ai-team-shared-memory').hexdigest()  # Fixed identity of `compartido` in memory_projects.mjs.
MAX_FILES = 10000
MAX_BYTES = 64*1024*1024
MAX_DOCUMENT = 128*1024
PATH = re.compile(r'projects/[a-f0-9]{64}/(?:project|team)/(?:contexts|decisions|facts|handoffs|lessons|notes|preferences|runbooks)/mem_[a-z0-9_-]{3,128}\.md\Z')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(path):
    path = Path(path)
    if not path.is_absolute():
        raise ArchiveError(tr('La ruta debe ser absoluta.'))
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ArchiveError(tr('La ruta contiene un enlace; no se seguirá ni modificará.'))
    return path


def read_file(path, limit):
    path = safe_path(path)
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,'rb') as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise ArchiveError(tr('El archivo no es regular o supera el tamaño admitido.'))
            content = source.read(limit+1)
            after = os.fstat(source.fileno())
        if len(content) > limit or (before.st_size,before.st_mtime_ns) != (after.st_size,after.st_mtime_ns):
            raise ArchiveError(tr('El archivo cambió durante la lectura; reintenta.'))
        return content
    except OSError as error:
        raise ArchiveError(tr('No se pudo leer un archivo de memoria.')) from error


def project_name(directory):
    metadata=directory/'.ai-team-project.json'
    if not metadata.is_file() or metadata.is_symlink():return None
    try:
        value=json.loads(read_file(metadata,4096))
        if isinstance(value,dict) and value.get('schemaVersion')==1 and isinstance(value.get('name'),str):
            # Same limits as restore and the MCP bridge: this text reaches the panel and the manifest.
            name=''.join(c for c in value['name'] if not (ord(c)<32 or 127<=ord(c)<160)).strip()[:120]
            return name or None
    except (OSError,ValueError,ArchiveError):pass
    return None


def collect(store, project='all'):
    store = safe_path(store)
    if project != 'all' and not re.fullmatch('[a-f0-9]{64}',project):
        raise ArchiveError(tr('El proyecto del archivo no es válido.'))
    folder = safe_path(store/'projects'/(project if project!='all' else ''))
    files = {}; total = 0
    if not folder.exists():
        return files
    for count, path in enumerate(folder.rglob('*')):
        if count > MAX_FILES*5:
            raise ArchiveError(tr('Hay demasiados archivos para una copia acotada.'))
        safe_path(path)
        if not path.is_file():
            if not path.is_dir():raise ArchiveError(tr('El almacén contiene un tipo de archivo no admitido.'))
            continue
        name = path.relative_to(store).as_posix()
        if not PATH.fullmatch(name):
            if path.suffix == '.md':
                raise ArchiveError(tr('Hay un documento fuera del formato de rutas de ECC.'))
            continue  # Never include settings, credentials, metadata, or other files.
        if project != 'all' and name.split('/')[1] != project:
            continue
        data = read_file(path, MAX_DOCUMENT); total += len(data)
        if total > MAX_BYTES or len(files) >= MAX_FILES:
            raise ArchiveError(tr('La memoria supera el límite de tamaño de una copia.'))
        files[name] = data
    return files


def stage(directory, files):
    (directory/'projects').mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        target = directory/name; target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(data); target.chmod(0o600)


def audit(directory, runtime):
    if not runtime.get('installed') or not runtime.get('node'):
        raise ArchiveError(tr('Activa o comprueba el runtime ECC antes de gestionar copias.'))
    command = [runtime['node'],str(Path(__file__).with_name('memory_audit.cjs')),runtime['path'],str(directory)]
    try:result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired as error:raise ArchiveError(tr('ECC no terminó la auditoría a tiempo; no se cambió la memoria.')) from error
    if result.returncode:
        raise ArchiveError(tr('La auditoría oficial de ECC detectó documentos inválidos, incompletos o posibles secretos.'))
    try:
        value = json.loads(result.stdout)
        if type(value['memoryCount']) is not int:
            raise ValueError()
        return value
    except (ValueError, KeyError, TypeError) as error:
        raise ArchiveError(tr('ECC no confirmó el número de memorias.')) from error


def validate_documents(files, runtime):
    with tempfile.TemporaryDirectory(prefix='ai-team-memory-audit-') as temporary:
        directory = Path(temporary); stage(directory,files)
        result = audit(directory,runtime)
    if result['memoryCount'] != len(files):
        raise ArchiveError(tr('ECC no reconoció todos los documentos; no se continuará.'))


def sync_directory(path):
    descriptor=None
    try:
        descriptor=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
        os.fsync(descriptor)
        return True
    except OSError:
        return False
    finally:
        if descriptor is not None:os.close(descriptor)


def export(store, destination, runtime, project='all', target_id=None):
    store = safe_path(store); destination = safe_path(destination)
    if destination.exists():
        raise ArchiveError(tr('Ya existe un archivo en ese destino; se conserva.'))
    if destination.is_relative_to(store):
        raise ArchiveError(tr('Guarda la copia fuera del almacén de memoria.'))
    files = collect(store,project)
    if not files:
        raise ArchiveError(tr('No hay memorias para exportar en ese ámbito.'))
    validate_documents(files,runtime)
    if collect(store,project) != files:
        raise ArchiveError(tr('La memoria cambió durante la copia; no se publicó ningún respaldo.'))
    manifest = {'format':FORMAT,'createdAt':datetime.now(timezone.utc).isoformat(),
                'eccVersion':runtime['version'],'files':{name:digest(data) for name,data in sorted(files.items())}}
    names={key:name for key in sorted({n.split('/')[1] for n in files})
           if (name:=project_name(store/'projects'/key)) is not None}
    if names:manifest['projectNames']=names
    if target_id:manifest['backupTarget']=target_id
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent,prefix='.ai-team-backup-',delete=False) as out:
            temporary = Path(out.name); os.chmod(temporary,0o600)
        with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json',json.dumps(manifest,sort_keys=True))
            for name,data in files.items():archive.writestr(name,data)
        # Read CRCs, hashes and document contract from the actual ZIP before publishing.
        loaded,_ = load(temporary,runtime)
        if loaded != files:
            raise ArchiveError(tr('La copia no coincide con el contenido verificado.'))
        with temporary.open('rb') as source:os.fsync(source.fileno())
        # Exclusive creation works on local, external and mounted SMB filesystems.
        # An interrupted copy is never recorded as valid or used for retention.
        created=None
        try:
            with os.fdopen(os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'wb') as target,temporary.open('rb') as source:
                created=os.fstat(target.fileno())
                shutil.copyfileobj(source,target);target.flush();os.fsync(target.fileno())
            if read_file(destination,MAX_BYTES+4*1024*1024)!=read_file(temporary,MAX_BYTES+4*1024*1024):
                raise ArchiveError(tr('La copia del destino no coincide con el respaldo verificado.'))
        except (OSError,ArchiveError):
            if created and not destination.is_symlink():
                current=destination.stat() if destination.exists() else None
                if current and (current.st_dev,current.st_ino)==(created.st_dev,created.st_ino):destination.unlink()
            raise
    except FileExistsError as error:
        raise ArchiveError(tr('El destino apareció durante la copia; se conserva.')) from error
    finally:
        if temporary:temporary.unlink(missing_ok=True)
    return {'file':str(destination),'memoryCount':len(files),'projectCount':len({n.split('/')[1] for n in files}),
            'sha256':digest(read_file(destination,MAX_BYTES+4*1024*1024)),
            'directorySynced':sync_directory(destination.parent)}


def load(archive, runtime, include_manifest=False):
    archive = safe_path(archive)
    raw = read_file(archive,MAX_BYTES+4*1024*1024)
    try:
        import io
        with zipfile.ZipFile(io.BytesIO(raw)) as source:
            infos=source.infolist();names=[i.filename for i in infos]
            if len(infos)>MAX_FILES+1 or len(names)!=len(set(names)) or 'manifest.json' not in names:
                raise ArchiveError(tr('El archivo contiene entradas duplicadas o un inventario inválido.'))
            files={};total=0
            for info in infos:
                mode=stat.S_IFMT(info.external_attr>>16)
                if info.flag_bits&1 or mode not in (0,stat.S_IFREG) or info.is_dir() or info.compress_type not in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED):
                    raise ArchiveError(tr('No se admiten enlaces, directorios o archivos cifrados dentro del ZIP.'))
                limit=2*1024*1024 if info.filename=='manifest.json' else MAX_DOCUMENT
                if info.filename!='manifest.json' and not PATH.fullmatch(info.filename):
                    raise ArchiveError(tr('El archivo contiene una ruta no autorizada.'))
                total+=info.file_size
                if info.file_size>limit or total>MAX_BYTES+2*1024*1024:
                    raise ArchiveError(tr('El archivo supera los límites de tamaño.'))
                with source.open(info) as entry:data=entry.read(limit+1)
                if len(data)!=info.file_size or len(data)>limit:
                    raise ArchiveError(tr('El archivo no tiene el tamaño declarado.'))
                files[info.filename]=data
            manifest=json.loads(files.pop('manifest.json'))
            if (not isinstance(manifest,dict) or manifest.get('format')!=FORMAT
                    or manifest.get('files')!={name:digest(data) for name,data in files.items()} or not files):
                raise ArchiveError(tr('Los hashes o el formato del respaldo no coinciden.'))
    except (zipfile.BadZipFile,ValueError,KeyError,RuntimeError,EOFError) as error:
        raise ArchiveError(tr('No se pudo verificar el respaldo; no se restauró nada.')) from error
    validate_documents(files,runtime)
    return (files,digest(raw),manifest) if include_manifest else (files,digest(raw))


def check_map(key_map, keys, target_key):
    """Every move must be explicit and leave each project with its own destination."""
    invalid=ArchiveError(tr('La reasociación no es válida: cada proyecto del archivo necesita una carpeta propia.'))
    if target_key:raise invalid
    new=list(key_map.values())
    if (any(old not in keys or old==SHARED_KEY for old in key_map)
            or any(not re.fullmatch('[a-f0-9]{64}',key) or key==SHARED_KEY for key in new)
            or len(new)!=len(set(new)) or set(new)&(keys-set(key_map))):
        raise invalid


def _plan(archive, store, runtime, target_key=None, key_map=None):
    store=safe_path(store);files,archive_hash,manifest=load(archive,runtime,include_manifest=True)
    keys={name.split('/')[1] for name in files}
    names=manifest.get('projectNames',{})
    names={key:name for key,name in names.items() if key in keys} if isinstance(names,dict) else {}
    original=sorted(keys);labels=dict(names)
    if key_map:check_map(key_map,keys,target_key)
    if target_key:
        if len(keys)!=1 or not re.fullmatch('[a-f0-9]{64}',target_key):
            raise ArchiveError(tr('Solo se puede reasociar un respaldo de un único proyecto.'))
        key_map={original[0]:target_key}
    if key_map:
        files={'projects/'+key_map.get(name.split('/')[1],name.split('/')[1])+'/'+name.split('/',2)[2]:data for name,data in files.items()}
        names={key_map.get(key,key):name for key,name in names.items()}
    key_map=key_map or {}
    current={}
    for key in {name.split('/')[1] for name in files}:current.update(collect(store,key))
    new=[];same=[];conflicts=[]
    for name,data in files.items():
        if name not in current:new.append(name)
        elif current[name]==data:same.append(name)
        else:conflicts.append(name)
    # Validate the resulting vault too: a merge must not introduce duplicate IDs or broken links.
    merged={**current,**files}
    if not conflicts:validate_documents(merged,runtime)
    value={'archive':str(safe_path(archive)),'archiveSha256':archive_hash,'store':str(store),'targetKey':target_key,
           'keyMap':key_map,'new':sorted(new),'identical':sorted(same),'conflicts':sorted(conflicts),
           'current':{n:digest(b) for n,b in sorted(current.items())}}
    token=digest(json.dumps(value,sort_keys=True).encode())
    return files,{'archive':value['archive'],'token':token,'newCount':len(new),'identicalCount':len(same),
                  'conflictCount':len(conflicts),'projectCount':len({n.split('/')[1] for n in files}),
                  'targetKey':target_key,
                  'projects':[{'key':key,'name':labels[key] if isinstance(labels.get(key),str) else '','shared':key==SHARED_KEY,'newKey':key_map.get(key,'')}
                              for key in original]},new,names


def preview(archive, store, runtime, target_key=None, *, key_map=None):
    return _plan(archive,store,runtime,target_key,key_map)[1]


def restore(archive, store, runtime, token, target_key=None, *, key_map=None):
    files,plan,new,names=_plan(archive,store,runtime,target_key,key_map)
    if plan['token']!=token or plan['conflictCount']:
        raise ArchiveError(tr('El plan cambió o hay conflictos. Revisa la vista previa; no se sobrescribió memoria.'))
    written=[]
    try:
        for name in new:
            destination=safe_path(Path(store)/name);destination.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            temporary=None
            try:
                with tempfile.NamedTemporaryFile(dir=destination.parent,prefix='.ai-team-restore-',delete=False) as out:
                    temporary=Path(out.name);os.chmod(temporary,0o600)
                    out.write(files[name]);out.flush();os.fsync(out.fileno())
                os.link(temporary,destination)  # whole document, create-only
                written.append(destination)
            finally:
                if temporary:temporary.unlink(missing_ok=True)
    except (OSError,ArchiveError) as error:
        # Roll back only our exact new files. Never remove another writer's data.
        for path in written:
            try:
                if not path.is_symlink() and read_file(path,MAX_DOCUMENT)==files[path.relative_to(store).as_posix()]:path.unlink()
            except (OSError,ArchiveError):
                pass  # Preserve changes or files we can no longer inspect.
        raise ArchiveError(tr('La restauración no pudo completarse; se retiraron solo las altas propias intactas.')) from error
    for key,name in names.items():
        if not isinstance(name,str) or any(ord(c)<32 or 127<=ord(c)<160 for c in name):continue
        name=name.strip()
        if not name or len(name)>120:continue
        # Optional display metadata: preserve existing files, as the MCP bridge does.
        try:
            metadata=safe_path(Path(store)/'projects'/key/'.ai-team-project.json')
            fd=os.open(metadata,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'w') as out:json.dump({'schemaVersion':1,'name':name},out)
        except (OSError,ArchiveError):pass
    # A backup keeps no folder paths: without a move, nothing proves these projects exist here.
    return {'restoredCount':len(new),'identicalCount':plan['identicalCount'],
            'unverifiedProjects':[row['name'] or row['key'][:10] for row in plan['projects'] if not row['shared'] and not row['newKey']]}


def inventory(store):
    store=safe_path(store);folder=safe_path(store/'projects');projects=[]
    if not folder.is_dir():return projects
    for directory in sorted(folder.iterdir()):
        if not re.fullmatch('[a-f0-9]{64}',directory.name):continue
        safe_path(directory)
        count=sum(1 for p in directory.rglob('*.md') if PATH.fullmatch(p.relative_to(store).as_posix()) and not p.is_symlink() and p.is_file())
        if not count:continue
        label=tr('Proyecto {nombre}', nombre=directory.name[:10])
        name=project_name(directory)
        if name is not None:label=name[:120]+' · '+directory.name[:8]
        projects.append({'key':directory.name,'label':label,'memoryCount':count})
    return projects

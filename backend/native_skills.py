"""Read native skill discovery without a model turn or a session transcript."""

from i18n import tr

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from atomic_files import atomic_write
import memory_archive
import native_plugins


def publish(source,content):
    """Write our generated skill only when it differs; a skill edited by hand is never replaced."""
    source=memory_archive.safe_path(source);marker=source/'.ai-team-managed';file=source/'SKILL.md'
    if source.exists() and any(source.iterdir()):
        try:
            record=json.loads(memory_archive.read_file(marker,4096))
            if record.get('sha256')!=hashlib.sha256(memory_archive.read_file(file,32*1024)).hexdigest():raise ValueError()
        except (ValueError,OSError,native_plugins.PluginError) as error:
            raise native_plugins.PluginError(tr('La skill gestionada está modificada o es ajena; se conserva.')) from error
        if record['sha256']==hashlib.sha256(content.encode()).hexdigest():return
    source.mkdir(parents=True,mode=0o700,exist_ok=True)
    # Config-owned generated files, never an upstream skill or original project.
    atomic_write(file,content)
    atomic_write(marker,json.dumps({'schemaVersion':1,'sha256':hashlib.sha256(content.encode()).hexdigest()}))


def claude_commands(binary):
    request={'type':'control_request','request_id':'ai-team-metadata','request':{'subtype':'initialize'}}
    # Metadata only: do not run SessionStart hooks or start configured MCPs.
    args=[binary,'-p','--input-format','stream-json','--output-format','stream-json','--verbose',
          '--no-session-persistence','--settings',json.dumps({'disableAllHooks':True}),
          '--strict-mcp-config','--mcp-config',json.dumps({'mcpServers':{}})]
    try:
        with tempfile.TemporaryDirectory(prefix='ai-team-skill-discovery-') as directory:
            result=subprocess.run(args,cwd=directory,input=json.dumps(request)+'\n',text=True,
                                  capture_output=True,timeout=20)
        if result.returncode:
            raise ValueError()
        for line in result.stdout.splitlines():
            row=json.loads(line)
            if not isinstance(row,dict):raise ValueError()
            response=row.get('response',{})
            if not isinstance(response,dict):raise ValueError()
            if (row.get('type')=='control_response' and response.get('request_id')==request['request_id']
                    and response.get('subtype')=='success'):
                commands=response['response']['commands']
                if not isinstance(commands,list):raise ValueError()
                # Never retain account metadata, built-in bodies or auth fields.
                return {c['name'] for c in commands if isinstance(c,dict) and isinstance(c.get('name'),str)}
    except (ValueError,KeyError,TypeError,subprocess.TimeoutExpired) as error:
        raise native_plugins.PluginError(tr('Claude no confirmó el descubrimiento nativo sin inferencia; sigue pendiente.')) from error
    raise native_plugins.PluginError(tr('Claude no respondió a la consulta nativa de skills; sigue pendiente.'))


def loaded(agent,binary,skill,name):
    if agent=='pi':
        import native_pi
        rows=native_pi.metadata(binary,skills=[skill])['get_commands']['commands']
        return any(row.get('name')=='skill:'+name for row in rows)
    if agent=='claude':
        return name in claude_commands(binary)
    result=native_plugins.codex_skills(binary)
    return any(Path(row['path']).resolve()==(Path(skill)/'SKILL.md').resolve() and row.get('enabled')
               for entry in result['data'] for row in entry.get('skills',[]))

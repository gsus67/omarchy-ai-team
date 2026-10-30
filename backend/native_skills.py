"""Read native skill discovery without a model turn or a session transcript."""

from i18n import tr

import json
from pathlib import Path
import subprocess
import tempfile

import native_plugins


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

// Lets one session use the memory of several projects: picks the store for each call and hands it to ECC's own service.
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import { pathToFileURL } from 'node:url';

const SHARED = 'compartido';
const { AI_TEAM_ECC_RUNTIME: runtime, AI_TEAM_MEMORY_STORE: base, AI_TEAM_PROJECTS_ROOT: repos, AI_TEAM_SESSION_STORE: sessionStore,
  AI_TEAM_SESSION_PROJECT: sessionProject } = process.env;
const ecc = await import(pathToFileURL(path.join(runtime, 'scripts/memory-mcp.mjs')).href);
const service = ecc.createMemoryMcpService();

function storeFor(identity) {
  return path.join(base, 'projects', createHash('sha256').update(identity).digest('hex'));
}

function knownProjects() {
  const projects = new Map();
  let folders = [];
  try { folders = fs.readdirSync(repos, { withFileTypes: true }); } catch { /* Without that folder only the shared memory is known. */ }
  for (const folder of folders) {
    if (!folder.isDirectory()) continue;
    const git = path.join(repos, folder.name, '.git');
    try {
      // Same identity as a session opened inside the repo, so both reach the same memory.
      if (fs.lstatSync(git).isDirectory()) projects.set(folder.name, storeFor(fs.realpathSync(git)));
    } catch { /* A folder that is missing .git or cannot be read is just not a project. */ }
  }
  // Last, so no repo can take the shared name. A same-named repo elsewhere keeps its own store through the session default.
  if (sessionProject && !projects.has(sessionProject)) projects.set(sessionProject, sessionStore);
  projects.set(SHARED, storeFor('ai-team-shared-memory'));
  return projects;
}

function label(store, name) {
  try {
    if (!fs.existsSync(store)) return;
    fs.writeFileSync(path.join(store, '.ai-team-project.json'), JSON.stringify({ schemaVersion: 1, name: name.slice(0, 120) }), { flag: 'wx', mode: 0o600 });
  } catch { /* Display metadata only: a label that exists or cannot be written never affects memory. */ }
}

function refuse(id, message) {
  return ecc.jsonRpcResult(id, { isError: true, content: [{ type: 'text', text: message }] });
}

const isObject = value => typeof value === 'object' && value !== null && !Array.isArray(value);
let ready = false;

async function call(message) {
  const { params } = message;
  // ECC answers everything it would reject anyway, in its own way.
  if (!ready || !ecc.isValidRequestId(message.id) || !isObject(params) || !isObject(params.arguments ?? {})) return service.handle(message);
  const { project, ...arguments_ } = params.arguments ?? {};
  const projects = knownProjects();
  const names = [...projects.keys()].sort().join(', ');
  const tool = params.name;
  let name = project ?? sessionProject;
  // Exact names from a closed list: a typo must not create a stray memory.
  if (Object.hasOwn(params.arguments ?? {}, 'project') && !projects.has(project)) {
    return refuse(message.id, `Unknown project. Ask the user which one it is. Known projects: ${names}.`);
  }
  if (!name && tool === 'memory_save') {
    return refuse(message.id, `This session has no project: pass project. Use ${SHARED} only for what applies to every project. Known projects: ${names}.`);
  }
  name ??= SHARED;
  const store = project === undefined && sessionProject ? sessionStore : projects.get(name);
  process.env.ECC_MEMORY_PROJECT_ROOT = store;
  const reply = await service.handle({ ...message, params: { ...params, arguments: arguments_ } });
  const succeeded = reply?.result?.content && !reply.result.isError;
  if (tool === 'memory_save' && succeeded) label(store, name);
  if (tool === 'memory_doctor' && succeeded) {
    const report = JSON.parse(reply.result.content[0].text);
    reply.result.content[0].text = JSON.stringify({ ...report, activeProject: sessionProject ?? null, checkedProject: name, projects: [...projects.keys()].sort() });
  }
  return reply;
}

async function handle(message) {
  if (message?.method === 'tools/call') return call(message);
  const reply = await service.handle(message);
  if (message?.method === 'notifications/initialized') ready = true;
  if (message?.method === 'tools/list' && reply?.result?.tools) {
    for (const tool of reply.result.tools) {
      tool.inputSchema.properties.project = { type: 'string', maxLength: 200, description: 'Project whose memory to use, by its exact name from memory_doctor. Defaults to the project of this session.' };
    }
  }
  return reply;
}

for await (const line of readline.createInterface({ input: process.stdin, crlfDelay: Infinity })) {
  if (!line.trim()) continue;
  let reply, message;
  if (Buffer.byteLength(line) > ecc.MAX_MESSAGE_BYTES) {
    reply = ecc.jsonRpcError(null, -32600, 'Message too large.');
  } else {
    try {
      message = JSON.parse(line);
      reply = await handle(message);
    } catch (error) {
      const invalid = error instanceof SyntaxError;
      reply = ecc.jsonRpcError(invalid ? null : message?.id ?? null, invalid ? -32700 : -32603, invalid ? 'Invalid JSON.' : 'Internal error.');
    }
  }
  if (reply) process.stdout.write(JSON.stringify(reply) + '\n');
}

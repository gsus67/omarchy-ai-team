# Omarchy AI Team

[Español](README.es.md)

Omarchy panel that makes your selected AI agents work as one team. It configures the tools you already use and adds no agent, orchestrator or memory engine of its own.

- **Agents:** Claude Code, Codex, Codex-2 and Pi, through their own CLIs, logins and permissions.
- **Team:** assign a model and effort to Plan, Code, Review and Simple tasks, with allowed alternatives if a model does not respond.
- **Quality:** Superpowers as the working method.
- **Shared memory:** ECC Memory Vault, local and open source, with one memory per project.
- **Tokens and security:** a catalog of local tools that agents use before inference.
- **Maintenance:** updates, memory backups, export and restore from the panel.

## Requirements

Omarchy shell, Python 3 and mise. Node is reused or prepared with mise when memory is enabled. The Nous OAuth login needs `secret-tool` and an unlocked keyring.

## Install

```sh
omarchy plugin add https://github.com/gsus67/omarchy-ai-team.git --enable
```

Update with `omarchy plugin update gsus67.ai-team`. The plugin then restarts the Omarchy shell once so no old code stays loaded; your windows stay open.

Remove it with `omarchy plugin remove gsus67.ai-team`. Disable each objective in the panel first to withdraw what it configured.

## Use

1. Click the bar icon and select your agents. Finish each login in **Settings** if needed.
2. Turn on the objectives you want. In **Settings → Team**, adjust the models.
3. Open your agent from the panel and ask in natural language: "let's work on project X". It loads that project's memory, and several projects in one session stay separate.

Common rules live in a project called `compartido`. Only repositories directly under `~/Work` can be named from another folder.

**Save and stop:** at 90 % of the 5-hour or weekly usage of Claude or Codex, sessions are told to stop and leave a detailed handoff in the project memory. Change the limit or turn it off in **Settings → Team**. Codex asks you to approve this once with `/hooks`.

Pi with Nous only offers free models. Grok through xAI is paid and needs your explicit consent. Only account errors pause Nous for every session. Nous does not publish an OAuth client for third parties, so that login uses the Hermes client and may stop working; API keys entered with `/login nous` are stored by Pi in plain text.

## Per-launch context

Panel launches and Team recipes read the plugin JSON's `context` section (`$XDG_CONFIG_HOME/omarchy-ai-team/settings.json`, default `~/.config/omarchy-ai-team/settings.json`). They affect new processes only, preserving native model, permissions and sandbox. They never write CLI global settings or modify existing sessions.

```json
{
  "context": {
    "enabled": true,
    "apply": "launch",
    "compact_at_tokens": 150000,
    "compact_floor_tokens": 70000,
    "roles": {"jefe": {"compact_at_tokens": 100000}},
    "dump_state_before": true,
    "chief_brief": "memory_index",
    "worker_report_max_tokens": 800,
    "worker_report_on": ["milestone", "blocked"]
  }
}
```

All numbers are adjustable recommendations. Each role (`jefe`, `plan`, `code`, `review`, `simple`) can override threshold, floor and report budget. Pass `--context-override '{"compact_at_tokens":180000,"reason":"finish a debugging boundary"}'` for an explicit, reasoned invocation exception: invocation > role > settings > defaults, without saving it. `apply=launch` and global configuration isolation are fixed. Claude's native threshold range is 100000–1000000. `enabled=false` preserves native arguments, environment and output, including the absence of report filtering; it also supports reasoned invocation exceptions. Authentication is excluded.

Each invocation gets a UUID and frozen cwd, using ECC's existing project/worktree identity. Agents submit explicit JSON on stdin with lists `plan`, `decisions`, `files`, `errors`, `pending`, and optional booleans `safe_boundary`, `debug`, to the checkpoint command shown in their instructions (`python3 backend/context_state.py checkpoint`, or `python3 backend/harness.py context-checkpoint`). ECC saves and reads back the handoff; no conversation or tool-log extraction occurs. PreCompact saves a pointer to that invocation's checkpoint. Missing, invalid, previously consumed state and ECC errors are explicitly incomplete/failed; there is no fabricated summary or guaranteed compaction veto. `dump_state_before=false` omits this hook and its retention assurance. The chief gets only bounded ECC titles and IDs for the current project and `compartido`, with untrusted metadata and pending/error status, without bodies or excerpts. No project means no silent lookup of another project's memory.

Claude receives per-process hooks and appended instructions. Active inherited `DISABLE_AUTO_COMPACT`/`DISABLE_COMPACT` switches stop launch with an error; resolve them or explicitly disable the policy. Codex receives per-process overrides and SessionStart/PreCompact hooks while preserving developer instructions and native hook trust review: instructions/index remain pending until approved. A new UUID can require fresh approval. Native global settings and controls may still prevent compaction: parser acceptance does not prove effective session values.

Recipes such as `python3 backend/context_run.py --agent codex-2 --role review --cwd '<dir>' -- codex exec -s read-only -m '<model>' '<task>'` use the same resolver and accept `--settings` and `--context-override`. They do not choose tasks or models. With policy enabled, they consume native structured output and discard tool events and stderr, returning only JSON fields `status`, `references`, `tests`, `error`, `pending`. The transport budget approximates four UTF-8 bytes per token, with a minimum 256-byte envelope; this is not a tokenizer. Oversized reports are saved/read back in ECC and returned by reference; rejection never forwards their body. Structured native fatal errors are bounded or referenced; stderr-only failures report the exit code and discarded stderr. No transcript copy is retained locally.

Floor, safe phases, debugging, irreversible operations and chief-only delegation are cooperative wherever native guards are unavailable. Workers report at milestones or blocks without raw logs. Justified threshold exceptions are allowed; no hard cap or deterministic phase/token detection is promised. Pi uses a per-process extension for threshold compaction and ECC precompact checks; no Pi settings are changed. Synthetic tests and parser probes do not establish hook trust, real compaction or effective session windows.

### Savings settings

Goal: fewer accumulated input tokens per accepted task, cache reads included. A stable prefix can improve cache use; by itself it does not reduce what is re-read. All keys live in `context`, accept the same role overrides and reasoned invocation exceptions, and take effect in new sessions.

| Key | Default / range | Kind |
|---|---|---|
| `task_plan_with_pointers` | `true` | Cooperative: briefs carry goal, `file:line` references, acceptance and a do-not-reread list; reread when files changed, a failure appears or evidence is insufficient |
| `fresh_worker_per_subtask` | `true` | Native recipe (`codex exec --ephemeral`, `claude -p --no-session-persistence`; `resume`/`fork`, `--resume`/`--continue`/`-c` are rejected for Claude, while Codex `-c key=value` stays valid); opening one worker per subtask is cooperative |
| `bounded_reads` / `read_window_lines` | `true` / `120` (1–2000) | Cooperative: read windows, not whole files |
| `stable_prompt_prefix` | `true` | Plugin text puts the instance UUID and checkpoint command last; Claude also gets `--system-prompt-snapshot on`; Codex has no equivalent control |
| `measure_task_tokens` | `true` | Native counters of the events the wrapper already reads; private numeric snapshots in `$XDG_STATE_HOME/omarchy-ai-team/context-usage/<uuid>/` |
| `clear_between_tasks_below_floor` | `true` | Cooperative: suggests `/clear` after saving the handoff; never sent automatically |
| `group_related_subtasks` | `true` | Cooperative: Chief/Plan ask to group related subtasks (same files) in ONE worker and open another only on a phase change; a new worker per step raised new tokens ~40% in the pilot A/B |
| `explore_with_simple_role` | `true` | Cooperative: exploration, reading and searches go to the Simple tasks role, which returns only `file:line` pointers, no content |
| `handoff_on_pause` | `true` | Cooperative: at the end of a block or when waiting for you, the session saves a structured handoff with the existing ECC checkpoint (saved and re-read) |
| `suggest_resume_after_idle` / `idle_cache_ttl_minutes` | `true` / `55` (1–1440) | Native `UserPromptSubmit` hook per instance (same trust as the other hooks); stores only the last-turn time in `$XDG_STATE_HOME/omarchy-ai-team/context-usage/<uuid>/` (0600). After the idle time it adds ONE line of context about the cold cache; it recommends `/clear` + brief only if a saved, re-read and identical ECC handoff exists, otherwise it says there is none and not to clear. It never blocks or clears; the native hook gives no token count, so it reports them as unknown |

Edit them in **Settings → Context** or with `python3 backend/harness.py context-set <key> <json> [--role <role>]` and `context-reset [--role <role>]` (64-bit integers travel as decimal text). Only the plugin's `settings.json` changes; no profile is touched. Per-task counters: `python3 tools/session_usage.py --task-tokens --before before.json --after after.json` (Codex `input_tokens` already include cached ones; Claude's new, cache-read and cache-creation inputs add up). Cost appears only if a CLI reports it. The synthetic benchmark `python3 tools/context_benchmark.py` is arithmetic on invented fixtures and does **not** prove real savings; the real A/B protocol is in `docs/RENDIMIENTO.md` (local).

The CLI that the main **Open default** button launches is chosen in the **Chief** row of **Settings → Team**; the saved preference is unchanged.

## Development

```sh
make check
```

Runs the plugin validator, tests, qmllint and the Wayland smoke test with temporary profiles and no inference. Rules for working on this repository are in [AGENTS.md](AGENTS.md).

## License

[MIT](LICENSE)

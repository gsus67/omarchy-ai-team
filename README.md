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

Pi with Nous only offers free models. Grok through xAI is paid and needs your explicit consent. Only account errors pause Nous for every session. Nous does not publish an OAuth client for third parties, so that login uses the Hermes client and may stop working; API keys entered with `/login nous` are stored by Pi in plain text.

## Development

```sh
make check
```

Runs the plugin validator, tests, qmllint and the Wayland smoke test with temporary profiles and no inference. Rules for working on this repository are in [AGENTS.md](AGENTS.md).

## License

[MIT](LICENSE)

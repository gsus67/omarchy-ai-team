# Security

Report vulnerabilities privately through GitHub Security Advisories on this repository.

AI Team runs with your user permissions and configures the agent CLIs you select.
It stores settings and memory owner-only and keeps no credentials.
Agent logins stay in each CLI's own store. The Pi extension for Nous sends OAuth
tokens only to the fixed Nous Portal and keeps them in the encrypted keyring through
`secret-tool`, with no plain-text fallback; Pi's `auth.json` holds only placeholders.
Other Pi logins, such as an API key entered in `/login`, are written in plain text
by Pi itself.

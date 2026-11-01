# Security and privacy

Postmark is a work-in-progress project. Please do not use this public source repository as proof that the local application is production hardened.

## Never publish
- Gmail OAuth refresh/access tokens, client secrets, and `credentials.json` / `token.json`
- API keys for any model provider
- .env files, credential caches, SQLite databases, backups and migration dumps containing user data
- Email message bodies, addresses, personal names or invoices
- Local runtime logs, traces, prompts or payloads with private mail
- Screenshots containing actual inbox content, sender identities, account addresses or authorization secrets

## Before any source-code release
1. Review the entire Git history for secrets, not only the working tree.
2. Remove any embedded private data from example fixtures and screenshots.
3. Ensure sample configuration uses placeholders.
4. Audit destructive operations, access consent and account isolation.
5. Verify dependency licensing and reproduce setup from a clean environment.
6. If credentials have ever been exposed, revoke and rotate them.

The repository contains reviewed source and synthetic fixtures. It does not contain deployed account permissions or private runtime state. The API is loopback-only, not an authenticated multi-user Internet service. Keep OAuth scopes read-only unless you separately authorize a specific mail action; never enable permanent deletion or paid inference by default. Report a vulnerability privately through GitHub private vulnerability reporting if enabled; do not attach real mail, credentials or production databases to public issues.

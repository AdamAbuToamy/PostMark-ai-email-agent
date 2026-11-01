# Security and privacy

Postmark is a work-in-progress project. Please do not use this public documentation repository as proof that the local application is production hardened.

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

This repository currently contains documentation only; not the production system source.

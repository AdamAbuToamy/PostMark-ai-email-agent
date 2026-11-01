# Reviewed source publication

Published from the latest working tree, not just the older local Git HEAD. The release retains the existing public repository's README, SECURITY and ROADMAP history and adds the application as a new ordinary commit. The original local repository/history/remotes were not reset, rewritten or pushed. No force push or branch/tag mirroring was used.

## Included

- Complete current Python backend, including migrations/repositories, atomic taxonomy rollback/recovery, autonomous structural-fit policy, typed local tools and safe bulk mail operations.
- React/TypeScript frontend, responsive sidebar, model activity, usage accounting and resource diagnostics.
- Current provider-aware routing and local/cloud safety gates. Active routes remain Gemini, Groq, Mistral, Z.AI and Ollama; historical inactive adapters are not new enabled services.
- Synthetic tests/resources, browser helper source, Windows tray source, dependency lockfiles/manifests, generic systemd templates and setup/architecture documentation.

The latest completed structural taxonomy repair, token accounting, retained-allocation fixes and UI work are included. Broader semantic accuracy, memory high-water stability and unattended production hardening remain work in progress, not features invented by this publication.

## Security review

Official Gitleaks8.30.1 was downloaded with its release checksum verified. The entire local Git history was scanned redacted. An additional offline scan compared candidates/history with known local credential values, message IDs and correspondent addresses; reports reveal locations/counts only, never values.

Gitleaks found no confirmed secret in local history, but local history contained private operator paths and correspondence-related diagnostic content, so that history was deliberately excluded. The public source is based only on the existing public documentation history plus reviewed allowlisted files.

The initial tree scan flagged a read-only verification script's natural-language console message as a generic-key candidate; manual inspection confirmed it was not a credential, and the console message was clarified. Final source-export and publication-history scans have no remaining findings. No confirmed exposed credential was found by these checks. Scanning is not proof that every possible secret format can be recognized.

Candidate tests contained public service senders also occurring in real mail. Their local parts were replaced with consistently fictional fixture identities while retaining public domains needed by domain/transport tests. Tests/resources are synthetic; no live body export or saved provider response is shipped. Operator-local path defaults in smoke scripts and service templates were generalized. No project license was invented.

Excluded: all credential/token files, environment secrets, databases/WAL/SHM/journals, backups, logs, exports, attachments, personal/university mail, private handoffs/agent transcripts/reference documents, source checkpoints, caches, installed dependencies/build outputs and unreviewed screenshots. A manually inspected screenshot of an actual empty verification instance is included. During publication, a concurrent remote commit added six already-redacted real screenshots. Each was visually reviewed for addresses, sender identities, subjects and keys before preserving that remote commit and its gallery.

## Verification

A genuinely fresh isolated Python environment installed the supplied runtime and optional dependency manifests. It had no provider keys, OAuth tokens or real account state.193 focused backend tests passed with external network connections explicitly disabled, covering API boundaries, bulk mail actions, MIME/body handling, taxonomy singleton-fit and conflict rules, manual overrides, merge undo, atomic failure/restart recovery, privacy/resource accounting and synthetic regressions. This is not a claim that every historical test suite was run.

Frontend dependencies were installed with `npm ci`; TypeScript/Vite build and ESLint passed. The pre-existing >500kB main-chunk warning remains. The import-boundary check found0 selected violations/parse errors across143 Python modules. The fresh ASGI instance started on a separate loopback port without Gmail/model credentials. GET-only Chromium rendered the actual empty interface without JavaScript errors; no inference request or destructive mailbox action was used for verification.

No running production worker was restarted or reconfigured for this publication. The temporary screenshot server is stopped after verification. No model test, paid inference, provider permission expansion or Gmail mutation was initiated. Configuration/setup details and remaining deployment limits are in SETUP.md and SECURITY.md.

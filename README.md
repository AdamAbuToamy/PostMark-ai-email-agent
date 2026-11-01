# Postmark

**Autonomous Multilingual AI Email Organizer · Work in Progress**

Postmark is a self-hosted email organization project combining local-first data processing, a multi-provider AI engine, and recoverable automation. It is being developed to understand and organize messages in **Arabic, Hebrew and English** without turning every email subtype into a separate folder.

> **Status — Work in Progress (November 2026).** Postmark is under active development. It is not presented as production-hardened, externally audited, or ready for third-party deployment.

## Highlights

- **Autonomous organization:** identifies meaningful local categories, reuses existing categories when appropriate, selects relevant icons, and publishes with an audit/rollback trail.
- **Multilingual understanding:** category, document subtype and workflow status are represented separately.
- **Multi-model routing:** Gemini, Groq, Mistral, Z.AI, and local Ollama, respecting actual per-model capabilities, account permissions and rate limits.
- **Privacy-conscious accounts:** separate personal and university account scopes; cloud AI access is provider-specific.
- **Durable background work:** Gmail sync, queued classification, retry/cooldown, crash recovery, idempotent operations, and SQLite WAL.
- **Cleanup and safety:** review, grouping, batch trash/restore, and additional authorization before irreversible deletion.
- **Observability:** real inference activity, queues, token usage, and resource diagnostics.

## Technology

| Layer | Technology |
|---|---|
| Backend | Python, FastAPI |
| Persistence | SQLite with WAL, durable job records |
| Frontend | React, TypeScript, Vite |
| AI | Provider-aware cloud inference + local Ollama |
| Email integration | Gmail API |
| Development | Ubuntu / WSL2, Git |

## Architecture

```text
Gmail accounts
     |
     v
Discovery / sync / body backfill
     |
     v
Durable task queue & scheduler
     |
     v
Account-aware, free-only AI router
     |---- qualified cloud models
     \---- Ollama local inference
     |
     v
Semantic results (category / subtype / status)
     |
     v
Atomic taxonomy publication + reversible audit
     |
     v
FastAPI -> React dashboard
```

## Real application screenshots

Screenshots of the running application were captured for the project showcase. The public screenshots are being reviewed for personal email and sender information before inclusion here. **No synthetic product screenshots or mockups are presented as real output.**

## Engineering work

Postmark has involved solving real issues such as SQLite locking, long-running task recovery, model-provider cooldowns, prompt duplication, RAM pressure from local inference, and autonomous taxonomy publication without category proliferation.

## Current limitations

- Multilingual classification and some document subtypes still need more human-verified accuracy evaluation.
- Free inference quotas limit throughput; provider availability is not guaranteed.
- Token use, local inference memory and end-to-end unattended reliability continue to be optimized.
- Packaging, security review and external installation instructions are not complete.

## Security & privacy

This public repository currently contains **project documentation only**. It is **not** a release of the local application source code.

Do not commit API keys, OAuth tokens, email bodies, user databases, logs, exported credentials, or screenshots containing private messages. Future source publication requires a code-and-history secret review and a separate verification of safe sample configuration.

## Development status

🚧 **Active development / Work in Progress.** The goal is a reliable autonomous email agent that organizes messages without routine user decisions while keeping irreversible mail actions behind explicit permissions.

---
Built as a hands-on software engineering project.
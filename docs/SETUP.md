# Local setup

This is reviewed work-in-progress source, not a hosted service or a transfer of the developer's deployed accounts. Supported deployment target: Linux/Ubuntu under WSL2. Python3.11+, Node22+ and SQLite with JSON/window support are required. The publication check records the exact tested versions separately. No project license has been chosen; this release does not invent licensing permission.

## Install and start an empty instance

From the checkout root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
# Optional attachment text extraction and independently approved unsubscribe verification:
.venv/bin/python -m pip install -r requirements-attachments.txt -r requirements-unsubscribe.txt
npm ci --prefix frontend
npm run build --prefix frontend
cp .env.example .env
PYTHONPATH=src .venv/bin/uvicorn email_agent.api.app:app --host 127.0.0.1 --port 8000 --env-file .env
```

Open http://127.0.0.1:8000. An empty instance starts without Gmail or model credentials. Importing the ASGI app initializes local SQLite files; isolate `EMAIL_AGENT_PROJECT_ROOT` when testing. The frontend can also run with `npm run dev --prefix frontend` on port5174, proxying the API on8000. Avoid development servers in a normal production installation.

Keep storage on persistent disk. Never copy only the main SQLite `.db` during live WAL operation: use SQLite's backup API. Keep `data/`, OAuth files, `.env` and all backups private. Journal directories must be writable for atomic category publication across mail and category databases. Main mail storage uses WAL normally; short taxonomy transactions use durable rollback journals for crash recovery.

## Gmail authorization

Register your own Google OAuth Desktop client, enable Gmail API, and save its downloaded client configuration as local `credentials.json`. No OAuth app credentials are shipped. The CLI `PYTHONPATH=src .venv/bin/python -m email_agent.main --account personal --limit 10` performs the initial explicitly requested read/login and creates `tokens/personal.json`; use `--account university` separately. It prints message metadata to your own terminal; do not publish that output. Default authorization is Gmail read-only. Tokens are separate per account.

Never run scope-upgrade utilities merely to start the app. Trash, restore, unsubscribe and permanent deletion require independently authorized scopes/actions. No source publication authorizes operating on an existing mailbox. Permanent deletion must never be autonomous.

## AI and autonomy

Retained active providers are Gemini, Groq, Mistral, Z.AI and Ollama. Retired adapters/tests remain only for history and regression coverage; they are not enabled options. Configure provider keys server-side in AI Engine. An API key does not grant mailbox-processing consent: permissions and verified-free limits are account/provider/model-specific. A fresh checkout includes no private cached model qualification or provider access grants. Never import a different user's qualification/budget records to bypass consent. New models remain inactive until evidence and authorization exist. No paid fallback or automatic qualification test is authorized by installation.

Ollama is optional and must be installed separately. No model weights are bundled. Use a private loopback endpoint by default; a trusted remote endpoint needs explicit configuration/authentication. Measure RAM/VRAM before loading a model. Existing qwen3.5:4b defaults are single local inference, bounded context and idle unload; they do not guarantee suitability for an8GB host. If neither a qualified local nor authorized cloud route exists, tasks remain queued honestly.

Autonomous **local** organization is account-specific and reversible. A new navigation category needs a reusable purpose separate from document subtype, sender or workflow status. Manual choices stay authoritative. This permission never authorizes Gmail mutations or external-provider data sharing.

## Workers and operating-system templates

The existing `email_agent.workers.continuous_mail` coordinates bounded restart-safe work; do not run duplicate installations against the same mailbox/database. Optional systemd templates under `scripts/systemd/` assume `/opt/postmark` and an operator-created `postmark` user; adapt paths deliberately. They are **not automatically installed**. Never enable them against another running installation or copy production secrets into Git. Expired OAuth and real provider quotas remain external blockers.

The optional Windows tray is `desktop/windows/EmailAgentTray.ps1`; it reads the loopback API and uses a single-instance mutex. Run with Windows PowerShell STA. It does not host another AI worker.

The optional isolated browser helper is shipped in `browser/`. Install with `npm ci --prefix browser`, then `cd browser && npx playwright install chromium`. It is used only for explicitly authorized browser flows; no browser session or cookies are bundled.

## Verification

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -q
.venv/bin/python scripts/architecture_check.py
npm run build --prefix frontend
npm run lint --prefix frontend
```

Tests use synthetic fixtures, temporary SQLite and mocked provider/Gmail adapters. Public brand domains test routing semantics; sender identities are fictional. Do not run `*_live*`, `*_readonly*` or operator audit scripts against personal data unless you intend their read scope; read-only output may still be private. Browser smoke scripts use isolated fixtures where documented. Do not turn test success into proof of live model qualification or unattended accuracy.

## Security boundaries and limitations

The API is restricted to loopback and is not a complete authenticated multi-user service. Do not expose it directly to a LAN/Internet or blindly add allowed hosts. Public source does not include a hardened reverse proxy/authentication deployment. Model availability/free quotas vary; the router cannot invent free capacity. Semantic accuracy and backend high-water RAM still need longer independent evaluation. Use the project's preserved SECURITY.md before filing issues; never attach real mail, provider payloads or credentials.

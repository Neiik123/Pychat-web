# PyChat Web

PyChat Web is the hosted edition of the supplied PyChat application. Its existing chat interface and feature code are retained; the web edition adds an account gate and routes persistence through an authenticated cloud API. The separate local edition remains under `local/`.

## Repository layout

- `web/index.html` — original PyChat interface with only the auth bootstrap included in its document head and the auth-ready wait added to startup. The interface itself is not redesigned.
- `web/auth.js` — Supabase email/password sign-up, sign-in, sign-out, session restoration, and authenticated API requests.
- `backend/main.py` — FastAPI service, session validation, per-user persistence, API-key encryption, API proxy, model discovery, search, and static frontend hosting.
- `supabase/migrations/001_user_data.sql` — account data table, row-level security, and update timestamp trigger.
- `local/` — preserved local application. Its UI is the same as the uploaded source; `server.py` includes the previously requested `run.bat` behavior.
- `local_connector.py` — optional loopback bridge so a hosted website can use Ollama running on the user's own computer.
- `tests/` — backend safety and account-isolation tests.

## 1. Create the Supabase project

1. Create a project at [Supabase](https://supabase.com/).
2. In **SQL Editor**, run `supabase/migrations/001_user_data.sql`.
3. In **Authentication → Providers**, enable Email/password.
4. Configure the site's production URL and allowed redirect URLs in **Authentication → URL Configuration**.
5. Decide whether email confirmation is required. If enabled, users must confirm their email before signing in.
6. Copy the Project URL, publishable/anon key, and service-role secret. The service-role secret must never be put in browser code or committed to Git.

## 2. Configure backend secrets

Set these environment variables in the hosting provider:

- `SUPABASE_URL` — Supabase project URL.
- `SUPABASE_ANON_KEY` — Supabase publishable/anon key.
- `SUPABASE_SERVICE_ROLE_KEY` — Supabase service-role secret; backend only.
- `APP_ENCRYPTION_KEY` — Fernet key used to encrypt API keys stored in account JSON.
- `ALLOWED_ORIGINS` — optional comma-separated browser origins if the frontend is hosted separately. The recommended deployment serves frontend and API from the same origin, so leave empty.

Generate an encryption key locally:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Keep the encryption key backed up securely. If it is lost, encrypted API keys already stored in the database cannot be recovered. Do not rotate it without first migrating existing encrypted values.

## 3. Deploy

A `Dockerfile` and `render.yaml` are included for a single-origin Render deployment.

1. Push this repository to GitHub.
2. In Render, create a Blueprint deployment from the repository (or create a Docker web service using the included Dockerfile).
3. Set all five environment variables above in the service's environment settings. The four Supabase/encryption values are required; `ALLOWED_ORIGINS` can remain empty for same-origin hosting.
4. Deploy and verify `https://YOUR-SERVICE/api/health` returns `{"ok":true,...}`.
5. Open the site, create an account, sign in, create a character/conversation, reload, and verify that it persists.

`render.yaml` intentionally does not contain secrets. Supply them through the hosting dashboard.

## 4. Local development

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
# Set the environment variables listed above.
uvicorn backend.main:app --reload --port 8000
```

The local API serves the web frontend at `http://localhost:8000`.

## 5. Ollama on a user's computer

A cloud server cannot reach `localhost:11434` on a visitor's computer. The optional connector solves this without changing PyChat's chat interface:

1. Download/run `local_connector.py` on the computer that runs Ollama.
2. Set `PYCHAT_WEB_ORIGINS` to the exact website origin, for example `https://pychat-web.onrender.com` (no trailing slash). Multiple origins can be comma-separated.
3. On Windows, run `run-local-connector.bat`; otherwise run `python local_connector.py`.
4. Keep the connector window open while using local Ollama from the website.
5. Configure PyChat's Ollama URL as `http://localhost:11434` as usual.

The connector binds only to `127.0.0.1`, requires an exact allowed website Origin, and only forwards to loopback/private/LAN addresses. Do not expose its port publicly. The normal cloud proxy rejects local/private targets.

## 6. Tests and checks

```bash
pip install -r requirements.txt pytest
python -m py_compile backend/main.py local/server.py local_connector.py
python -m pytest -q
```

GitHub Actions runs these checks on pushes and pull requests.

## Security and launch checklist

- Never commit `.env`, service-role keys, encryption keys, or user data.
- Use HTTPS for the hosted website.
- Keep Supabase Row Level Security enabled.
- The backend validates the Supabase access token and scopes every database operation to its immutable user ID.
- API-key values in fields named `key` are encrypted before database storage and decrypted only when loaded for that same account.
- The public proxy accepts only HTTPS endpoints with globally routable DNS results, blocks redirects, and applies basic per-user request limits. Provider-level rate limits and abuse monitoring should also be enabled before opening public registration widely.
- Configure Supabase Auth email confirmation, CAPTCHA/rate limits as appropriate, backups, and account deletion policy before public launch.
- The in-memory request limiter is per application process; use hosting/provider-level limits for multi-instance production deployments.

## Versioning

Keep the local edition intact. Publish web releases using immutable tags such as `v_0.1.0`, `v_0.2.0`, etc. Do not overwrite a published release; use a new tag and GitHub Release for each version.

## Current scope

This project includes the application code and deployment configuration. A live public website still requires the owner to configure Supabase and a hosting account and provide those secrets through the host's private environment settings. No credentials are embedded in this repository.

# Changelog

## v_0.1.0 — Initial web conversion

- Preserved the original PyChat interface and local edition.
- Added Supabase email/password registration, login, logout, and password recovery.
- Added authenticated, per-user cloud persistence through FastAPI and Supabase Postgres.
- Added server-side encryption for configured API-key values before database storage.
- Added authenticated API proxying, remote model discovery, web search, and basic per-user request limits.
- Added an optional loopback bridge for using a user's local Ollama installation from the hosted site.
- Added Docker/Render deployment configuration and automated backend checks.

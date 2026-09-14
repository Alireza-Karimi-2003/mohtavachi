# Production Audit — v19

## Blocking issues found in v18 and fixed

1. `docker-compose.production.yml` did not pass `DOMAIN` into the Caddy container, while the Caddyfile uses `{$DOMAIN}`. Production HTTPS routing could therefore fail. Fixed by passing `DOMAIN` to Caddy.
2. `DailySuggestion` and `Referral` ORM models contained admin-control fields that were not present in Alembic migrations. This could cause PostgreSQL queries against non-existent columns. Removed the accidental fields from those two models; admin-control fields remain on `User`.
3. Docker build context had no `.dockerignore`. Added one to prevent secrets, VCS metadata, local virtualenv files, tests, and local-only files from being sent as build context.
4. README webhook-header documentation was stale. Updated it to the Telegram header used by the application.
5. README production database note was stale. Updated it to reflect PostgreSQL/Docker Compose.

## Verified

- Python compileall: PASS
- Focused production/payment/admin static tests: PASS
- Full pytest is not currently runnable in this execution environment because `asyncpg` and `aiogram` are not installed there; this is an environment limitation, not a project test result.
- Docker Compose syntax was parsed successfully with a YAML parser, but actual `docker compose config`/image build requires Docker on the target machine.

## Still intentionally deferred

- Real ZarinPal credentials and live payment activation
- Real HTTPS certificate issuance on the VPS
- Telegram webhook registration against the production domain
- PostgreSQL backup automation
- SSH hardening on the VPS (key-only login after access is confirmed)

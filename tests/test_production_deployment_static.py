from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_production_files_exist_and_are_safe_defaults():
    compose = (ROOT / "docker-compose.production.yml").read_text(encoding="utf-8")
    env = (ROOT / ".env.production.example").read_text(encoding="utf-8")
    caddy = (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")
    startup = (ROOT / "start_production.sh").read_text(encoding="utf-8")

    assert "postgres:16-alpine" in compose
    assert '"80:80"' in compose and '"443:443"' in compose
    assert '"5432:5432"' not in compose
    assert '"8000:8000"' not in compose
    assert "postgres_data" in compose
    assert "PAYMENT_ENABLED=false" in env
    assert "PUBLIC_BASE_URL=https://" in env
    assert "reverse_proxy bot:8000" in caddy
    assert "alembic upgrade head" in startup
    assert "uvicorn app.main:app" in startup


def test_production_has_readiness_probe_and_healthy_dependency():
    compose = (ROOT / "docker-compose.production.yml").read_text(encoding="utf-8")
    main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert '@app.get("/ready")' in main
    assert 'urlopen(\'http://127.0.0.1:8000/ready\'' in compose
    assert 'condition: service_healthy' in compose


def test_postgres_backup_script_is_present_and_does_not_expose_password():
    backup = (ROOT / "deploy" / "backup_postgres.sh").read_text(encoding="utf-8")
    assert "pg_dump" in backup
    assert "--format=custom" in backup
    assert "POSTGRES_PASSWORD" not in backup
    assert "RETENTION_DAYS" in backup

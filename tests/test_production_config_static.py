from pathlib import Path

import pytest

from app.config import settings, validate_production_configuration


def _snapshot():
    return {
        "app_env": settings.app_env,
        "bot_token": settings.bot_token,
        "admin_telegram_id": settings.admin_telegram_id,
        "webhook_secret": settings.webhook_secret,
        "public_base_url": settings.public_base_url,
        "database_url": settings.database_url,
        "payment_enabled": settings.payment_enabled,
        "zarinpal_merchant_id": settings.zarinpal_merchant_id,
        "zarinpal_sandbox": settings.zarinpal_sandbox,
        "ai_provider": settings.ai_provider,
        "ollama_base_url": settings.ollama_base_url,
        "ollama_model": settings.ollama_model,
        "ai_api_base_url": settings.ai_api_base_url,
        "ai_api_key": settings.ai_api_key,
        "ai_model": settings.ai_model,
    }


def _restore(old):
    for key, value in old.items():
        setattr(settings, key, value)


def test_local_configuration_is_not_blocked():
    old = _snapshot()
    try:
        settings.app_env = "local"
        validate_production_configuration()
    finally:
        _restore(old)


def test_production_rejects_default_webhook_secret():
    old = _snapshot()
    try:
        settings.app_env = "production"
        settings.bot_token = "token"
        settings.admin_telegram_id = 1
        settings.webhook_secret = "change-me"
        settings.public_base_url = "https://example.com"
        settings.database_url = "postgresql+asyncpg://u:p@postgres/db"
        with pytest.raises(RuntimeError):
            validate_production_configuration()
    finally:
        _restore(old)


def test_production_rejects_sandbox_when_payments_enabled():
    old = _snapshot()
    try:
        settings.app_env = "production"
        settings.bot_token = "token"
        settings.admin_telegram_id = 1
        settings.webhook_secret = "strong-secret"
        settings.public_base_url = "https://example.com"
        settings.database_url = "postgresql+asyncpg://u:p@postgres/db"
        settings.payment_enabled = True
        settings.zarinpal_merchant_id = "merchant"
        settings.zarinpal_sandbox = True
        with pytest.raises(RuntimeError):
            validate_production_configuration()
    finally:
        _restore(old)


def test_production_start_validates_before_migration():
    script = Path("start_production.sh").read_text()
    assert script.index("validate_production_configuration") < script.index("alembic upgrade head")


def _set_valid_production():
    settings.app_env = "production"
    settings.bot_token = "123:real-like-token"
    settings.admin_telegram_id = 1
    settings.webhook_secret = "x" * 32
    settings.public_base_url = "https://bot.mohtavachi.test"
    settings.database_url = "postgresql+asyncpg://u:p@postgres/db"
    settings.payment_enabled = False
    settings.ai_provider = "openai_compatible"
    settings.ai_api_base_url = "https://api.provider.test/v1"
    settings.ai_api_key = "secret-key"
    settings.ai_model = "gpt-5.6-luna"


def test_valid_production_configuration_passes():
    old = _snapshot()
    try:
        _set_valid_production()
        validate_production_configuration()
    finally:
        _restore(old)


def test_production_rejects_localhost_ollama():
    old = _snapshot()
    try:
        _set_valid_production()
        settings.ai_provider = "ollama"
        settings.ollama_base_url = "http://127.0.0.1:11434"
        settings.ollama_model = "gemma3:12b"
        with __import__("pytest").raises(RuntimeError):
            validate_production_configuration()
    finally:
        _restore(old)


def test_production_rejects_incomplete_external_ai():
    old = _snapshot()
    try:
        _set_valid_production()
        settings.ai_api_key = ""
        with __import__("pytest").raises(RuntimeError):
            validate_production_configuration()
    finally:
        _restore(old)

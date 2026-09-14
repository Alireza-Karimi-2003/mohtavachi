from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "local"
    bot_token: str = ""
    bot_username: str = "mohtavachi_ai_bot"
    admin_telegram_id: int = 0
    webhook_secret: str = "change-me"
    public_base_url: str = ""
    database_url: str = "postgresql+asyncpg://mohtavachi@localhost:5432/mohtavachi"

    ai_provider: str = "ollama"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "gemma3:12b"
    ollama_timeout: int = 300
    ai_api_base_url: str = ""
    ai_api_key: str = ""
    ai_model: str = ""
    ai_fallback_model: str = "gpt-5-mini"
    ai_fallback_reasoning_effort: str = "minimal"
    studio_plus_ai_model: str = "gpt-5.6-terra"
    studio_plus_image_model: str = "gpt-image-2"
    ai_image_model: str = "gpt-image-1-mini"

    starter_price: int = 299000
    pro_price: int = 599000
    studio_price: int = 1190000
    studio_plus_price: int = 2490000
    studio_plus_credits: int = 1600

    payment_enabled: bool = False
    zarinpal_merchant_id: str = ""
    zarinpal_sandbox: bool = True
    payment_timeout: int = 20
    payment_order_reuse_minutes: int = 60

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()


def validate_production_configuration() -> None:
    """Fail fast on unsafe/incomplete Production configuration."""
    if str(settings.app_env).strip().lower() != "production":
        return

    required = {
        "BOT_TOKEN": settings.bot_token,
        "ADMIN_TELEGRAM_ID": settings.admin_telegram_id,
        "WEBHOOK_SECRET": settings.webhook_secret,
        "PUBLIC_BASE_URL": settings.public_base_url,
        "DATABASE_URL": settings.database_url,
    }
    missing = [
        name for name, value in required.items()
        if not value or (name == "ADMIN_TELEGRAM_ID" and int(value) <= 0)
    ]
    if missing:
        raise RuntimeError(f"Production configuration missing: {', '.join(missing)}")

    placeholder_values = ("replace-with-", "example.com")
    for name, value in {
        "BOT_TOKEN": settings.bot_token,
        "WEBHOOK_SECRET": settings.webhook_secret,
        "PUBLIC_BASE_URL": settings.public_base_url,
        "DATABASE_URL": settings.database_url,
    }.items():
        text = str(value).strip().lower()
        if any(marker in text for marker in placeholder_values):
            raise RuntimeError(f"Production {name} still contains a placeholder value")

    if len(settings.webhook_secret.strip()) < 32:
        raise RuntimeError("Production WEBHOOK_SECRET must be at least 32 characters")

    public = urlparse(settings.public_base_url.strip())
    if public.scheme != "https" or not public.hostname:
        raise RuntimeError("Production PUBLIC_BASE_URL must be a valid HTTPS URL")

    if not str(settings.database_url).startswith("postgresql+asyncpg://"):
        raise RuntimeError("Production DATABASE_URL must use PostgreSQL/asyncpg")

    provider = str(settings.ai_provider).strip().lower()
    if provider not in {"ollama", "openai_compatible"}:
        raise RuntimeError(f"Unsupported Production AI_PROVIDER: {settings.ai_provider}")

    if provider == "openai_compatible":
        ai_url = urlparse(settings.ai_api_base_url.strip())
        if not settings.ai_api_base_url or ai_url.scheme != "https" or not ai_url.hostname:
            raise RuntimeError("Production OpenAI-compatible AI_API_BASE_URL must be a valid HTTPS URL")
        if any(marker in settings.ai_api_base_url.lower() for marker in placeholder_values):
            raise RuntimeError("Production AI_API_BASE_URL still contains a placeholder value")
        if not settings.ai_api_key or settings.ai_api_key.lower().startswith("replace-with-"):
            raise RuntimeError("Production AI_API_KEY must be configured")
        if not settings.ai_model or settings.ai_model.lower().startswith("replace-with-"):
            raise RuntimeError("Production AI_MODEL must be configured")
    else:
        ollama_url = urlparse(settings.ollama_base_url.strip())
        if not settings.ollama_base_url or not ollama_url.hostname:
            raise RuntimeError("Production OLLAMA_BASE_URL must be configured")
        blocked_hosts = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
        if (ollama_url.hostname or "").lower() in blocked_hosts:
            raise RuntimeError("Production OLLAMA_BASE_URL cannot point to the bot container itself")
        if not settings.ollama_model:
            raise RuntimeError("Production OLLAMA_MODEL must be configured")

    image_url = urlparse(settings.ai_api_base_url.strip())
    if not settings.ai_api_base_url or image_url.scheme != "https" or not image_url.hostname:
        raise RuntimeError("Production image AI_API_BASE_URL must be a valid HTTPS URL")
    if not settings.ai_api_key:
        raise RuntimeError("Production image generation requires AI_API_KEY")

    if settings.payment_enabled:
        if not settings.zarinpal_merchant_id or settings.zarinpal_merchant_id.lower().startswith("replace-with-"):
            raise RuntimeError("Production payments require ZARINPAL_MERCHANT_ID")
        if settings.zarinpal_sandbox:
            raise RuntimeError("Production payments cannot use ZarinPal Sandbox")

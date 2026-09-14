# Architecture

Telegram Bot API
→ FastAPI webhook
→ PostgreSQL/SQLite
→ usage + referral + loyalty services
→ provider-agnostic AI layer
→ local Ollama for development / OpenAI-compatible provider for Production

## Production upgrades
- PostgreSQL
- Redis + job queue
- object storage for assets
- admin dashboard
- observability
- rate limits
- abuse detection
- payment adapter after compliance review

## AI
Development can use Ollama locally. Production is configured for an externally reachable OpenAI-compatible provider. Model names and API credentials are supplied through environment variables; the application keeps provider/model selection separate from the generation workflow.

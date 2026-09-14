from app.config import settings

raw = settings.database_url

print("Length:", len(raw))
print("Starts:", repr(raw[:25]))
print("Ends:", repr(raw[-25:]))
print("Has @:", "@" in raw)
print("Has postgresql+asyncpg:", "postgresql+asyncpg" in raw)
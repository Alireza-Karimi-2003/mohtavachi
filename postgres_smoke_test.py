import asyncio
from sqlalchemy import select, func, text
from app.db import (
    engine,
    SessionLocal,
    User,
    Generation,
    DailySuggestion,
    Referral,
    SupportTicket,
    PaymentOrder,
)


async def main():
    try:
        async with engine.connect() as conn:
            version = await conn.scalar(text("SELECT version()"))
            print("PostgreSQL connection: PASS")
            print("Database:", version.splitlines()[0])

        async with SessionLocal() as session:
            checks = [
                ("users", User),
                ("generations", Generation),
                ("daily_suggestions", DailySuggestion),
                ("referrals", Referral),
                ("support_tickets", SupportTicket),
                ("payment_orders", PaymentOrder),
            ]

            for name, model in checks:
                count = await session.scalar(select(func.count()).select_from(model))
                print(f"{name}: {count}")

            user = await session.scalar(select(User).order_by(User.id).limit(1))
            generation = await session.scalar(
                select(Generation).order_by(Generation.id).limit(1)
            )

            if user is None:
                raise RuntimeError("Expected migrated User record was not found")

            if generation is None:
                raise RuntimeError("Expected migrated Generation record was not found")

            if generation.status is None:
                raise RuntimeError("Generation.status could not be read from PostgreSQL")

            print("ORM User read: PASS")
            print("ORM Generation read: PASS")
            print("Generation.status read: PASS")
            print("ORM smoke test: PASS")

    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

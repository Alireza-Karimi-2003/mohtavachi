FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY set_webhook.py ./set_webhook.py
COPY start_production.sh ./start_production.sh

RUN chmod +x /app/start_production.sh

EXPOSE 8000

CMD ["/app/start_production.sh"]

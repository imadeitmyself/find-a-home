FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY rental_alert_bot ./rental_alert_bot
COPY config.example.json ./config.json

RUN pip install --no-cache-dir .

VOLUME ["/app/data"]

CMD ["find-a-home", "run", "--config", "config.json"]

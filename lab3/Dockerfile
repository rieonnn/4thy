# Многостадийная сборка: зависимости собираются в отдельном этапе.
FROM python:3.12-slim AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000 LOG_LEVEL=info
RUN groupadd --system app && useradd --system --gid app --create-home app
COPY --from=builder /install /usr/local
WORKDIR /app
COPY app ./app
USER app:app
EXPOSE 8000
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT} --workers 1 --log-level ${LOG_LEVEL} app.web:app"]

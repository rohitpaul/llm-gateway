FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir fastapi[standard] uvicorn[standard] httpx aiosqlite pyyaml python-dotenv pydantic

COPY app/ app/
COPY templates/ templates/
COPY static/ static/

VOLUME /data

EXPOSE 4000

CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "4000"]

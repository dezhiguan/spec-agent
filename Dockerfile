# Replace with keel/python-runtime when that base image is published.
FROM python:3.11-slim
WORKDIR /app
COPY . /app
# docker build --secret id=KEEL_GITHUB_TOKEN,env=KEEL_GITHUB_TOKEN .
RUN --mount=type=secret,id=KEEL_GITHUB_TOKEN \
    KEEL_GITHUB_TOKEN="$(cat /run/secrets/KEEL_GITHUB_TOKEN)" \
    python scripts/install-keel-sdk.py \
 && pip install --no-cache-dir -r requirements.lock
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]

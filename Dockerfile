# CPU development image. Official GPU image/base digest awaits organizer guidance.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1
WORKDIR /app
COPY requirements.lock.txt pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements.lock.txt \
    && pip install --no-cache-dir --no-deps . \
    && useradd --uid 10001 --create-home agent \
    && mkdir -p /app/outputs \
    && chown agent:agent /app/outputs
COPY configs ./configs
COPY tests/fixtures ./tests/fixtures
USER 10001:10001
ENTRYPOINT ["datamate"]
CMD ["test-fixtures", "--config", "configs/default.yaml"]

FROM python:3.12.10-slim-bookworm@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db
ENTRYPOINT []
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=/code/src
WORKDIR /code
COPY requirements.txt /code/requirements.txt
RUN pip install --no-cache-dir -r /code/requirements.txt
COPY training_code/ /code/training_code/
COPY configs/ /code/configs/
COPY calibration/ /code/calibration/
COPY scripts/ /code/scripts/
COPY docs/ /code/docs/
COPY predict.sh README.md pyproject.toml architecture.mmd /code/
COPY src/ /code/src/
RUN pip install --no-cache-dir --no-deps -e /code \
    && chmod +x /code/predict.sh /code/training_code/train.sh \
    && python -c "import json; from pathlib import Path; from datamate.config import load_config; from datamate.decision.calibration import inference_settings, runtime_signature; c = load_config(Path('/code/calibration/deployment-config.yaml')); a = json.loads(c.calibration_artifact.read_text()); assert a['runtime_signature'] == runtime_signature(inference_settings(c)), 'calibration/runtime mismatch'"
CMD ["bash", "/code/predict.sh"]

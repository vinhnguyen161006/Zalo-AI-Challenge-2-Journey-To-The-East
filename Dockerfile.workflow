ARG RUNTIME_IMAGE=datamate-local:qwen35-runtime
FROM ${RUNTIME_IMAGE}
ARG EXPECTED_RUNTIME_SIGNATURE
RUN python -c "import os; from datamate.decision.calibration import runtime_signature; assert os.environ.get('EXPECTED_RUNTIME_SIGNATURE') == runtime_signature({}), 'Runtime differs: rebuild Dockerfile.offline instead of refreshing workflow only'"
COPY scripts /app/scripts
COPY configs /app/configs
COPY docs /app/docs
COPY README.md architecture.mmd predict.sh train.sh Dockerfile.offline Dockerfile.calibrated Dockerfile.workflow /app/
RUN chmod +x /app/predict.sh /app/train.sh

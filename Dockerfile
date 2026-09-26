FROM python:3.12-slim

WORKDIR /lab
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    COMMERCE_LAB_ROOT=/lab \
    COMMERCE_LAB_RUNS=/lab/runs

COPY pyproject.toml requirements.lock ./
COPY lab lab
COPY drivers drivers
COPY fixtures fixtures
COPY fault_proxy fault_proxy
COPY reference_targets reference_targets
COPY schemas schemas
COPY manifests manifests
COPY suites suites
COPY dashboard dashboard
COPY docs docs

RUN pip install --no-cache-dir -e .

EXPOSE 8000 8080
CMD ["commerce-lab", "serve-target", "--target", "local-merchant-corrected", "--host", "0.0.0.0", "--port", "8000"]

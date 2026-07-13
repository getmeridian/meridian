# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client curl openssl jq ca-certificates git bash procps iproute2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# Install Python deps first (cached unless pyproject.toml changes)
COPY pyproject.toml VERSION README.md ./
COPY scripts/hatch_build.py scripts/hatch_build.py
COPY src/ src/
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir .

# Copy test scripts and fixtures (small, changes often)
COPY tests/systemlab/ tests/systemlab/

CMD ["bash"]

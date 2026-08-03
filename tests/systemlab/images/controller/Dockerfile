FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN printf 'Acquire::Retries "3";\nAPT::Update::Error-Mode "any";\n' \
      > /etc/apt/apt.conf.d/80-meridian-network \
    && apt-get update && apt-get install -y --no-install-recommends \
    openssh-client curl openssl jq ca-certificates git bash procps iproute2 \
    && rm -rf /var/lib/apt/lists/*

# Trust Pebble's HTTPS endpoint; bootstrap installs its runtime issuance root.
COPY tests/systemlab/fixtures/pebble-ca.pem /usr/local/share/ca-certificates/pebble-ca.crt
RUN update-ca-certificates
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

WORKDIR /workspace

# Install Python deps first (cached unless pyproject.toml changes)
COPY pyproject.toml VERSION README.md ./
COPY scripts/hatch_build.py scripts/hatch_build.py
COPY src/ src/
RUN pip install --no-cache-dir .

# Copy test scripts and fixtures (small, changes often)
COPY tests/systemlab/ tests/systemlab/

CMD ["bash"]

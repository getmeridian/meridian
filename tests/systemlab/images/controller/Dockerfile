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

# Client engines that check the delivered sing-box and Mihomo documents
# (scripts/check-client-engines.py). Move a version with both checksums; the
# client-management docs name the tested versions. Pinned rule data lets
# `mihomo -t` load GEOSITE/GEOIP rules without downloading anything.
ARG TARGETARCH
ARG SING_BOX_VERSION=1.14.4
ARG SING_BOX_SHA256_AMD64=699a1b510aa4396b2176d32d1181920399ebf1e267835da6f3b598d0f7c24968
ARG SING_BOX_SHA256_ARM64=436441f76ee2f7fe21b5b2d426d3cb261a480455a704865789ad533e4273ec26
ARG MIHOMO_VERSION=1.19.34
ARG MIHOMO_SHA256_AMD64=2763f5b414527f7baf6dc57e3f82f3ab561eedf34bfd7c2f2cde1f7e592efe06
ARG MIHOMO_SHA256_ARM64=daaf78a6240a282cdfd75bcbf98fe321f6bd52765cc29e79f344191198f0b50f
ARG GEOSITE_RELEASE=202611202204
ARG GEOSITE_SHA256=f0320d0cf22431eace345946a5fb196a6654e3e24441dffa3ea38ef28cd44289
ARG GEOIP_RELEASE=202611190019
ARG GEOIP_SHA256=9840946762fa8c2673738bbbbab22bb720bde931f6dea327eb58e689babc37dc
ENV MIHOMO_GEODATA_DIR=/opt/mihomo-geodata
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) sing_box_sha256=$SING_BOX_SHA256_AMD64; mihomo_asset=amd64-compatible; mihomo_sha256=$MIHOMO_SHA256_AMD64 ;; \
      arm64) sing_box_sha256=$SING_BOX_SHA256_ARM64; mihomo_asset=arm64; mihomo_sha256=$MIHOMO_SHA256_ARM64 ;; \
      *) echo "No pinned client engines for architecture '$TARGETARCH'" >&2; exit 1 ;; \
    esac; \
    work=$(mktemp -d); \
    sing_box_dir="sing-box-${SING_BOX_VERSION}-linux-${TARGETARCH}"; \
    curl -fsSL --retry 3 -o "$work/sing-box.tar.gz" \
      "https://github.com/SagerNet/sing-box/releases/download/v${SING_BOX_VERSION}/${sing_box_dir}.tar.gz"; \
    echo "$sing_box_sha256  $work/sing-box.tar.gz" | sha256sum -c -; \
    tar -xzf "$work/sing-box.tar.gz" -C "$work"; \
    install -m 0755 "$work/$sing_box_dir/sing-box" /usr/local/bin/sing-box; \
    curl -fsSL --retry 3 -o "$work/mihomo.gz" \
      "https://github.com/MetaCubeX/mihomo/releases/download/v${MIHOMO_VERSION}/mihomo-linux-${mihomo_asset}-v${MIHOMO_VERSION}.gz"; \
    echo "$mihomo_sha256  $work/mihomo.gz" | sha256sum -c -; \
    gunzip "$work/mihomo.gz"; \
    install -m 0755 "$work/mihomo" /usr/local/bin/mihomo; \
    mkdir -p "$MIHOMO_GEODATA_DIR"; \
    curl -fsSL --retry 3 -o "$MIHOMO_GEODATA_DIR/GeoSite.dat" \
      "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/download/${GEOSITE_RELEASE}/geosite.dat"; \
    echo "$GEOSITE_SHA256  $MIHOMO_GEODATA_DIR/GeoSite.dat" | sha256sum -c -; \
    curl -fsSL --retry 3 -o "$MIHOMO_GEODATA_DIR/Country.mmdb" \
      "https://github.com/Loyalsoldier/geoip/releases/download/${GEOIP_RELEASE}/Country.mmdb"; \
    echo "$GEOIP_SHA256  $MIHOMO_GEODATA_DIR/Country.mmdb" | sha256sum -c -; \
    rm -rf "$work"; \
    sing-box version | grep -qF "sing-box version ${SING_BOX_VERSION}"; \
    mihomo -v | grep -qF "v${MIHOMO_VERSION} "

# uv builds this checkout's wheel for the control server (meridian.control_artifacts).
ARG UV_VERSION=0.13.9
RUN pip install --no-cache-dir "uv==${UV_VERSION}"

WORKDIR /workspace

# An editable install: the deployer then uploads this checkout's wheel and agent build to
# the servers instead of the released meridian-vpn. Dependencies are cached unless
# pyproject.toml changes.
COPY pyproject.toml VERSION README.md CHANGELOG.md ./
COPY scripts/hatch_build.py scripts/hatch_build.py
COPY src/ src/
RUN pip install --no-cache-dir -e . pytest

# The node agent for this architecture (`make agent`; the Makefile and CI build it first).
COPY agent/dist/ agent/dist/
RUN test -n "$(ls agent/dist/netagent-*-unknown-linux-musl 2>/dev/null)" \
    || { echo "agent/dist has no netagent build: run make agent" >&2; exit 1; }

# Test scripts, fixtures, and the corpus comparison the live golden diff reuses.
COPY tests/__init__.py tests/__init__.py
COPY tests/test_control_subscription_corpus.py tests/test_control_subscription_corpus.py
COPY tests/systemlab/ tests/systemlab/

CMD ["bash"]

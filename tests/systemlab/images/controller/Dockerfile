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
ARG SING_BOX_VERSION=1.14.3
ARG SING_BOX_SHA256_AMD64=e2bdf179c15a3652955dc44867e33ca0965c9c9b91b34267dcc5a5d639a5feee
ARG SING_BOX_SHA256_ARM64=29dae24d76bea3bc62d07dd3e60cfc12003de7debbd6b768df9be9c68360174e
ARG MIHOMO_VERSION=1.19.32
ARG MIHOMO_SHA256_AMD64=ba3ce607747a07f948fc35780e108a4a7c7f552a38b9bd4d115f313ebcb89c20
ARG MIHOMO_SHA256_ARM64=9dd862e28b46ff7d775f169cceebc28deccaa0a9e804237d421cd2571e0caba0
ARG GEOSITE_RELEASE=202610132205
ARG GEOSITE_SHA256=c5a73fbd415bb0506ecf8fa574caadc6f54a71d9308c265e97da49e4cebf8466
ARG GEOIP_RELEASE=202610080033
ARG GEOIP_SHA256=ad2a3b90848bccd2065545e295de53b324931f710e0077a1a0fae2b89fddc5e5
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

WORKDIR /workspace

# Install Python deps first (cached unless pyproject.toml changes)
COPY pyproject.toml VERSION README.md ./
COPY scripts/hatch_build.py scripts/hatch_build.py
COPY src/ src/
RUN pip install --no-cache-dir .

# Copy test scripts and fixtures (small, changes often)
COPY tests/systemlab/ tests/systemlab/

CMD ["bash"]

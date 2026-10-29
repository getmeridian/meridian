#!/bin/sh
# Build static netagent binaries inside the pinned rust:alpine image (run by `make agent`).
# Usage: build-musl.sh TARGET...   (source at /src, output to /out, CARGO_TARGET_DIR set)
# The container's own architecture builds natively; the other cross-compiles with
# zig cc (ring's C code) and rust-lld (the target's bundled musl), so no emulation is needed.
set -eu
native="$(uname -m)-unknown-linux-musl"
# The build id names these sources, so an upgraded agent's hello differs from the old one's (build.rs).
NETAGENT_BUILD_ID=$(find build.rs Cargo.toml Cargo.lock src -type f | LC_ALL=C sort | xargs cat | sha256sum | cut -c1-12)
export NETAGENT_BUILD_ID
version="$(sed -n 's/^version = "\(.*\)"$/\1/p' Cargo.toml | head -n 1)+$NETAGENT_BUILD_ID"
for target in "$@"; do
  if [ "$target" != "$native" ]; then
    arch=${target%%-*}
    command -v zig >/dev/null 2>&1 || apk add --no-cache zig >/dev/null
    rustup target add "$target" >/dev/null 2>&1
    cc="/usr/local/bin/$arch-musl-cc"
    cat > "$cc" <<SCRIPT
#!/bin/sh
# cc-rs passes its own --target, which zig cc rejects.
for arg; do shift; case "\$arg" in --target=*) ;; *) set -- "\$@" "\$arg" ;; esac; done
exec zig cc -target $arch-linux-musl "\$@"
SCRIPT
    printf '#!/bin/sh\nexec zig ar "$@"\n' > /usr/local/bin/zig-ar
    chmod +x "$cc" /usr/local/bin/zig-ar
    lower=$(echo "$target" | tr - _)
    upper=$(echo "$lower" | tr a-z A-Z)
    export "CC_$lower=$cc" "AR_$lower=/usr/local/bin/zig-ar" "CARGO_TARGET_${upper}_LINKER=rust-lld"
  fi
  cargo build --locked --release --target "$target"
  cp "$CARGO_TARGET_DIR/$target/release/netagent" "/out/netagent-$target"
  (cd /out && sha256sum "netagent-$target" | cut -d' ' -f1 > "netagent-$target.sha256")
  # The version the binary reports; `meridian check` compares each node's with it.
  echo "$version" > "/out/netagent-$target.version"
done

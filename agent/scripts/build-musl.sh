#!/bin/sh
# Build static netagent binaries inside the pinned rust:alpine image (run by `make agent`).
# Usage: build-musl.sh TARGET...   (source at /src, output to /out, CARGO_TARGET_DIR set)
# The container's own architecture builds natively; the other cross-compiles with
# zig cc (ring's C code) and rust-lld (the target's bundled musl), so no emulation is needed.
set -eu
native="$(uname -m)-unknown-linux-musl"
for target in "$@"; do
  if [ "$target" != "$native" ]; then
    arch=${target%%-*}
    command -v zig >/dev/null 2>&1 || apk add --no-cache zig >/dev/null
    rustup target add "$target" >/dev/null 2>&1
    cc="/usr/local/bin/$arch-musl-cc"
    cat > "$cc" <<EOF
#!/bin/sh
# cc-rs passes its own --target, which zig cc rejects.
for arg; do shift; case "\$arg" in --target=*) ;; *) set -- "\$@" "\$arg" ;; esac; done
exec zig cc -target $arch-linux-musl "\$@"
EOF
    printf '#!/bin/sh\nexec zig ar "$@"\n' > /usr/local/bin/zig-ar
    chmod +x "$cc" /usr/local/bin/zig-ar
    lower=$(echo "$target" | tr - _)
    upper=$(echo "$lower" | tr a-z A-Z)
    export "CC_$lower=$cc" "AR_$lower=/usr/local/bin/zig-ar" "CARGO_TARGET_${upper}_LINKER=rust-lld"
  fi
  cargo build --locked --release --target "$target"
  cp "$CARGO_TARGET_DIR/$target/release/netagent" "/out/netagent-$target"
  (cd /out && sha256sum "netagent-$target" | cut -d' ' -f1 > "netagent-$target.sha256")
done

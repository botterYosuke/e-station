#!/bin/bash
set -euo pipefail
TARGET="flowsurface"
ENGINE="flowsurface-engine"
VERSION=$(grep '^version = ' Cargo.toml | cut -d'"' -f2)
ARCH=${1:-universal} # x86_64 | aarch64 | universal
RELEASE_DIR="target/release"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

export MACOSX_DEPLOYMENT_TARGET="11.0"

rustup target add x86_64-apple-darwin
rustup target add aarch64-apple-darwin

mkdir -p "$RELEASE_DIR"

# PyInstaller cannot cross-build on macOS — the engine binary is locked to
# the host architecture.  We refuse to ship a mismatched engine inside an
# archive labelled for a different arch (would silently break on the user's
# Mac).  Set `SKIP_ENGINE_ARCH_CHECK=1` to override (e.g. when each arch is
# built on a separate runner that produces its own host-native engine, or
# when intentionally producing a viewer-only archive).
HOST_UNAME="$(uname -m)"  # x86_64 or arm64
case "$HOST_UNAME" in
  arm64)  HOST_ARCH="aarch64" ;;
  x86_64) HOST_ARCH="x86_64" ;;
  *)      HOST_ARCH="$HOST_UNAME" ;;
esac

build_engine_once() {
  "$REPO_ROOT/scripts/build-engine.sh"
  ENGINE_BIN="$REPO_ROOT/target/release/python-engine/$ENGINE"
  if [ ! -f "$ENGINE_BIN" ]; then
    echo "build-macos: engine binary not produced at $ENGINE_BIN" >&2
    exit 1
  fi
}

# Bundle the engine only if it matches the package arch.  `universal` is
# treated as host-arch-only — there is no fat PyInstaller output, so a
# universal archive can only ship a host-arch engine and would still fail
# on the opposite arch.  Refuse rather than ship a broken artifact.
package() {
  local arch="$1"
  local archive="$RELEASE_DIR/${TARGET}-${arch}-macos.tar.gz"

  local include_engine=1
  if [ "$arch" = "universal" ]; then
    include_engine=0
  elif [ "$arch" != "$HOST_ARCH" ]; then
    include_engine=0
  fi

  if [ "$include_engine" -eq 0 ] && [ "${SKIP_ENGINE_ARCH_CHECK:-0}" != "1" ]; then
    echo "build-macos: refusing to bundle host-arch engine ($HOST_ARCH) into '$arch' archive."
    echo "             Build the engine on a matching-arch host and re-run, or set"
    echo "             SKIP_ENGINE_ARCH_CHECK=1 to ship a viewer-only archive."
    exit 1
  fi

  if [ "$include_engine" -eq 1 ]; then
    build_engine_once
    cp "$ENGINE_BIN" "$RELEASE_DIR/$ENGINE"
    tar -czf "$archive" -C "$RELEASE_DIR" "$TARGET" "$ENGINE"
  else
    echo "build-macos: WARNING — packaging '$arch' WITHOUT engine (arch mismatch)."
    tar -czf "$archive" -C "$RELEASE_DIR" "$TARGET"
  fi
  echo "Created $archive"
}

if [ "$ARCH" = "x86_64" ]; then
  cargo build --release --target=x86_64-apple-darwin
  cp "target/x86_64-apple-darwin/release/$TARGET" "$RELEASE_DIR/$TARGET"
  package "x86_64"
  exit 0
fi

if [ "$ARCH" = "aarch64" ]; then
  cargo build --release --target=aarch64-apple-darwin
  cp "target/aarch64-apple-darwin/release/$TARGET" "$RELEASE_DIR/$TARGET"
  package "aarch64"
  exit 0
fi

# default: build both and create universal viewer + host-arch engine
cargo build --release --target=x86_64-apple-darwin
cargo build --release --target=aarch64-apple-darwin

lipo "target/x86_64-apple-darwin/release/$TARGET" \
     "target/aarch64-apple-darwin/release/$TARGET" \
     -create -output "$RELEASE_DIR/$TARGET"
package "universal"

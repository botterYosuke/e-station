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

# Build the Python data engine once (PyInstaller is not cross-arch on macOS;
# the resulting binary inherits the host architecture).  Universal viewer +
# host-arch engine is acceptable for Phase 6 — the engine is forked, not
# linked into the viewer.
"$REPO_ROOT/scripts/build-engine.sh"
ENGINE_BIN="$REPO_ROOT/target/release/python-engine/$ENGINE"
if [ ! -f "$ENGINE_BIN" ]; then
  echo "build-macos: engine binary not produced at $ENGINE_BIN" >&2
  exit 1
fi

package() {
  local arch="$1"
  local archive="$RELEASE_DIR/${TARGET}-${arch}-macos.tar.gz"

  cp "$ENGINE_BIN" "$RELEASE_DIR/$ENGINE"
  tar -czf "$archive" -C "$RELEASE_DIR" "$TARGET" "$ENGINE"
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

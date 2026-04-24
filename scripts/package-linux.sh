#!/bin/bash
set -euo pipefail
ARCH=${2:-x86_64}  # if first arg is "package" then $2 holds arch
TARGET="flowsurface"
ENGINE="flowsurface-engine"
PROFILE="release"
RELEASE_DIR="target/$PROFILE"
ARCHIVE_DIR="$RELEASE_DIR/archive"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENGINE_BIN="$REPO_ROOT/target/release/python-engine/$ENGINE"

if [ "$ARCH" = "aarch64" ]; then
  TRIPLE="aarch64-unknown-linux-gnu"
  ARCHIVE_NAME="$TARGET-aarch64-linux.tar.gz"
else
  TRIPLE="x86_64-unknown-linux-gnu"
  ARCHIVE_NAME="$TARGET-x86_64-linux.tar.gz"
fi

ARCHIVE_PATH="$RELEASE_DIR/$ARCHIVE_NAME"
BINARY="target/$TRIPLE/$PROFILE/$TARGET"

build() {
  rustup target add "$TRIPLE"
  cargo build --release --target="$TRIPLE"
}

build_engine() {
  "$REPO_ROOT/scripts/build-engine.sh"
  if [ ! -f "$ENGINE_BIN" ]; then
    echo "package-linux: engine binary not produced at $ENGINE_BIN" >&2
    exit 1
  fi
}

archive_name() {
  echo $ARCHIVE_NAME
}

archive_path() {
  echo $ARCHIVE_PATH
}

package() {
  build
  build_engine
  mkdir -p "$ARCHIVE_DIR/bin"
  install -Dm755 "$BINARY" -t "$ARCHIVE_DIR/bin"
  install -Dm755 "$ENGINE_BIN" -t "$ARCHIVE_DIR/bin"
  if [ -d "assets" ]; then
    cp -r assets "$ARCHIVE_DIR/"
  fi
  tar czvf "$ARCHIVE_PATH" -C "$ARCHIVE_DIR" .
  echo "Packaged archive: $ARCHIVE_PATH"
}

case "$1" in
  "package") package;;
  "archive_name") archive_name;;
  "archive_path") archive_path;;
  *)
    echo "available commands: package, archive_name, archive_path"
    ;;
esac
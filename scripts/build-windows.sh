#!/bin/bash
set -euo pipefail
EXE_NAME="flowsurface.exe"
ARCH=${1:-x86_64} # x86_64 | aarch64
VERSION=$(grep '^version = ' Cargo.toml | cut -d'"' -f2)
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# update package version on Cargo.toml
cargo install cargo-edit
cargo set-version "$VERSION"

rustup override set stable-msvc

# set target triple and zip name
if [ "$ARCH" = "aarch64" ]; then
  TARGET_TRIPLE="aarch64-pc-windows-msvc"
  ZIP_NAME="flowsurface-aarch64-windows.zip"
else
  TARGET_TRIPLE="x86_64-pc-windows-msvc"
  ZIP_NAME="flowsurface-x86_64-windows.zip"
fi

# build Rust viewer
rustup target add "$TARGET_TRIPLE"
cargo build --release --target="$TARGET_TRIPLE"

# build Python data engine via PyInstaller (Phase 6 distribution)
"$REPO_ROOT/scripts/build-engine.sh"
ENGINE_BIN="$REPO_ROOT/target/release/python-engine/flowsurface-engine.exe"
if [ ! -f "$ENGINE_BIN" ]; then
  echo "build-windows: engine binary not produced at $ENGINE_BIN" >&2
  exit 1
fi

# create staging directory
STAGING="target/release/win-portable"
mkdir -p "$STAGING"

# copy executables and assets
cp "target/$TARGET_TRIPLE/release/$EXE_NAME" "$STAGING/"
cp "$ENGINE_BIN" "$STAGING/"
if [ -d "assets" ]; then
    cp -r assets "$STAGING/"
fi

# create zip archive
cd target/release
powershell -Command "Compress-Archive -Path win-portable\* -DestinationPath $ZIP_NAME -Force"
echo "Created $ZIP_NAME"

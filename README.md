# Flowsurface

[![Crates.io](https://img.shields.io/crates/v/flowsurface)](https://crates.io/crates/flowsurface)
[![Lint](https://github.com/flowsurface-rs/flowsurface/actions/workflows/lint.yml/badge.svg)](https://github.com/flowsurface-rs/flowsurface/actions/workflows/lint.yml)
[![Format](https://github.com/flowsurface-rs/flowsurface/actions/workflows/format.yml/badge.svg)](https://github.com/flowsurface-rs/flowsurface/actions/workflows/format.yml)
[![Discord](https://img.shields.io/badge/Discord-%235865F2.svg?&logo=discord&logoColor=white)](https://discord.gg/RN2XAF7ZuR)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://github.com/flowsurface-rs/flowsurface/blob/main/LICENSE)
[![Made with iced](https://iced.rs/badge.svg)](https://github.com/iced-rs/iced)

An open-source native desktop charting application for crypto markets. Supports Binance, Bybit, Hyperliquid, OKX, and MEXC.

<div align="center">
  <img
    src="https://github.com/user-attachments/assets/baddc444-e079-48e5-82b2-4f97094eba07"
    alt="Flowsurface screenshot"
    style="max-width: 100%; height: auto;"
  />
</div>

### Key Features

-   Multiple chart/panel types:
    -   **Heatmap (Historical DOM):** Uses live trades and L2 orderbook to create a time-series heatmap chart. Supports customizable price grouping, different time aggregations, fixed or visible range volume profiles.
    -   **Candlestick:** Traditional kline chart supporting both time-based and custom tick-based intervals.
    -   **Footprint:** Price grouped and interval aggregated views for trades on top of a candlestick chart. Supports different clustering methods, configurable imbalance and naked-POC studies.
    -   **Time & Sales:** Scrollable list of live trades.
    -   **DOM (Depth of Market) / Ladder:** Displays current L2 orderbook alongside recent trade volumes on grouped price levels.
    -   **Comparison:** Line graph for comparing multiple data sources, normalized by kline `close` prices on a percentage scale
-   Real-time sound effects driven by trade streams
-   Multi window/monitor support
-   Pane linking for quickly switching tickers across multiple panes
-   Persistent layouts and customizable themes with editable color palettes

##### Market data is received directly from exchanges' public REST APIs and WebSockets

#

#### Historical Trades on Footprint Charts:

-   By default, it captures and plots live trades in real time via WebSocket.
-   For Binance tickers, you can optionally backfill the visible time range by enabling trade fetching in the settings:
    -   [data.binance.vision](https://data.binance.vision/): Fast daily bulk downloads (no intraday).
    -   REST API (e.g., `/fapi/v1/aggTrades`): Slower, paginated intraday fetching (subject to rate limits).
    -   The Binance connector can use either or both methods to retrieve historical data as needed.
-   Fetching trades for Bybit/Hyperliquid is not supported, as both lack a suitable REST API. OKX is WIP.

## Installation

### Method 1: Prebuilt Binaries

Standalone executables are available for Windows, macOS, and Linux on the [Releases page](https://github.com/flowsurface-rs/flowsurface/releases).

<details>
<summary><strong>Having trouble running the file? (Permission/Security warnings)</strong></summary>
 
Since these binaries are currently unsigned they might get flagged.

-   **Windows**: If you see a "Windows protected your PC" pop-up, click **More info** -> **Run anyway**.
-   **macOS**: If you see "Developer cannot be verified", control-click (right-click) the app and select **Open**, or go to _System Settings > Privacy & Security_ to allow it.
</details>

### Method 2: Build from Source

#### Requirements

-   [Rust toolchain](https://www.rust-lang.org/tools/install)
-   [Python 3.11+](https://www.python.org/downloads/) — runs the data engine
-   [`uv`](https://github.com/astral-sh/uv) (recommended) for managing the Python environment
-   [Git version control system](https://git-scm.com/)
-   System dependencies:
    -   **Linux**:
        -   Debian/Ubuntu: `sudo apt install build-essential pkg-config libasound2-dev`
        -   Arch: `sudo pacman -S base-devel alsa-lib`
        -   Fedora: `sudo dnf install gcc make alsa-lib-devel`
    -   **macOS**: Install Xcode Command Line Tools: `xcode-select --install`
    -   **Windows**: No additional dependencies required

#### Option A: Cloning the repo (recommended for development)

```bash
git clone https://github.com/flowsurface-rs/flowsurface
cd flowsurface

# Install Python deps (the data engine that supplies all market data)
uv sync

# Run viewer + data engine.  When `--data-engine-url` is omitted, the
# viewer spawns and supervises the engine automatically.
cargo run --release
```

If `uv` is unavailable, set `--engine-cmd` to point at a Python interpreter that
has the `engine` package importable:

```bash
PYTHONPATH=python cargo run --release -- --engine-cmd python3
```

To attach the viewer to an externally managed engine (useful when iterating on
Python code), launch the engine first and pass its WebSocket URL:

```bash
# Terminal 1 — engine on a fixed dev port
FLOWSURFACE_ENGINE_TOKEN=devtoken \
PYTHONPATH=python python -m engine --port 8765 --token devtoken

# Terminal 2 — viewer connects, does not spawn its own engine
FLOWSURFACE_ENGINE_TOKEN=devtoken \
cargo run --release -- --data-engine-url ws://127.0.0.1:8765
```

#### Option B: Building a redistributable bundle

The release scripts under [`scripts/`](./scripts/) freeze the engine into a
single executable (via [PyInstaller](https://pyinstaller.org/)) and ship it
alongside the viewer.  Install PyInstaller first:

```bash
uv tool install pyinstaller   # recommended
# or: pip install pyinstaller
```

Then run the platform-specific script:

```bash
scripts/build-windows.sh      # → target/release/win-portable/
scripts/build-macos.sh        # → target/release/flowsurface-*-macos.tar.gz
scripts/package-linux.sh package
```

Each archive contains both `flowsurface(.exe)` and `flowsurface-engine(.exe)`.
At runtime the viewer locates the engine binary next to its own executable, so
the user does not need a system Python install.

### Runtime behaviour

-   **Logs**: viewer logs go to `flowsurface.log` (same directory as the
    binary on portable installs, or the user data folder otherwise).  The
    engine's stdout/stderr is forwarded into the same file under the `engine`
    log target — no separate Python log to chase.
-   **Engine supervision**: if the engine process crashes, the viewer
    surfaces a "data engine restarting" toast, restarts the engine with
    exponential backoff (500 ms → 30 s cap), reapplies the proxy, and
    re-subscribes every active stream.  Charts repopulate automatically.

## Credits and thanks to

-   [Kraken Desktop](https://www.kraken.com/desktop) (formerly [Cryptowatch](https://blog.kraken.com/product/cryptowatch-to-sunset-kraken-pro-to-integrate-cryptowatch-features)), the main inspiration that sparked this project
-   [Halloy](https://github.com/squidowl/halloy), an excellent open-source reference for the foundational code design and the project architecture
-   And of course, [iced](https://github.com/iced-rs/iced), the GUI library that makes all of this possible

## Community

For feedback, questions, or for more casual conversations about the project, join our community on Discord:  
https://discord.gg/RN2XAF7ZuR

## License

Flowsurface is released under the [GPLv3](./LICENSE) license. Contributions to the project are shared under the same license.  

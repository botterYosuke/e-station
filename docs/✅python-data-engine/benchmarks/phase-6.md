# Phase 6 ベンチマーク — `onefile` cold-start 計測

計測日: 2026-04-25
対象ブランチ: `tachibana/phase-0/plan` (Phase 7 T4.c)
対象成果物: `target/release/python-engine/flowsurface-engine.exe` (PyInstaller `onefile` バンドル)

---

## 1. 計測方針 (Phase 7 T4.c)

PyInstaller `onefile` 形式では、起動のたびにバンドルを一時ディレクトリに展開し、CPython ランタイムを起動して `engine` パッケージをインポートする。この **bootstrap オーバーヘッド** がユーザー体験に直結するため、`onefile` 採用判断 (spec §7.1) の根拠として実測値を残す。

- **計測対象**: `flowsurface-engine.exe` 単体の launch → "サーバが TCP 接続を受理可能" までの時間
- **判定方法**: `subprocess.Popen` で起動 → 50 ms 周期で `socket.create_connection("127.0.0.1", port)` を試行 → 最初に成功するまでの elapsed 時間
- **判定理由**: engine 内部に `logging.basicConfig` が無く ([python/engine/server.py:32](../../../python/engine/server.py#L32) の `log` は handler 未設定) `"Data engine listening on"` の log line を stdout でフックできない。Rust IPC クライアントから見える「接続可能になった瞬間」と等価な signal として TCP listen socket の accept ready を採用。
- **iteration**: 6 回連続実行。OS のページキャッシュは reboot 直後でなければ常時 warm 寄りなため、`first` を「半 cold（実際の初回ユーザー起動と近似）」、`warm runs` を「キャッシュ完全 hit のベスト値」として記録する。
- **計測スクリプト**: 本ドキュメント §5 に inline 化（再現用途）。

## 2. 環境

| 項目 | 値 |
|---|---|
| OS | Windows 11 Home 10.0.26200 |
| CPU | 13th Gen Intel Core i7-13700H |
| Storage | NVMe SSD (内蔵) |
| Python | CPython 3.12 (PyInstaller bootloader 経由で bundled) |
| PyInstaller | 6.20.0 |
| Engine binary size | 17,348,658 bytes (~16.5 MiB) |
| flowsurface.exe size | 19,655,168 bytes (~18.7 MiB) — 参考値 |

## 3. 結果

### 3.1 Engine `onefile` cold-start (Windows)

```
run 1: 0.782s   ← first (semi-cold, post-build)
run 2: 0.796s
run 3: 0.782s
run 4: 0.813s
run 5: 0.797s
run 6: 0.797s

first:        0.782s
warm min:     0.782s
warm median:  0.797s
warm max:     0.813s
```

| 指標 | 値 |
|---|---|
| Cold-start (first run) | **0.782 s** |
| Warm median | **0.797 s** |
| Warm max | 0.813 s |
| 観測幅 | 0.031 s (warm 5 runs) |

### 3.2 妥当性

- spec §9.1 の合格ライン「Python クラッシュ → 自動復旧完了 < 3 秒」の **約 1/4** に収まる。
- ユーザー視点では起動時の Rust GUI 立ち上がり（Iced + wgpu Vulkan init で数百 ms 規模）に隠蔽される範囲。
- 本計測では bootloader → Python interpreter init → `engine.server.DataEngineServer` インスタンス化 → `websockets.serve` の listen 完了までを含む。

### 3.3 macOS / Linux

- **本計測ラウンドでは取得していない**。GitHub Actions ランナー上で 3 OS 分を自動計測する CI ジョブは別 PR で追加予定 (Phase 7 計画書 §T4.c の deferred 注記参照)。
- 手動再現には §5 のスクリプトを当該 OS の `flowsurface-engine` 出力に対して実行する。

## 4. 発見事項 (out-of-scope, 別途修正推奨)

### 4.1 `scripts/build-engine.sh` の `uv tool run pyinstaller` バグ

[scripts/build-engine.sh:35](../../../scripts/build-engine.sh#L35) は PyInstaller を `uv tool run pyinstaller` で起動している。`uv tool run` は **isolated 環境**で PyInstaller を取ってくるため、プロジェクトの `pyproject.toml` 依存（`orjson` / `websockets` / `httpx` / `pydantic`）が解決されない。結果として PyInstaller が静的解析でこれらモジュールを検出できず、生成された `.exe` の起動時に `ModuleNotFoundError: No module named 'orjson'` で即死する。

**再現**:
```bash
bash scripts/build-engine.sh   # uv tool run pyinstaller がデフォルト
./target/release/python-engine/flowsurface-engine.exe --port 19879 --token x
# → ModuleNotFoundError: No module named 'orjson'
```

**ワークアラウンド**:
```bash
uv sync --extra build
PYINSTALLER="uv run pyinstaller" bash scripts/build-engine.sh
```

**影響**: CI で生成されている `flowsurface-engine.exe` は本番起動が壊れている可能性が高い (Phase 6 で配布タグを切る前に再検証が必要)。本計測は workaround で生成した正常 bundle を対象とした。

**対処方針 (別 PR)**:
- (a) `build-engine.sh` を `uv sync --extra build && uv run pyinstaller` ベースに置き換える、または
- (b) `uv tool run --with orjson --with websockets --with httpx --with pydantic pyinstaller` で必要 deps を inject する。

(a) が pyproject.toml と single source of truth を保てるため推奨。

## 5. 再現スクリプト

```python
# scripts/measure_engine_coldstart.py (記載のため inline。実ファイル化は不要)
import subprocess, time, sys, socket

EXE = sys.argv[1]
N = int(sys.argv[2]) if len(sys.argv) > 2 else 6

def measure_one(port):
    t0 = time.monotonic()
    proc = subprocess.Popen(
        [EXE, "--port", str(port), "--token", "coldstart"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    elapsed = None
    deadline = t0 + 60.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                elapsed = time.monotonic() - t0
                break
        except (ConnectionRefusedError, socket.timeout, OSError):
            time.sleep(0.05)
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    return elapsed

results = [measure_one(19890 + i) for i in range(N)]
for i, t in enumerate(results, 1):
    print(f"run {i}: {t:.3f}s" if t else f"run {i}: TIMEOUT")
```

実行:
```bash
PYINSTALLER="uv run pyinstaller" bash scripts/build-engine.sh
python scripts/measure_engine_coldstart.py target/release/python-engine/flowsurface-engine.exe 6
```

## 6. 結論

| 指標 | 結果 | 合格 |
|---|---|---|
| Engine `onefile` cold-start (Windows, first run) | 0.782 s | ✅ (spec §9.1 復旧 < 3s の 1/4 以下) |
| Engine `onefile` cold-start (Windows, warm median) | 0.797 s | ✅ |
| 観測のばらつき (warm) | 31 ms | ✅ (実用上無視可能) |
| macOS / Linux 計測 | 未取得 (CI PR で追加予定) | ⬜ |

`onefile` 形式はユーザー体験を損なうレベルの起動遅延をもたらさないことを確認。`onedir` への切替は現時点で不要 (Phase 7 §1.2 非スコープ)。

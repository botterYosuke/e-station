---
name: iced-gui-testing
description: >
  iced GUI アプリの動作確認・E2E テストを書くスキル。「〜ができなくなった」「動作確認して」
  「バグか確認して」という報告から始めて、まずコードバグか運用問題かを切り分け、
  必要に応じて回帰テストを追加する。3 つのテスト層を使い分ける:
  (0) **ソースコードピン**: ソースを読んで不変条件をアサートする最速手法（display 不要）。
  (1) **iced_test ヘッドレス**: ウィジェット操作・Message ルーティング・update ロジックを
  実ウィンドウなしで高速テスト。(2) **xcap + enigo + Claude Vision**: 実ウィンドウを
  起動してマウス/キーボードを操作し、スクリーンショットを Claude Vision API で検証する
  ビジュアル E2E テスト。
  「iced のテスト」「GUI automation」「スクリーンショットで assert」「ウィンドウ操作」
  「enigo」「xcap」「iced_test」「ビジュアルリグレッション」「動作確認」「できなくなった」
  「バグか確認」「配線確認」と言ったら必ず起動する。
  既存の Python/pytest e2e（`e2e-testing` スキル）を補完する Rust GUI 専用スキル。
---

# iced GUI テスト — 3 層アプローチ

e-station (Flowsurface) は Rust + iced 0.14 GUI。Python 側 E2E は `e2e-testing` スキル参照。
ここでは **Rust GUI 層のみ**をターゲットにする。

---

## タスクの入口 — コードバグか運用問題かを先に判断する

「〜ができなくなった」と報告されたとき、**テストを書く前に原因を特定する**。
Tier 0 相当のコードパス静的調査を先に行い、修正・テスト追加の要否を決める。

### 診断フロー

1. **エラーメッセージから発生層を特定する**
   - Rust UI 内で閉じているか（Message 配線・ハンドラ）
   - IPC 境界で失敗しているか（engine_client）
   - Python バックエンドが返したエラーか（コードか設定か）

2. **コードパスを静的にトレースする**
   - `grep` + `Read` でメッセージ配線・ハンドラ・バックエンド処理を追う
   - `.env`・`dev_flag`・依存サービスの状態も確認する

3. **判定して行動する**

   | 判定 | アクション |
   |---|---|
   | **コードバグ** | 修正 + 回帰テスト追加（以降のテスト手順へ） |
   | **設定・環境問題** | 原因と対処法を報告。必要なら配線ピンテストだけ追加 |
   | **テストカバレッジのギャップ** | コードは正常でもテストが存在しない配線はピン追加 |

### 対称性ギャップを見落とさない

調査中に「類似機能 A はテストがあるが B はない」というパターンに気づいたら積極的にピンを追加する。
例: 「立花 login chip のピンはあるが kabu chip のピンがなかった」。
`git show <commit> --stat` で直近の変更ファイルを確認すると漏れを発見しやすい。

---

## どれを使うか

| 検証したいこと | 使う層 |
|---|---|
| コードパスの存在確認・バグ/運用問題の切り分け | **Tier 0** (grep + Read 静的調査) |
| ハンドラが正しい IPC コマンドを送るか | **Tier 0** (ソースコードピン) |
| 特定のメッセージが特定の関数に配線されているか | **Tier 0** (ソースコードピン) |
| 関数が存在する・呼ばれる（回帰防止） | **Tier 0** (ソースコードピン) |
| Message ルーティング・update ロジック | **Tier 1** (iced_test) |
| ウィジェットのテキスト・表示状態 | **Tier 1** (iced_test) |
| 実際の描画・レイアウト崩れ | **Tier 2** (xcap + Vision) |
| OS レベルのウィンドウ操作（リサイズ等） | **Tier 2** のみ |
| CI（ディスプレイなし）| **Tier 0** または **Tier 1** 推奨 |

**選択の原則**: Tier 0 で書けるなら Tier 0 を優先する。`iced_test` は
コードベースで**実績がない**（2026-05 時点でテスト 0 件）ため、初使用時は
コンパイルエラーに備えること。

---

## Cargo.toml 設定

```toml
[dev-dependencies]
# Tier 1 — iced_test はすでに追加済み（ただし使用実績は 0 件）
iced_test = "0.14"   # iced バージョンと必ず一致させること

# Tier 2 — 追加が必要なクレート
enigo = "0.3"
xcap  = "0.0.14"
image = "0.25"
base64 = { version = "0.22", features = ["alloc"] }
reqwest = { version = "0.12", features = ["json"] }
serde_json = "1"
tokio = { version = "1", features = ["process", "time", "macros"] }
anyhow = "1"
```

ワークスペースで feature が衝突する場合は `flowsurface` crate の `[dev-dependencies]` に限定。

---

## Tier 0: ソースコードピンテスト

このコードベースで最も多用されているパターン。`src/` を `read_to_string` して
不変条件を文字列アサートする。iced_test も display も不要で最速に走る。

**このコードベースの実績**: `tests/tachibana_login_update.rs`、
`tests/status_bar_login_chip_pin.rs` など多数。

### 基本パターン

```rust
fn read_src(relative_path: &str) -> String {
    let base = env!("CARGO_MANIFEST_DIR");
    std::fs::read_to_string(format!("{base}/{relative_path}"))
        .unwrap_or_else(|_| panic!("{relative_path} not found"))
}

/// 対象関数の本体を切り出す。
/// ⚠️ max_bytes は関数全体を収めるのに十分な値にすること。
fn extract_fn_body(src: &str, fn_needle: &str, max_bytes: usize) -> String {
    let start = src
        .find(fn_needle)
        .unwrap_or_else(|| panic!("{fn_needle} not found"));
    let end = (start + max_bytes).min(src.len());
    src[start..end].to_string()
}

#[test]
fn handle_venue_calls_try_claim_login_in_flight() {
    let src = read_src("src/handlers/venue.rs");
    let body = extract_fn_body(&src, "VenueMsg::RequestTachibanaLogin(trigger)", 3000);
    assert!(
        body.contains("try_claim_login_in_flight()"),
        "ハンドラが try_claim_login_in_flight() を呼ばないと二重送信が起きる"
    );
}
```

### ⚠️ max_bytes のゴッチャ

`extract_fn_body` の `max_bytes` が小さいと、**関数末尾のアサートが silently 失敗する**。
例: 100 行の関数を `max_bytes = 2000` で切り出すと末尾 80 行が欠落。

#### テスト書く前に PowerShell で事前計測する（推奨）

関数開始からターゲット文字列まで何バイトか計測してから `max_bytes` を決めると確実:

```powershell
$src = Get-Content "src/main.rs" -Raw
$fnStart = $src.IndexOf("fn status_bar(")
$target  = $src.IndexOf("RequestKabuLogin(Trigger::Manual)", $fnStart)
$rowEnd  = $src.IndexOf("kabu_chip,", $target)
"target offset: $($target - $fnStart)"
"row end offset: $($rowEnd - $fnStart)"
# → 最大 offset + テキスト長 + 余裕 200 を max_bytes にする
```

#### Rust デバッグで確認する方法

```rust
let body = extract_fn_body(&src, "fn venue_login_chip(", 2000);
println!("body length: {} chars", body.len());
println!("last 100: {}", &body[body.len().saturating_sub(100)..]);
```

#### 目安値

| 関数規模 | 推奨 max_bytes |
|---|---|
| ~20 行 | 2000 |
| ~50 行 | 4000 |
| ~100 行 | 8000 |
| 不明 | 15000（安全側） |

---

## Tier 1: iced_test ヘッドレステスト

`iced_test::simulator()` はビュー関数を受け取り、実ウィンドウなしでウィジェットツリーを
シミュレートする。

### Message のトレイト境界

`simulator()` は `Message: Clone + Send + 'static` を要求する。
`Arc<T>` を含む Message（例: `EngineMsg::Connected(Arc<EngineConnection>)`）は
`T: Send + Sync` を確認すること。コンパイルエラーになる場合はまず Tier 0 で代替する。

### main.rs のプライベート関数をテストする場合

`main.rs` の `fn venue_login_chip(...)` などプライベート関数は
`#[cfg(test)]` + `use super::*` でアクセスする：

```rust
// src/main.rs の末尾に追加
#[cfg(test)]
mod venue_login_chip_tests {
    use super::*;               // ← main.rs のプライベート fn にアクセス
    use iced_test::simulator;

    #[test]
    fn idle_chip_shows_login_and_fires_manual() {
        let chip = venue_login_chip(
            "立花",
            VenueState::Idle,
            Message::Venue(VenueMsg::RequestTachibanaLogin(Trigger::Manual)),
            Message::Venue(VenueMsg::RequestTachibanaLogout),
            false,
        );
        let mut ui = simulator(chip);
        // find() はウィジェットの表示テキストで一致（leading/trailing space に注意）
        assert!(ui.find("ログイン").is_ok());
        let msgs: Vec<_> = ui.click("ログイン").unwrap().into_messages().collect();
        assert!(matches!(
            msgs[0],
            Message::Venue(VenueMsg::RequestTachibanaLogin(Trigger::Manual))
        ));
    }
}
```

`tests/` 配下のインテグレーションテストからはプライベート関数にアクセスできないため、
その場合は Tier 0（ソースコードピン）を使う。

### iced_test 主要 API

| メソッド | 動作 |
|---|---|
| `simulator(view)` | ヘッドレスシミュレータを作成 |
| `ui.click("テキスト")` | 指定テキストを含むウィジェットをクリック |
| `ui.typewrite("文字列")` | フォーカス中の入力に文字を送る |
| `ui.tap_key(key_code)` | キーボードキーを送る |
| `ui.find("テキスト")` | ウィジェットを検索（`Result` を返す） |
| `ui.into_messages()` | 生成された Message を drain |

**`click()` の挙動**:
- `on_press` がないボタンをクリック → `Ok(空の Outcome)`（エラーにならない）
- テキストが存在しない → `Err`
- `text(format!(" {label}"))` のように leading space があるとマッチしない場合がある

### Tier 1 のその他の制約

- `iced_test::simulator()` はビュー関数の出力を静的に解析する
- 非同期で生成されるウィジェット（`Command` 経由）や canvas 内部はヘッドレスでは見えない → Tier 2 が必要
- `iced_test` はコードベースで**2026-05 時点で実績 0 件** — コンパイルエラーが出たら Tier 0 に切り替える

---

## Tier 2: ビジュアル E2E テスト (xcap + enigo + Claude Vision)

実ウィンドウを立ち上げ、`enigo` でマウス/キーボードを操作し、`xcap` でスクリーンショット
を撮って `Claude Vision API` で視覚的に assert する。

ディスプレイが必要なので `#[ignore]` を付けて明示的に実行する。

### ユーティリティ関数

`tests/gui_helpers.rs` または `tests/common/mod.rs` に置いて共用する：

```rust
use std::time::{Duration, Instant};
use xcap::Window;
use image::DynamicImage;
use base64::Engine as _;

pub fn wait_for_window(title_prefix: &str, timeout: Duration) -> anyhow::Result<Window> {
    let deadline = Instant::now() + timeout;
    loop {
        if let Some(w) = Window::all()?
            .into_iter()
            .find(|w| w.title().map_or(false, |t| t.starts_with(title_prefix)))
        {
            return Ok(w);
        }
        anyhow::ensure!(Instant::now() < deadline,
            "ウィンドウ '{}' が {:?} 以内に現れなかった", title_prefix, timeout);
        std::thread::sleep(Duration::from_millis(200));
    }
}

pub fn capture_and_save(window: &Window, path: &str) -> anyhow::Result<DynamicImage> {
    let img = window.capture_image()?;
    let dyn_img = DynamicImage::ImageRgba8(img);
    dyn_img.save(path)?;
    Ok(dyn_img)
}

pub async fn vision_assert(image: &DynamicImage, question: &str) -> anyhow::Result<bool> {
    let mut buf = std::io::Cursor::new(Vec::new());
    image.write_to(&mut buf, image::ImageFormat::Png)?;
    let b64 = base64::engine::general_purpose::STANDARD.encode(buf.into_inner());

    let api_key = std::env::var("ANTHROPIC_API_KEY")
        .expect("ANTHROPIC_API_KEY が未設定");

    let body = serde_json::json!({
        "model": "claude-sonnet-4-6",
        "max_tokens": 16,
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": { "type": "base64", "media_type": "image/png", "data": b64 }
                },
                {
                    "type": "text",
                    "text": format!("Answer only YES or NO, nothing else. {}", question)
                }
            ]
        }]
    });

    let resp: serde_json::Value = reqwest::Client::new()
        .post("https://api.anthropic.com/v1/messages")
        .header("x-api-key", &api_key)
        .header("anthropic-version", "2023-06-01")
        .json(&body)
        .send().await?
        .json().await?;

    let answer = resp["content"][0]["text"].as_str().unwrap_or("NO");
    Ok(answer.trim().to_uppercase().starts_with("YES"))
}
```

### テスト例

```rust
// tests/gui_e2e.rs
mod common;
use common::{wait_for_window, capture_and_save, vision_assert};
use enigo::{Enigo, Button, Direction, Coordinate, Mouse as _, Keyboard as _};
use std::time::Duration;

#[tokio::test]
#[ignore = "requires display + ANTHROPIC_API_KEY"]
async fn chart_renders_after_ticker_click() -> anyhow::Result<()> {
    // uv run 経由で起動しないと Python engine が transport error ループになる
    let mut child = tokio::process::Command::new("uv")
        .args(["run", "cargo", "run", "--release", "--", "--mode", "live"])
        .spawn()?;

    let window = wait_for_window("Flowsurface", Duration::from_secs(15))?;
    tokio::time::sleep(Duration::from_secs(2)).await;

    let mut enigo = Enigo::new(&Default::default())?;
    enigo.move_mouse(window.x() + 150, window.y() + 250, Coordinate::Abs)?;
    enigo.button(Button::Left, Direction::Click)?;
    tokio::time::sleep(Duration::from_millis(1000)).await;

    let img = capture_and_save(&window, "tests/screenshots/after_ticker_click.png")?;
    assert!(
        vision_assert(&img, "Does the window show a financial chart with price data?").await?,
        "ティッカークリック後にチャートが表示されるはず"
    );

    child.kill().await?;
    Ok(())
}
```

---

## Vision プロンプトパターン

Vision に送る質問は **YES/NO で答えられる具体的な検証文** にする。

| 検証内容 | 良い質問例 |
|---|---|
| ボタンの存在 | "Is there a button labeled 'Login' visible?" |
| ローディング状態 | "Is a loading spinner or progress bar visible?" |
| エラー表示 | "Is there an error message or red-colored text visible?" |
| チャート種別 | "Does the chart show candlestick bars (not just a single line)?" |
| 空状態 | "Does the main content area appear empty with no chart data?" |
| 接続インジケータ | "Is there a green dot or green indicator suggesting an active connection?" |

複数箇所の確認や数値取得が必要なときは `max_tokens` を増やし「Describe X」形式にする。

---

## 実行コマンド

```powershell
# Tier 0 / Tier 1: ヘッドレス（通常の cargo test に含まれる）
cargo test

# Tier 2: ビジュアル E2E（ディスプレイ + API キーが必要）
$env:ANTHROPIC_API_KEY = "sk-ant-..."
cargo test -- --ignored

# 特定テストのみ
cargo test chart_renders -- --ignored --nocapture
```

---

## CI 設定

Tier 2 は `windows-latest` ランナーで仮想ディスプレイなしで動く（Windows ネイティブ）。
Linux では `Xvfb` が必要。

```yaml
# .github/workflows/gui-e2e.yml
name: GUI E2E Tests
on:
  schedule:
    - cron: '0 2 * * *'
  workflow_dispatch:

jobs:
  visual-e2e:
    runs-on: windows-latest
    env:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - name: Build release binary
        run: cargo build --release
      - name: Run visual e2e tests
        run: cargo test -- --ignored
      - name: Upload screenshots
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: e2e-screenshots
          path: tests/screenshots/
```

---

## e-station 固有の注意点

- **ウィンドウタイトル**: `"Flowsurface"` または `"Flowsurface [<layout-name>]"`。
  `wait_for_window("Flowsurface", ...)` で prefix 一致。
- **起動時間**: iced + wgpu の初期化は 2〜4 秒。最初の `sleep` は 2 秒以上。
- **モード引数**: ライブ vs リプレイで UI が変わる。`--mode live` / `--mode replay` を明示する。
- **engine セッション**: ビジュアル E2E でライブ GUI を起動すると
  `%APPDATA%\flowsurface\engine-session.json` が生成される。
  `child.kill()` しないと次の attach モードテストに干渉する。

### アプリ起動コマンド（Tier 2 実機確認用）

**必ず `uv run` 経由で起動すること。** bare `Start-Process flowsurface.exe` や
`cargo run` 単体では Python engine が `transport error` ループに陥る。

```powershell
# .env の dev credentials を読み込んでから起動
cd D:\Documents\e-station
Get-Content .env | Where-Object { $_ -match '^\s*[^#]' -and $_ -match '=' } | ForEach-Object {
    $parts = $_ -split '=', 2
    [System.Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), 'Process')
}
$proc = Start-Process -FilePath "uv" -ArgumentList "run", "cargo", "run", "--release", "--", "--mode", "live" `
    -WorkingDirectory "D:\Documents\e-station" -PassThru -WindowStyle Normal
Write-Host "PID: $($proc.Id)"
```

起動後は release build のログファイルで状態確認:

```powershell
$log = "$env:APPDATA\flowsurface\flowsurface-current.log"
# ログが出るまで最大40秒待つ
$deadline = (Get-Date).AddSeconds(40)
while (-not (Test-Path $log) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 500 }
# ログイン・接続状態のみ抽出
Get-Content $log | Select-String "VenueReady|market_closed|depth_stream|connected|disconnected|VenueLogin"
```

ログで確認するキーワード:
| キーワード | 意味 |
|---|---|
| `VenueReady` | ログイン成功（自動ログインが効いた） |
| `market_closed` | 市場閉場で WS 切断 → depth データは来ない |
| `a stream connected to ... WS` | depth/trades の WS 接続確立 |
| `depth_stream subscribe error` | depth 購読失敗 |
| `VenueLoginError` | ログイン失敗 |

---

## Tier 2 実行前プリフライトチェック

**視覚的な動作確認（Tier 2）を行う前に、必ずこの確認を済ませること。**
特に「Waiting for data...」「データが来ない」系のバグは、コードのバグと
環境条件が重なって見えるため、条件を先に分離する。

### チェックリスト

```
□ 1. バグ発現の venue を特定する
      → python/engine/exchanges/<venue>.py の venue_caps() で
        client_aggr_depth が True/False を確認

□ 2. その venue のサービスが起動しているか確認する
      → KabuStation: Get-Process | Where-Object { $_.Name -match "kabu" }
      → 立花: .env に DEV_TACHIBANA_USER_ID があれば自動ログイン可

□ 3. 市場が開いているか確認する（depth ストリームには市場時間が必要）
      → 東証（TSE）: 9:00-11:30 / 12:30-15:30 JST
      → 市場外ではログに "market_closed" が出て depth データは来ない

□ 4. saved-state.json に対象ペイン構成が入っているか確認する
      $f = "$env:APPDATA\flowsurface\saved-state.json"
      Get-Content $f -Raw | Select-String 'Ladder|depth_aggr'
```

### 「Waiting for data...」の原因切り分け

Ladder パネルがこのメッセージを表示する原因は複数ある。視覚確認で
見えても、**それがバグなのか環境問題なのかは切り分けが必要**。

| 原因 | 見分け方 |
|---|---|
| **コードの routing バグ**（Issue #38 等） | Tier 0 静的解析でコードの不一致を確認 |
| **市場閉場**（正常動作） | ログに `market_closed` が出ている |
| **ログイン失敗・未接続** | ログに `VenueLoginError` / `VenueReady` がない |
| **依存サービス未起動** | ログに `transport error` ループがある |

**Tier 0 で決定論的にバグが確認できた場合**（`A == B` が型レベルで常に false
になるなど）は、Tier 2 による視覚確認は必須ではない。環境条件が揃わない
状況で強行しても「再現できない＝バグがない」と誤解されるリスクがある。

### 視覚再現不可の場合の記録パターン

条件が揃わず Tier 2 で再現できなかった場合は、以下を明示して報告する：

```
## 動作確認結果（視覚再現不可）

**バグの存在**: Tier 0 静的解析で確定（決定論的）
**視覚再現**: 不可
**再現不可の理由**: <以下から選ぶ>
  - 市場閉場（JSE 15:30 JST 閉場 / 確認時刻 XX:XX JST）
  - 依存サービス未起動（KabuStation が起動していない）
  - 対象 venue の VenueCaps がバグ条件を満たさない
    （例: 立花は client_aggr_depth=True のため Issue #38 は発現しない）

**視覚再現に必要な条件**:
  - <具体的な条件を列挙>
```

---

## ログデバッグ手順

静的解析だけでは原因が特定できない場合（ランタイム値・タイミング依存の問題）に使う。

### 手順

1. **仮説を 2〜8 つ作成する**
   現状のコードを静的に読み、「ここが怪しい」という候補を列挙する。
   パターン例（バグの種類に応じて読み替える）:
   - 「フラグ X が想定外の値になっている」
   - 「条件分岐 Y が期待と逆の方向に進んでいる」
   - 「イベント Z がルーティング先に届いていない」
   - 「状態 W が初期化前に参照されている」

2. **各仮説を検証するログを追加する**
   `log::debug!` を使い、各仮説の検証点にログを置く。
   ログには仮説番号とコンテキスト値を含める。

   ```rust
   // 仮説 A: フラグが想定外の値になっている
   log::debug!("[DEBUG-A] flag_name({context}) = {value}");

   // 仮説 B: ルーティング条件が false を返している
   log::debug!("[DEBUG-B] routing_check: expected={expected:?}, actual={actual:?}");
   ```

3. **ログを確認する**

   `RUST_LOG` はクレート名でスコープを絞るとノイズが少ない（`debug` 全体だと
   依存クレートのログが大量に出る）。

   ```powershell
   # クレート名でスコープを絞って起動
   $env:RUST_LOG = "flowsurface=debug"
   cargo run --release

   # ログをファイルに保存して [DEBUG- 行だけ抽出
   $env:RUST_LOG = "flowsurface=debug"
   cargo run --release 2>&1 | Tee-Object -FilePath debug.log
   Select-String "\[DEBUG-" debug.log
   ```

4. **ログ結果から原因を特定する**
   各仮説に対応するログが出力されているか、値が期待通りかを確認する。
   原因が特定できたら、その仮説に対応する修正方針を決める。

5. **追加したログをすべて削除する**
   原因特定後は `[DEBUG-` プレフィックスで検索してすべて除去する。

   ```powershell
   # 残存チェック（0 件になるまで削除する）
   # ※ Select-String は ** glob を展開しないため Get-ChildItem 経由で検索する
   Get-ChildItem -Recurse -Filter "*.rs" -Path src | Select-String "\[DEBUG-"
   ```

   `cargo check` でコンパイルエラーがないことを確認してから commit する。

### ログ追加のルール

- プレフィックスは `[DEBUG-A]` のように仮説番号を入れる（後で一括削除しやすいため）
- 本番ログ（`log::warn!` / `log::error!` で既存のもの）は触らない
- ログの追加・削除は小さなコミットにせず、原因特定後に一括で削除する
- ログが残ったまま PR を出さない

### ⚠️ PowerShell 出力キャプチャの落とし穴

`| Select-Object -First N` はパイプライン上流のプロセスを N 行取得後に **強制終了**する。
これにより：

- 上流プロセスの **exit code が失われ**、PowerShell パイプライン全体は exit code **0** を返す（偽の成功）
- エラーが出力の後半にあると **見えないまま診断が終わる**
- Python スクリプト（`replay_session.py` など）を確認するときに特にハマりやすい

**正しいキャプチャ方法**:

```powershell
# NG: 80行でプロセスを kill → エラーが後半にあると見えない
uv run python -m engine.replay_session run ... 2>&1 | Select-Object -First 80

# OK: 全出力をキャプチャ（長くなっても全部見える）
uv run python -m engine.replay_session run ... 2>&1 | Out-String -Width 300

# OK: ファイルにも残したい場合
uv run python -m engine.replay_session run ... 2>&1 | Tee-Object -FilePath out.txt
```

exit code を確認したい場合は `$LASTEXITCODE` を使う（パイプ後でも有効）。
`Out-String` 経由では `$LASTEXITCODE` が保持されるが、`Select-Object` 後は不定になる。

---

## 新テスト追加の手順

1. **コードパス調査を先に行う** — テストを書く前に「診断フロー」で原因を特定する
2. **対称性ギャップを確認する** — 類似機能（複数 venue、複数パネル等）で
   片側だけテストが存在しないか確認する。`git show <commit> --stat` で直近の
   変更ファイルを確認すると漏れを発見しやすい
3. **構造的不変条件**: `tests/` に `*_pin.rs` を作り Tier 0 で書く
4. **ウィジェット操作ロジック**: `src/` 内の `#[cfg(test)]` ブロックに Tier 1 を追加
   - `main.rs` のプライベート関数は `use super::*` でアクセス
5. **ビジュアル確認**: `tests/gui_e2e.rs` に `#[ignore]` 付きで Tier 2 を追加
6. `max_bytes` は PowerShell で事前計測してから決める（不足は silent fail の原因）
7. Vision 質問は「YES/NO で答えられるか」を確認してから書く

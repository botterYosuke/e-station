---
name: iced-gui-testing
description: >
  iced GUI アプリの動作確認・E2E テストを行うスキル。
  「動作確認して」「確認して」「テストして」「〜ができなくなった」「バグか確認」
  「実際に動かして」「画面を見て」と言われたら、必ずアプリを起動して
  スクリーンショットを撮り Vision API で視覚検証する。
  コードを読んで「実装は正しそうです」と報告するのは「動作確認」ではない — 絶対にしない。
  「iced のテスト」「GUI automation」「スクリーンショット」「ウィンドウ操作」
  「xcap」「enigo」「iced_test」「ビジュアルリグレッション」「配線確認」
  「ピンテスト」「動作確認」「できなくなった」「バグか確認」と言ったら必ず起動する。
  既存の Python/pytest e2e（`e2e-testing` スキル）を補完する Rust GUI 専用スキル。
---

# iced GUI テスト — 動作確認は実機起動が前提

e-station (Flowsurface) は Rust + iced 0.14 GUI。Python 側 E2E は `e2e-testing` スキル参照。

---

## 依頼の種類を最初に判断する

| ユーザーの言葉 | 目的 | やること |
|---|---|---|
| 「動作確認して」「確認して」「テストして」「バグか確認」「できなくなった」 | **画面で見て確認したい** | **→ Tier 2（実機起動）へ。コードを読んで終わりにしない** |
| 「ピンテストを追加して」「回帰テストを書いて」「配線確認テスト」 | コード構造を固定したい | → Tier 0 へ |
| 「Message ルーティングをヘッドレスでテスト」 | ロジックをヘッドレスで確認したい | → Tier 1 へ |

### 動作確認でやってはいけないこと

コードを grep / Read して「実装を確認しました」「コードは正しいです」と報告して終わること。
これは **コードレビューであり、動作確認ではない**。

「動作確認完了」と言えるのは、アプリを起動してスクリーンショットを撮り、
Vision API または目視で実際の表示を確認したときだけ。

---

## Tier 2: 実機起動 + 視覚確認（動作確認の標準手順）

「動作確認」依頼のデフォルト。まずここから始める。

### 手順 1: アプリを起動する

```powershell
# .env を読み込む（自動ログインに必要）
Get-Content "D:\Documents\e-station\.env" |
    Where-Object { $_ -match '^\s*[^#]' -and $_ -match '=' } |
    ForEach-Object {
        $parts = $_ -split '=', 2
        [System.Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), 'Process')
    }

# 必ず uv run 経由で起動する（bare cargo run / flowsurface.exe では Python engine が死ぬ）
$proc = Start-Process -FilePath "uv" `
    -ArgumentList "run", "cargo", "run", "--release", "--", "--mode", "live" `
    -WorkingDirectory "D:\Documents\e-station" `
    -PassThru -WindowStyle Normal
Write-Host "起動 PID: $($proc.Id)"
```

### 手順 2: 起動確認（最大 40 秒待つ）

```powershell
$log = "$env:APPDATA\flowsurface\flowsurface-current.log"
$deadline = (Get-Date).AddSeconds(40)
while (-not (Test-Path $log) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 500 }
Get-Content $log | Select-String "VenueReady|market_closed|connected|VenueLogin|transport error"
```

| ログキーワード | 意味 |
|---|---|
| `VenueReady` | ログイン成功。自動ログインが効いた |
| `market_closed` | 市場閉場（正常）。UI の確認は可能 |
| `connected` | WebSocket 接続確立 |
| `VenueLoginError` | ログイン失敗 |
| `transport error` ループ | `uv run` を使っていない → 再起動 |

### 手順 3: スクリーンショット取得 + Vision 検証

アプリが起動したら、`tests/gui_verify.rs` を作成して即実行する。
（確認後は `#[ignore]` を維持するか、CI 用として残す）

```rust
// tests/gui_verify.rs
// cargo test verify -- --ignored --nocapture で実行

use image::DynamicImage;
use std::time::{Duration, Instant};
use xcap::Window;
use base64::Engine as _;

fn wait_for_window(prefix: &str, timeout: Duration) -> anyhow::Result<Window> {
    let deadline = Instant::now() + timeout;
    loop {
        if let Some(w) = Window::all()?
            .into_iter()
            .find(|w| w.title().map_or(false, |t| t.starts_with(prefix)))
        {
            return Ok(w);
        }
        anyhow::ensure!(Instant::now() < deadline,
            "ウィンドウ '{}' が {:?} 内に現れなかった", prefix, timeout);
        std::thread::sleep(Duration::from_millis(300));
    }
}

async fn vision_yes_no(img: &DynamicImage, question: &str) -> anyhow::Result<bool> {
    let mut buf = std::io::Cursor::new(Vec::new());
    img.write_to(&mut buf, image::ImageFormat::Png)?;
    let b64 = base64::engine::general_purpose::STANDARD.encode(buf.into_inner());
    let api_key = std::env::var("ANTHROPIC_API_KEY").expect("ANTHROPIC_API_KEY not set");
    let body = serde_json::json!({
        "model": "claude-sonnet-4-6",
        "max_tokens": 16,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
            {"type": "text", "text": format!("Answer only YES or NO. {question}")}
        ]}]
    });
    let resp: serde_json::Value = reqwest::Client::new()
        .post("https://api.anthropic.com/v1/messages")
        .header("x-api-key", &api_key)
        .header("anthropic-version", "2023-06-01")
        .json(&body).send().await?.json().await?;
    Ok(resp["content"][0]["text"].as_str().unwrap_or("NO").trim().starts_with("YES"))
}

#[tokio::test]
#[ignore = "requires display + running app + ANTHROPIC_API_KEY"]
async fn verify_empty_state_message() -> anyhow::Result<()> {
    // アプリは手順 1 で起動済みの前提
    let window = wait_for_window("Flowsurface", Duration::from_secs(10))?;
    std::thread::sleep(Duration::from_secs(2)); // レンダリング安定待ち

    let img = DynamicImage::ImageRgba8(window.capture_image()?);
    img.save("tests/screenshots/verify_empty_state.png")?;

    // ← ここに確認したい Visual assertion を書く
    let ok = vision_yes_no(&img,
        "Is the text '銘柄を選択してください' visible in the panel?").await?;
    assert!(ok, "未選択時に正しいメッセージが表示されるはず — スクショ: tests/screenshots/verify_empty_state.png");

    Ok(())
}
```

```powershell
# 実行（アプリ起動後に）
$env:ANTHROPIC_API_KEY = "sk-ant-..."
cargo test verify_empty_state_message -- --ignored --nocapture
```

### 手順 4: スクリーンショットを確認して報告する

`tests/screenshots/` に保存された PNG を Read ツールで読み込み、
何が映っているか・期待値と一致しているかを報告する。

---

## Tier 2 を省略できる唯一の条件

以下の **3つ全て** が成立する場合のみ省略可能。そうでない限り省略禁止。

1. アプリの起動が物理的に不可能（バイナリが存在しない、依存サービスが未インストール等）
2. かつ、コードの欠陥が型レベルで決定論的に確認できる（実行しなくても常に false になる等）
3. かつ、表示されるメッセージがランタイム状態に依存しない

省略した場合は必ず以下の形式で報告する:

```
## 動作確認結果（視覚確認省略）

**省略理由**: [3条件のどれに該当するか具体的に]
**静的解析で確認した内容**: [確認した不変条件]
**視覚確認するには**: [必要な条件を列挙]
```

「市場が閉場している」「立花にログインしていない」は省略理由にならない。
市場外でも UI の状態（ログイン必要メッセージ、銘柄未選択メッセージ等）は確認できる。

---

## Tier 2 実行前プリフライトチェック

起動の前に環境を確認する。

```
□ 1. uv が使える: uv --version
□ 2. cargo build が通る: cargo check --workspace
□ 3. .env に DEV_TACHIBANA_USER_ID がある（立花自動ログイン用）
□ 4. ANTHROPIC_API_KEY が設定済み（Vision 検証用）
□ 5. Cargo.toml に xcap / reqwest / base64 / image が dev-dependencies にある
```

Cargo.toml に不足があれば追加:

```toml
[dev-dependencies]
xcap     = "0.0.14"
image    = "0.25"
base64   = { version = "0.22", features = ["alloc"] }
reqwest  = { version = "0.12", features = ["json"] }
serde_json = "1"
tokio    = { version = "1", features = ["rt-multi-thread", "macros", "time", "process"] }
anyhow   = "1"
```

---

## Vision プロンプトパターン

YES/NO で答えられる具体的な質問を書く。

| 検証内容 | 良い質問例 |
|---|---|
| 銘柄未選択メッセージ | "Is the text '銘柄を選択してください' visible?" |
| ログイン必要メッセージ | "Is the text '立花へのログインが必要です' or similar login-required message visible?" |
| チャート表示 | "Does the window show a financial chart with candlestick bars?" |
| 空状態 | "Is the main panel area empty with no data displayed?" |
| ボタンの有効/無効 | "Is there a disabled or grayed-out '注文' button visible?" |
| エラーメッセージ | "Is there an error message or warning text visible in the window?" |

複数箇所の確認: `max_tokens` を 100 以上にして「Describe what you see in X」形式にする。

---

## Tier 0: ソースコードピンテスト（コード構造の固定）

**動作確認ではなく「コードが変更されていないことを保証したい」場合に使う。**
Tier 0 単独で「動作確認完了」とは言えない。

このコードベースで最も多用されているパターン。`src/` を `read_to_string` して
不変条件を文字列アサートする。iced_test も display も不要で最速に走る。

**実績**: `tests/tachibana_login_update.rs`、`tests/status_bar_login_chip_pin.rs` など多数。

### 基本パターン

```rust
fn read_src(relative_path: &str) -> String {
    let base = env!("CARGO_MANIFEST_DIR");
    std::fs::read_to_string(format!("{base}/{relative_path}"))
        .unwrap_or_else(|_| panic!("{relative_path} not found"))
}

fn scan_brace_body(src: &str, needle: &str) -> String {
    let start = src.find(needle).unwrap_or_else(|| panic!("not found: {needle}"));
    let rest = &src[start..];
    if let Some(open) = rest.find('{') {
        let bytes = rest.as_bytes();
        let mut depth: i32 = 0;
        let mut i = open;
        while i < bytes.len() {
            match bytes[i] {
                b'{' => depth += 1,
                b'}' => { depth -= 1; if depth == 0 { return rest[..=i].to_string(); } }
                _ => {}
            }
            i += 1;
        }
    }
    rest.to_string()
}

#[test]
fn handler_calls_try_claim() {
    let src = read_src("src/handlers/venue.rs");
    let body = scan_brace_body(&src, "VenueMsg::RequestTachibanaLogin(trigger)");
    assert!(body.contains("try_claim_login_in_flight()"));
}
```

### max_bytes の計測（extract_fn_body を使う場合）

```powershell
$src = Get-Content "src/main.rs" -Raw
$fnStart = $src.IndexOf("fn target_function(")
$target  = $src.IndexOf("target_string", $fnStart)
"offset: $($target - $fnStart)"
# → offset + テキスト長 + 余裕 200 を max_bytes に
```

| 関数規模 | 推奨 max_bytes |
|---|---|
| ~20 行 | 2000 |
| ~50 行 | 4000 |
| ~100 行 | 8000 |
| 不明 | 15000（安全側） |

### 対称性ギャップを見落とさない

「立花のピンはあるが kabu のピンがない」パターンを積極的に埋める。
`git show <commit> --stat` で直近の変更ファイルを確認すると漏れを発見しやすい。

---

## Tier 1: iced_test ヘッドレステスト

`iced_test::simulator()` はビュー関数を受け取り、実ウィンドウなしでウィジェットツリーを
シミュレートする。

**注意**: コードベースで 2026-05 時点で実績 0 件。コンパイルエラーが出たら Tier 0 で代替。

```toml
# Cargo.toml
[dev-dependencies]
iced_test = "0.14"  # iced バージョンと必ず一致
```

```rust
// src/main.rs の末尾に追加
#[cfg(test)]
mod venue_chip_tests {
    use super::*;
    use iced_test::simulator;

    #[test]
    fn idle_chip_shows_login() {
        let chip = venue_login_chip(
            "立花", VenueState::Idle,
            Message::Venue(VenueMsg::RequestTachibanaLogin(Trigger::Manual)),
            Message::Venue(VenueMsg::RequestTachibanaLogout),
            false,
        );
        let mut ui = simulator(chip);
        assert!(ui.find("ログイン").is_ok());
    }
}
```

`tests/` からプライベート関数にはアクセスできない → その場合は Tier 0 を使う。

---

## ログデバッグ手順（ランタイム値の確認）

静的解析で原因が特定できないとき（タイミング依存・条件分岐の値確認）に使う。

1. **仮説を 2〜8 つ作る** — 「フラグXが逆」「イベントYが届いていない」等
2. **`log::debug!("[DEBUG-A] ...")`** で各仮説に対応するログを追加
3. **実行して確認**:

```powershell
$env:RUST_LOG = "flowsurface=debug"
cargo run --release 2>&1 | Tee-Object -FilePath debug.log
Select-String "\[DEBUG-" debug.log
```

4. **原因確定後、`[DEBUG-` プレフィックスで全ログを削除する**

```powershell
# 残存チェック（0件になること）
Get-ChildItem -Recurse -Filter "*.rs" -Path src | Select-String "\[DEBUG-"
```

### ⚠️ PowerShell 出力キャプチャの落とし穴

```powershell
# NG: 80行でプロセスを強制終了 → エラーが後半にあると見えない
cmd 2>&1 | Select-Object -First 80

# OK: 全出力を取得
cmd 2>&1 | Out-String -Width 300
```

---

## 新テスト追加の手順

1. **動作確認が目的なら Tier 2 から始める** — コードを読む前にアプリを起動する
2. **コード構造の固定が目的なら Tier 0** — `tests/*_pin.rs` に書く
3. **対称性ギャップを確認** — `git show <commit> --stat` で変更ファイルを確認
4. **Vision 質問は YES/NO で答えられるか確認してから書く**
5. **`max_bytes` は PowerShell で事前計測** — 不足は silent fail の原因

---

## CI 設定（Tier 2 の自動化）

```yaml
# .github/workflows/gui-e2e.yml
jobs:
  visual-e2e:
    runs-on: windows-latest
    env:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - run: cargo build --release
      - run: cargo test -- --ignored --nocapture
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: e2e-screenshots
          path: tests/screenshots/
```

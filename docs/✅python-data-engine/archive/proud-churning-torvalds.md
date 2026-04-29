# 修正計画: `--data-engine-url` 自動デフォルト + spawn フォールバック

## Status

**実装完了**（2026-04-29）

- `engine-client/src/connection.rs`: `EngineConnection::probe()` + `connect_ws_with_tcp_timeout()` 追加
- `engine-client/src/process.rs`: `ProcessManager::start_or_attach()` / `try_attach_or_spawn()` / `spawn_count()` 追加
- `src/main.rs`: `start(port)` → `start_or_attach(port)` 差し替え（L443）
- `engine-client/tests/start_or_attach.rs`: 統合テスト 5 件（全 PASS）
- `docs/✅python-data-engine/spec.md` §3.1 / §4.1.1 更新済み
- `CLAUDE.md` 運用追記済み

## Context

現状:
- `--data-engine-url` 指定なし → 常に Python を spawn（managed モード）
- `--data-engine-url` 指定あり → spawn せず外部接続のみ。失敗したらエラー終了

要求: **Python エンジンが既に起動していれば再利用、いなければ spawn する。**

## 方針

新規 IPC 機構や発見メカニズム（ロックファイル等）は導入しない。
既存の external 接続経路と spawn 経路を、起動時に **接続試行 → 失敗時 spawn フォールバック** の順で組み合わせるだけ。

```
起動
  ├─ --data-engine-url 指定あり → 既存動作（external 固定。失敗はエラー終了のまま）
  │
  └─ --data-engine-url 指定なし
       ├─ env FLOWSURFACE_ENGINE_TOKEN 未設定 → external 試行を skip して直接 spawn
       │   （空 token で 19876 を叩いても HMAC で必ず失敗するため、無駄な遅延・warn を避ける）
       │
       ├─ env FLOWSURFACE_ENGINE_TOKEN 設定あり
       │   └─ ws://127.0.0.1:19876/ へ接続試行（既存 external 経路を流用）
       │       ├─ 成功（ハンドシェイクまで通る）→ そのまま使う
       │       └─ 失敗（接続不可 / token 不一致 / SCHEMA_MAJOR 不一致）→ 下へ
       └─ Python を spawn（既存 managed 経路、ポートは呼び出し側が任意空きポートを選択）
```

## Token の扱い

現行ルールをそのまま使う。新規仕様は追加しない。

- spawn 経路: Rust がランダム token を生成し stdin で Python に渡す（現行どおり）
- 既存接続経路: 環境変数 `FLOWSURFACE_ENGINE_TOKEN` を使う（現行どおり、CLAUDE.md 記載）
- 手動起動側で `uv run python -m engine --port 19876 --token $FLOWSURFACE_ENGINE_TOKEN` を合わせる運用
- **env 未設定時は外部試行を行わず直接 spawn**（[src/main.rs:234](../../../src/main.rs#L234) の `unwrap_or_default()` が空文字 token を返す現行挙動を踏まえたショートサーキット）

## デフォルトポート

`127.0.0.1:19876` を **external プローブ専用の固定値**として使う。CLAUDE.md で
既に flowsurface 用に運用されているポート。

spawn 側のポートは現行どおり呼び出し側 (`src/main.rs`) が決定する任意空きポートとし、
19876 を奪い合わない（19876 が他プロセスに占有されている場合に spawn の listen が失敗する事故を避けるため）。

衝突時の挙動:
- 別アプリが 19876 を使っていても、ハンドシェイク（`SCHEMA_MAJOR` 一致 + token HMAC 検証）で必ず弾かれる
- 弾かれたら spawn フォールバックに進む。spawn 側は別ポートを使うので 19876 占有とは独立に成功できる
- 診断のため、失敗理由（接続拒否 / handshake 失敗 / token 不一致 / SCHEMA_MAJOR 不一致）を `log::info!` で必ず残す

## タイムアウト方針

層別に区別して合計上限を見積もる:

| 層 | 値 | 出典 / 必要な変更 |
|---|---|---|
| TCP connect 試行 | 2s | **`connection.rs` の改修が必要**（後述） |
| WS handshake | 既存 `HANDSHAKE_TIMEOUT` を流用 | [engine-client/src/connection.rs:69](../../../engine-client/src/connection.rs#L69) |
| 合計（external 試行の上限） | `2s + HANDSHAKE_TIMEOUT` | これを超えたら必ず spawn フォールバック |

**注意**: 現行 [connection.rs:69](../../../engine-client/src/connection.rs#L69) は `connect_plain_ws(url)` 全体を
`HANDSHAKE_TIMEOUT` で包むだけで、`TcpStream::connect` 自体には個別タイムアウトが無い
（[connection.rs:155 `connect_plain_ws`](../../../engine-client/src/connection.rs#L155)）。
そのため `process.rs` / `main.rs` だけ触っても 2 秒で external プローブを打ち切ることはできず、
最悪 `HANDSHAKE_TIMEOUT`（≒10 秒）まで張り付く。**`connection.rs` 側で TCP connect を
個別に `tokio::time::timeout(Duration::from_secs(2), …)` で包む新規 connect 関数を
export する**ことを成果物に含めること（次節 §変更する成果物 → connection.rs を参照）。

## 変更する成果物

### `engine-client/src/connection.rs`（新規追加・必須）

旧計画は触れていなかったが、**TCP connect の個別タイムアウトを実装するために必須**。

- `connect_plain_ws` 内部の `TcpStream::connect` を
  `tokio::time::timeout(Duration::from_secs(2), TcpStream::connect(addr))` で包む
  新規 connect 関数（例: `EngineConnection::probe`）を public に export
- 既存の `connect_with_mode` を改変して全 connect 経路に 2s を強制すると
  spawn 後の retry ループ（process.rs:402 の指数バックオフ）と干渉するため、
  **external プローブ専用の関数として分離**することを推奨
- TCP timeout 失敗時は `EngineClientError::ConnectionRefused` 相当の variant
  または新規 `Timeout` variant を返し、`process.rs` 側がそれを fallback シグナルとして扱う

### `engine-client/src/process.rs`

attach / spawn 判定を `ProcessManager` 内部に閉じ込める（責務分離の理由は本節末尾）。

- 新規メソッド `start_or_attach()` を追加。`start(port)` は内部実装として残す
- `start_or_attach` の中で:
  1. `FLOWSURFACE_ENGINE_TOKEN` env を読む。未設定なら 3. へ直行
  2. `EngineConnection::probe("ws://127.0.0.1:19876/", &token)` を呼ぶ
     （TCP 2s + 既存 handshake タイムアウト）
     - 成功 → `apply_after_handshake` を呼んで attach 完了
     - 失敗 → 失敗理由を `log::info!(target: "engine_client::process", "external probe failed: {reason}")` で記録し 3. へ
  3. 任意空きポートを内部で選択（または `start(port)` を呼び出して既存 spawn フロー）
- **観測点**: spawn 経路に入ったときは `log::info!(target: "engine_client::process", "spawning python engine on port {port}")` を必ず出す。
  さらにテスト用の seam として `ProcessManager` に `pub fn spawn_count(&self) -> usize`
  または `AtomicUsize` カウンタを追加し、attach か spawn かをログ依存せずに
  判定できるようにする（理由は §テスト方針 を参照）

### `src/main.rs`

`main.rs` 側には起動ポリシーを書かない（spec.md §5.2 の「`src/` 側は薄い facade」原則を維持）。

- `ProcessManager::start(port)` 直接呼び出しを `start_or_attach()` に差し替えるだけ
- env チェック・19876 プローブ・spawn 判定はすべて `process.rs` 側の責務
- 再起動ループ（[src/main.rs:433 付近](../../../src/main.rs#L433)）は再起動時も
  `start_or_attach()` を呼ぶ。**初回起動と再起動でロジックが分散しないようにする**
  （再起動時に既存の手動エンジンに再 attach させたいか、それとも常に spawn
   させたいかは実装着手時に決定。現時点の推奨は「再起動時は常に新規 spawn」
  ＝ `start(port)` を直接呼ぶ）

### Python 側

変更なし。

### `docs/✅python-data-engine/spec.md`（旧計画の「修正不要」は誤り）

採用時に以下を更新する。これを怠ると実装と仕様が乖離する。

- **§3.1 起動フロー**（[spec.md:37](../spec.md#L37)）: ロックファイル検出フローを削除し、
  「env `FLOWSURFACE_ENGINE_TOKEN` チェック → 19876 プローブ → 失敗時 spawn」フローに置き換える
- **§4.1.1 ローカル IPC のアクセス制御 → ポート秘匿**（[spec.md:76 周辺](../spec.md#L76)）:
  「ロックファイル経由の検出メカニズムを除く」例外条項を撤回し、固定ポート
  `19876` プローブを許容する根拠（HMAC + SCHEMA_MAJOR で必ず弾けること）を記載
- **§3.1 末尾の `engine-discovery.md` リンク**: archive へのリンクに張り替えるか削除

### ドキュメント

`CLAUDE.md` の「外部エンジンに接続する際のトークン認証」節に 1 段落追記:

> `--data-engine-url` を指定しなくても、`FLOWSURFACE_ENGINE_TOKEN` を設定して
> `ws://127.0.0.1:19876/` で待ち受け中のエンジンがあれば自動接続する。
> env 未設定の場合は external 試行を skip して直接 spawn する（無駄な遅延と
> warn ログを避けるため）。env 設定ありで接続できない場合のみ Rust が新規 spawn する。
> 手動起動エンジンを再利用したい場合は、env var `FLOWSURFACE_ENGINE_TOKEN` を
> Python の `--token` と一致させること。

`docs/✅python-data-engine/engine-discovery.md` は本計画と同時にアーカイブへ
退避済み（`docs/✅python-data-engine/archive/engine-discovery.md` に存在。
ロックファイル案は採用しない）。

## テスト方針

最小構成 + failure path:

1. **Rust 統合テスト** (`engine-client/tests/`):
   - **正常 reuse**: 19876 で `tokio-tungstenite` モックを起動した状態で managed モード起動 → spawn されず接続される
   - **正常 fallback**: 19876 が空いている状態で managed モード起動 → 通常 spawn にフォールバックする
   - **token 不一致 fallback**: 19876 にモックを立てて HMAC を意図的に失敗させる → fallback して spawn する
   - **SCHEMA_MAJOR 不一致 fallback**: モックが古い `SCHEMA_MAJOR` を返す → fallback して spawn する
   - **env 未設定ショートサーキット**: `FLOWSURFACE_ENGINE_TOKEN` 未設定 → 19876 への接続試行が一切発生しない（モック側に到達しない）
   - **観測点**: spawn が走ったかは (a) `ProcessManager::spawn_count()` の値で判定するのが第一推奨。
     ログだけだと現状の `PythonProcess::spawn_with` には spawn 進入を示す `log::info!` が
     存在せず（[engine-client/src/process.rs:188](../../../engine-client/src/process.rs#L188)）、
     計画書 §変更する成果物 → process.rs の「観測点」追加とセットでないと安定検証できない。
     (b) 補助的に `log::info!(target: "engine_client::process", "spawning python engine ...")` を
     `tracing-test` でキャプチャしてもよい

2. **E2E**:
   - Python を `--port 19876` で手動起動 + `FLOWSURFACE_ENGINE_TOKEN` 設定 → `cargo run -- --mode live` → spawn ログが出ないこと
   - Python を起動しない + env 設定あり → `cargo run -- --mode live` → 既存の spawn ログが出ること
   - env 未設定 → `cargo run -- --mode live` → external 試行ログが出ず即 spawn

## 実装ステップ

1. `engine-client/src/connection.rs` に TCP connect の 2s タイムアウトを実装した
   external プローブ専用関数を追加・export
2. `engine-client/src/process.rs` に `start_or_attach()` 追加（env チェック →
   probe → 失敗時 spawn）。spawn 経路に `log::info!` と `spawn_count()` seam を追加
3. `src/main.rs` の `ProcessManager::start(port)` 呼び出しを `start_or_attach()` に
   差し替え（起動ポリシーを `main.rs` に書かない）
4. `docs/✅python-data-engine/spec.md` §3.1 / §4.1.1 を更新（ロックファイル前提を撤去）
5. `CLAUDE.md` に運用追記
6. Rust 統合テスト 5 ケース追加（正常 2 + failure 2 + ショートサーキット 1）。
   spawn / attach 判定は `spawn_count()` seam で行う
7. 手動 E2E 確認 3 シナリオ

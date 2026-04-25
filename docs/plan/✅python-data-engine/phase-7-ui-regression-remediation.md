# フェーズ 7: UI リグレッション修復と E2E 検証の明文化

> 作成: 2026-04-25 / ブランチ `tachibana/phase-0/plan`
>
> 前提: Phase 0〜6 は「Rust / Python 単体テスト」「cargo clippy」「cargo build」の静的検証は通過しているが、**`main` ブランチ（Rust 単体構成）で動いていた UI 動線が Python IPC 経由でも通ることの実地検証**は `implementation-plan.md` に含まれていなかった。結果として、Phase 6 完了後の実機起動で以下のような UI リグレッションが発覚している。

## 0. トリアージ中の不具合

| ID | 症状 | 再現手順 | 暫定仮説 |
|----|------|----------|----------|
| UI-1 | 起動直後、サイドバーの銘柄検索（虫眼鏡）を開いても一覧が空 | アプリ起動 → tickers_table トグル | **根本原因特定済み (2026-04-25)**: `TickerStats::daily_price_chg: f32` が Python 側の `str(...)` 送出と型不整合。25,518 件のパースエラーで全 venue の stats が silent drop → `ticker_rows` が空のまま。[修正済み: `de_f32_from_number_or_string` カスタム deserializer を追加] |
| UI-2 | MEXC depth snapshot が "Expected this character to be either a ',' or a ']'" で無限再接続ループ | アプリ起動後に MEXC futures ストリーム購読時 | Python 側 MEXC depth snapshot のレスポンスパーサがネストした配列 `[[p,q,n],[p,q,n]]` を処理できていない疑い |
| UI-3 | OKX SWAP で `resync streak 5, backing off 16.0s` まで到達する depth gap 頻発 | アプリ起動後に OKX futures depth 購読時 | Python 側 OKX syncer の seqId チェックが gap 誤検知、または snapshot→diff の切替タイミング不整合 |

`main` では [`AdapterHandles::spawn_all()`](https://github.com/.../src/main.rs) が同期的にネイティブバックエンドを立ち上げ、`Sidebar::new` から呼ばれる `fetch_metadata_task` は同プロセス内の REST クライアントを直接叩いていたため、**metadata/stats の到着順・timing は自明**だった。IPC 化により「Ready 直後に `ListTickers` を送ってよいか」「Python ワーカーの HTTP クライアントが初期化済みか」が非自明になった。

## 1. スコープと非スコープ

### 1.1 スコープ

1. UI-1 を含む起動時リグレッションの根本修復
2. `implementation-plan.md` で未定義だった **UI 経路 E2E 検証タスク** をフェーズ完了条件として明文化する
3. Phase 6 の残タスク（CI、clippy、cold-start 計測）を取り込む
4. `engine-client/src/hybrid.rs` および関連テストの整理方針決定

### 1.2 非スコープ

- Phase 2〜5 で合意済みの IPC スキーマ / プロトコルの再設計
- 新取引所の追加
- `onedir` 配布形式への切替

## 2. タスク

### T1. UI-1 の根本原因特定と修復 (Priority: High)

#### T1.1 ログベース切り分け

- [ ] `RUST_LOG=info,engine_client=debug,flowsurface=debug` で起動し、以下をすべて収集:
  - `Ticker metadata fetch failed for <venue>: ...` ([tickers_table.rs:1699](../../src/screen/dashboard/tickers_table.rs#L1699))
  - `fetch_ticker_metadata timeout` ([backend.rs:502](../../engine-client/src/backend.rs#L502))
  - Python 側 `_handle_list_tickers` の着信・応答ログ ([python/engine/server.py](../../python/engine/server.py))
- [ ] **完了条件**: 以下の 4 象限のどれに該当するか確定:
  - (a) `ListTickers` が Python に届かない
  - (b) Python が空配列を返している
  - (c) Python が返したが Rust 側の ASCII / `venue` 等価フィルタで全弾除外
  - (d) metadata は入るが stats が届かず `update_ticker_rows` の AND 条件で 0 行

#### T1.2 象限別の修復

- [ ] **(a) の場合** — handshake 完了前に `ListTickers` が送られている疑い。`EngineConnection` が `Ready` を受信してから metadata fetch を発火するよう `Sidebar::new` の初期 Task をゲート。具体的には `main.rs:476` の `chain(launch_sidebar...)` を `ENGINE_READY` watch 経由の `Subscription` にぶら下げる形に変更する設計を検討。
- [ ] **(b) の場合** — Python ワーカーの HTTP クライアントが Ready 発行時点でまだウォームアップ済みでない。`engine/server.py` の `_do_handshake` で全 worker の `prepare()` を `await` してから `Ready` を送るよう修正。
- [x] **(c) の場合** ✅ (2026-04-25) — 確定原因は ASCII フィルタではなく `TickerStats` の serde 型不整合だった。[exchange/src/lib.rs:660](../../exchange/src/lib.rs#L660) の `daily_price_chg: f32` に `de_f32_from_number_or_string` カスタム deserializer を追加。[exchange/src/serde_util.rs](../../exchange/src/serde_util.rs) に helper を実装。`TickerStats` に 2 件の回帰テスト（`daily_price_chg_accepts_stringified_number` / `daily_price_chg_accepts_json_number`）を追加。Python 側 (`binance.py:472` 他 4 venue) の `str(...)` 送出はそのまま許容する方針（IPC スキーマは number or string の両方を受理できるよう lenient に）。
- [ ] **(d) の場合** — [tickers_table.rs:327-332](../../src/screen/dashboard/tickers_table.rs#L327-L332) の `build_stats_fetch_task` ゲート条件（`selected_exchanges.contains(&venue)`）と、`MetadataFetchState` の pending セットが `selected_exchanges` と一致するかを確認。

#### T1.3 回帰テスト

- [ ] `engine-client` 側: `EngineClientBackend::fetch_ticker_metadata` が Ready 未受信時にタイムアウトする/しないかの期待動作を明文化し unit test 化。
- [ ] Rust 側: `TickersTable::new_with_settings` → `UpdateMetadata` → `UpdateStats` の一気通貫テスト（Python engine スタブを `MockEngineConnection` で注入、`engine-client/tests/` に配置）。

**完了条件**: 起動直後に虚眼鏡を開くと全 5 venue の銘柄がリスト表示される状態が手動 QA + 自動テストの両方で確認できる。

### T2. 起動ハンドシェイク契約の強化 (Priority: High)

spec §4.5 は Hello/Ready の順序を固定しているが、**"Ready 発行時点で worker が業務リクエストを受理できること"** は未規定。

- [ ] spec §4.5 に以下を追記:
  > Python engine は `Ready` を送る前に、全 worker の REST/HTTP クライアント初期化（`aiohttp.ClientSession` 等）を完了しなければならない。`ListTickers` / `FetchTickerStats` / `FetchKlines` は `Ready` 受信直後から即時受理可能とする。
- [ ] Python: `engine/server.py` の handshake で `await asyncio.gather(*(w.prepare() for w in workers))` を追加。各 worker に `async def prepare(self)` を生やし、`ClientSession` の構築 + DNS 事前解決までを行う。
- [ ] Rust: `EngineConnection` に `wait_ready()` API を追加し、`AdapterHandles` 経由のあらゆる fetch 系呼び出しが `Ready` 到着を待ってから送信できるようにする。タイムアウトは 20 秒（Phase 6 の onefile cold-start を考慮）。
- [ ] `engine-client/tests/` に「Ready 前 fetch は待機する」テストを追加。

### T3. UI 経路 E2E スモークテスト (Priority: High)

Phase 7 以降の完了条件として固定する。

- [ ] `tests/e2e/` (新設) に以下のシナリオを定義:
  1. 起動 → 銘柄一覧 ≥ 100 件がサイドバーに表示される
  2. 任意の Binance 銘柄をクリック → チャート描画までの往復が 5 秒以内
  3. depth/trade/kline ストリームが 30 秒間切断なく流れる
  4. Python プロセス `kill -9` → Toast 表示 → 5 秒以内に自動復旧 → 板再同期
- [ ] 実行は cargo workspace 管理外の手動 QA スクリプトから開始。段階的に `cargo xtask e2e` 等で自動化。
- [ ] `implementation-plan.md` の各フェーズ完了条件に「E2E スモーク合格」を後付け追記（フェーズ 6 以降は必須）。

### T4. Phase 6 残タスクの取り込み (Priority: Medium)

既存 `implementation-plan.md` §フェーズ 6「残タスク」をここに取り込む。

- [ ] `.github/workflows/release.yml` に PyInstaller 依存導入と `build-engine.sh` 呼び出しを追加
- [ ] `engine-client/tests/depth_gap_recovery.rs` / `connection_closed.rs` の `unused variable` clippy 違反修正
- [ ] `onefile` cold-start 計測（Windows / macOS / Linux）→ `docs/plan/benchmarks/phase-6.md` に記録
- [ ] Linux AppImage / Flatpak の要否判断

### T5. `engine-client/src/hybrid.rs` の決着 (Priority: Low)

Phase 5 完了時点で不要となったが残置。

- [ ] 参照箇所をすべて削除し crate から除去
- [ ] `HybridVenueBackend` に依存していたテストを削除または `EngineClientBackend` で書き直し

## 3. ドキュメント更新

- [ ] `README.md`（本ディレクトリ）の進捗サマリ表に「フェーズ 7: UI リグレッション修復」を追加し進行中でマーク
- [ ] `spec.md` §4.5 にハンドシェイク契約の追記（T2 参照）
- [ ] `implementation-plan.md` フェーズ 6 の完了条件に「手動 QA で UI 経路 E2E が合格」を追記
- [ ] `open-questions.md` に「E2E テスト自動化の運用方針（Playwright 相当 or 既存の `agent-experience-verification` スキル流用）」を追加

## 4. 依存関係と順序

```
T1.1 (ログ切り分け)
   ├─► T1.2 (象限別修復) ──► T1.3 (回帰テスト) ──┐
   │                                             │
   └─► T2 (ハンドシェイク契約強化) ──────────────┤
                                                  ├──► T3 (E2E スモーク) ──► リリース
   T4.a (CI) ─────────────────────────────────────┤
   T4.b (clippy 修正) ─────────────────────────────┤
   T5 (hybrid.rs 整理) ───────────────────────────┘
```

T1.1 は他すべてのブロッカー。まずログ収集を最優先。

## 5. 完了条件（Phase 7 全体）

1. UI-1 が解消され、main ブランチと同等の起動時ユーザー体験が得られる
2. Ready 契約が spec と Python 実装の両方に反映されている
3. UI 経路の E2E スモークが手動 QA 手順書として存在し、少なくとも 1 回合格している
4. Phase 6 の全残タスクが完了している
5. `cargo test --workspace` / `pytest` / `cargo clippy -- -D warnings` すべて clean

## 6. リスクと未決事項

- **R1**: Ready ゲートを厳密化すると cold-start 時の体感起動時間が伸びる。T2 実施後に手動計測して許容範囲を再確認する。
- **R2**: E2E スモーク自動化の土台が未定。`.claude/skills/agent-experience-verification` の流儀を踏襲するか、独自スクリプトか、open-questions で決定する。
- **R3**: UI-1 の象限が (b) または複合要因だった場合、Python 側の変更量が想定より大きくなる。T1.1 のログ収集に 1 日取る前提で見積もる。

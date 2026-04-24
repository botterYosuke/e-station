# Rust ビュアー化 + Python データエンジン化 計画

本ディレクトリは、現状 Rust 単体構成の Flowsurface (e-station) を、
**Rust = ビュアー専用 / Python = 取引所データ取得・配信** という二層構成に
リアーキテクトする計画をまとめたものです。

## 目次

- [`current-architecture.md`](./current-architecture.md) — 現状調査結果
- [`spec.md`](./spec.md) — 新仕様（責務分割・IPC・データモデル）
- [`implementation-plan.md`](./implementation-plan.md) — 段階的な実装計画
- [`open-questions.md`](./open-questions.md) — 未決事項・要相談事項

## フェーズ進捗サマリ

| フェーズ | 内容 | 状態 |
|---------|------|------|
| 0 | 準備・ベースライン計測 | ✅ 完了 |
| 0.5 | venue 単位 backend 抽象化 | ✅ 完了 (2026-04-24) |
| 1 | Python データエンジン MVP (Binance) | ✅ 完了 (2026-04-24) |
| 2 | Rust engine-client 実装・Binance 切替 | ✅ 完了 (2026-04-24) |
| 3 | 残り取引所 Python 移植 | ✅ 完了 (2026-04-24) |
| 4 | ヒストリカルデータ bulk download 移植 | ✅ 完了 (2026-04-24) |
| 5 | Rust から取引所コード削除 | 未着手 |
| 6 | 配布・運用整備 | 未着手 |

---

## フェーズ 4 コードレビュー結果（2026-04-24）— Phase 5 着手前に対応すること

### 優先度: High（Phase 5 前に必須）

#### H1/H2: Python 並行 race + data_path キャッシュ無効
**対象**: [python/engine/exchanges/binance.py:629-641](../../python/engine/exchanges/binance.py#L629-L641)

- `data_path is None` のとき成功ダウンロードがメモリ保持のみ → 同一 worker 内でも毎回 `data.binance.vision` から再ダウンロードが発生。**対策**: 本番 spawn で `data_path` を渡す配線と同時に修正。
- 同一 (ticker, date) の並行ダウンロードで `.zip.tmp` が固定名のため race condition が発生。**対策**: `tmp_path = zip_path.with_suffix(f".{os.getpid()}.{uuid4().hex}.tmp")` + (ticker, date) 単位の `asyncio.Lock`。

#### M4: 429/5xx の誤フォールバック（実質 High）
**対象**: [python/engine/exchanges/binance.py:637-640](../../python/engine/exchanges/binance.py#L637-L640)

- `resp.is_success` で 404 / 429 / 5xx をまとめて intraday フォールバックしている。
- **対策**: 429/5xx は指数バックオフ retry、404 のみフォールバックに分ける。レート制限悪化の温床。

### 優先度: Medium

#### M1 (Rust): illiquid 銘柄で empty days が発散する
**対象**: [src/connector/fetcher.rs:471-493](../../src/connector/fetcher.rs#L471-L493)

- `EMPTY_DAYS_WARN_THRESHOLD` は警告のみ。廃止銘柄などで数年分リクエストするとバックエンド呼び出しが数千回発散する。
- **推奨**: `MAX_EMPTY_DAYS: u32 = 365` を定数定義し `AdapterError::InvalidRequest` を返す hard stop を追加。

#### M2 (Rust): intraday パスが single-day クランプを経由しない
**対象**: [exchange/src/adapter/hub/binance/fetch.rs:752-753](../../exchange/src/adapter/hub/binance/fetch.rs#L752-L753)

- intraday 専用パスが `effective_to_time` クランプを経由せず生の `to_time` を渡している。現行呼び出し元は 1 日刻みで実害なしだが、`VenueBackend::fetch_trades` を直接呼ぶと single-day 保証が崩れる。
- **推奨**: 関数先頭で一括クランプを追加。

### 優先度: Low / Nit

- **L1** ([exchange/src/adapter/hub/binance/fetch.rs:937-940](../../exchange/src/adapter/hub/binance/fetch.rs#L937-L940)): `unsafe { std::env::set_var }` の SAFETY コメントが不正確。`cargo test` はマルチスレッドのため `serial_test` 等でシリアライズすべき（UB リスク）。
- **L2** ([engine-client/src/backend.rs:320-374](../../engine-client/src/backend.rs#L320-L374)): `let _ = connection.send(...).await` がサイレント。`log::error!` を追加。
- **L3** ([engine-client/src/backend.rs:510](../../engine-client/src/backend.rs#L510)): `unwrap_or_default()` がサイレント失敗。パースエラー時にログを出すべき。
- `#[cfg(test)] let domain = ...` の 3 重複。
- Python 側: `_DAY_MS` 定数が 3 箇所重複。`tmp_path.write_bytes` 失敗時のゴミ `.tmp` 残存（try/finally 推奨）。
- Python 側: `ticker` がパス連結に直接使われている（`re.fullmatch(r"[A-Z0-9_-]+", ticker)` でパス traversal 防御推奨）。
- **テスト漏れ**: zip 破損 → intraday fallback、429/5xx 時の挙動の回帰テストなし。

---

## 実装着手前に固定すべき事項

レビュー指摘（2026-04-24、2 回目含む）を受け、以下は実装前に必ず決める。
**性能よりもまず障害時整合性と移行境界の定義**を優先する。

### A. 移行境界の定義
1. **IPC DTO 層**: 既存 Rust 型はそのまま serde に流せない（`Kline`/`OpenInterest` に派生なし、`Depth` は内部表現、`Event` は `Arc` / `Box<[T]>` を含む）。DTO 層を別定義する方針で進める。→ [spec.md §4.3](./spec.md#43-メッセージスキーマ)
2. ✅ **`VenueBackend` trait の責務網羅** (Phase 0.5 完了): ストリーム・フェッチ系に加え `fetch_ticker_metadata` / `fetch_ticker_stats` / `request_depth_snapshot` / `health` を含む 9 メソッドで網羅。`NativeBackend` による既存動作の維持と `set_backend` による venue 単位 swap-in API を実装済み。→ [exchange/src/adapter/venue_backend.rs](../../exchange/src/adapter/venue_backend.rs)
3. **Open Interest の完全経路**: インジケータが継続的に要求するため、Python MVP の初期スコープに OI REST を含める。IPC コマンド `FetchOpenInterest` / イベント `OpenInterest` を定義。→ [spec.md §4.2](./spec.md#42-メッセージ方向)

### B. 障害時整合性
4. **depth の整合性保証**: `session_id` + `sequence_id` + `prev_sequence_id` による gap 検知、`DepthGap` イベント、自動 `RequestDepthSnapshot`、checksum 検証。depth diff は drop 不可。→ [spec.md §4.4](./spec.md#44-バックプレッシャと整合性保証)
5. **Python プロセス復旧プロトコル**: Rust を source of truth とし、購読セット・進行中フェッチ・プロキシ設定を保持。crash 検知 → 指数バックオフで spawn → ハンドシェイク → 状態再投入。**フェーズ 1 の完了条件に含める**（後回し禁止）。→ [spec.md §5.3](./spec.md#53-python-プロセス復旧プロトコル)
6. **起動ハンドシェイク**: `Hello`（schema_version / session_id / token）→ `Ready`（capabilities）→ `SetProxy` → マーケットデータ系コマンド、の順を固定。`Connected` と `Ready` の意味を分ける。→ [spec.md §4.5](./spec.md#45-起動ハンドシェイク)

### C. セキュリティ
7. **ローカル IPC のアクセス制御**: loopback 専用バインド、ランダム接続トークン（stdin で受け渡し）、単一クライアント制限。CLI 引数でのポート・トークン受け渡しは不採用。→ [spec.md §4.1.1](./spec.md#411-ローカル-ipc-のアクセス制御)
8. **プロキシ資格情報の扱い**: keyring → Rust 保持 → `Ready` 受領後の IPC `SetProxy` で Python に渡す（CLI 引数・環境変数は基本採用しない）。→ [spec.md §5.4](./spec.md#54-プロキシ資格情報の受け渡し)

### D. 性能・射程（3 回目レビュー追加）
9. **非機能要件（合格ライン）**: IPC レイテンシ中央値 < 2 ms / p99 < 10 ms、復旧 < 3 秒、CPU +30% 以内など。フェーズ 0 でベースライン計測を先に取る。→ [spec.md §9](./spec.md#9-非機能要件合格ライン)
10. **depth チャネルのバイナリ化要否**: 第一段階は JSON、フェーズ 2 の計測で目標未達なら `DepthDiff` / `DepthSnapshot` のみ MessagePack + 固定小数 i64 に切替。→ [spec.md §4.3.1](./spec.md#431-depth-チャネルのバイナリ化検討)
11. **Python プロセスモデル**: フェーズ 1 は asyncio 単一プロセスで確定、ただし `ExchangeWorker` 抽象を最初から入れて将来 venue 分割可能な構造にする。→ [spec.md §6.1](./spec.md#61-プロセスモデルフェーズ-1-時点)
12. **Rust 直結モード長期方針**: 暫定撤去（案 A）。フェーズ 2 の計測結果で案 C（optional feature）に戻す余地を残す。→ [spec.md §7.1](./spec.md#71-rust-直結モードの長期方針要決定)
13. **スキーマバージョニング運用**: major / minor を分け、minor 差は警告のみで接続継続。→ [spec.md §4.5.1](./spec.md#451-スキーマバージョニング運用)

### 残る要決定（実装前に合意したい）
- **[Q5](./open-questions.md) Rust 直結残置の長期方針**（案 A/B/C）
- **[Q7](./open-questions.md) スキーマ生成方針**（JSON Schema + quicktype / schemars + datamodel-code-generator / proto）
- **[Q9](./open-questions.md) 非機能要件の合格ラインの数値合意**（§9 の提案値でよいか）
- **[Q10](./open-questions.md) depth バイナリ化の判断条件と実装着手タイミング**

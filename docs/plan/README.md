# Rust ビュアー化 + Python データエンジン化 計画

本ディレクトリは、現状 Rust 単体構成の Flowsurface (e-station) を、
**Rust = ビュアー専用 / Python = 取引所データ取得・配信** という二層構成に
リアーキテクトする計画をまとめたものです。

## 目次

- [`current-architecture.md`](./current-architecture.md) — 現状調査結果
- [`spec.md`](./spec.md) — 新仕様（責務分割・IPC・データモデル）
- [`implementation-plan.md`](./implementation-plan.md) — 段階的な実装計画
- [`open-questions.md`](./open-questions.md) — 未決事項・要相談事項

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

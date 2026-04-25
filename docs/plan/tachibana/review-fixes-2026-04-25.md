# 計画レビュー修正ログ（2026-04-25）

レビュー findings に基づき以下のファイルを修正した。

## 変更ファイル一覧

| ファイル | 修正 |
| :--- | :--- |
| `spec.md` | HIGH-C: ボタン配置をサイドバー固定に。HIGH-D/LOW-3: second_password env 削除・手動/自動再ログイン境界を明文化 |
| `architecture.md` | HIGH-D: 採用 env 名を 3 つに絞り SECOND_PASSWORD を Phase 1 不採用と明記 |
| `README.md` | HIGH-D: env 名一覧を修正。MEDIUM-1: TickerListed 架空型参照を削除 |
| `implementation-plan.md` | HIGH-D: T0.2 env 名タスクを修正。HIGH-B: T2 StartupLatch 設計を DI 方式へ書直し。MEDIUM-3: T0.2 受け入れを 2 段（個別/フェーズ）に分離。MEDIUM-4: request_id reject 責務を oneshot index 側に移動。MEDIUM-5: `_ensure_master_loaded` を Lock + Event 組合せに修正。MEDIUM-6: ticker pre-validate regex の Phase 2 拡張注記追加。MEDIUM-7: tickers dict キー欠落 debug ログ規約追加。LOW-1: cache key を `master_<env>_<YYYYMMDD>.jsonl` 形式に。LOW-4: tools/secret_scan.ps1 sibling を同時新設タスクに変更 |
| `data-mapping.md` | MEDIUM-1: TickerListed → `EngineEvent::TickerInfo.tickers[*]` dict 方式に訂正。MEDIUM-2: capabilities の `session_lifetime_seconds: 86400` を削除 |
| `inventory-T0.md` | HIGH-A: FD コードブロッカーに責任者・縮退影響・更新リストを追記。LOW-2: Timeframe serde migration テスト (`exchange/tests/timeframe_state_migration.rs`) を明示追加 |
| `.claude/skills/tachibana/SKILL.md` | LOW-5: L41 `BASE_URL_*` 旧表記を F-L1 方針（Python 1 ファイル限定）に沿って補正 |

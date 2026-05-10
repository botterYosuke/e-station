---
title: kabuステーション venue 統合 実装計画
status: migrated
migrated_from:
  - docs/✅kabusapi/README.md
source_commit: ea5022b
---

# kabuステーション API 統合プラン

三菱UFJ eスマート証券（旧 auカブコム）**kabuステーション API（v1.5）** を本アプリの venue として追加するための計画一式。立花証券 e支店 venue と**同じ Python autonomous アーキテクチャ**に揃える。Rust 側は UI / I/O は持たないが、**enum 追加・capabilities 拡張・IPC schema minor bump・既存 lifecycle wiring の更新**は触る。

> **注**: 旧 `docs/specs/venues/kabusapi/archive/implementation-plan.md` および `docs/specs/venues/kabusapi/archive/plan.md` は ADR 抽出候補として `docs/decisions/0017-implementation-plan.md` / `docs/decisions/0019-plan.md` に切り出される予定（Wave 3）。本ファイルは恒常参照される実装計画の Phase 0 出口時点エントリポイント。

## 安全不変条件（実装着手前に必ず読む）

- **本番接続は `KABU_ALLOW_PROD=1` の明示 opt-in を必須とする**。デフォルトは検証環境（`localhost:18081`）に固定し、Python 側で多層ガードを敷く。Phase 1 では実弾発注経路を一切作らない
- **取引パスワード `DEV_KABU_TRADE_PASSWORD` は Phase 2 着手前に env 名を予約決定**（API パスワードと別物）
- **`LiveSession.login()` は Phase 1 着手で破壊的にシグネチャ拡張される**（既存 `*, user_id, password` → `*, venue, ...`）。立花の自動化や既存テストが kabu 実装着手と同時に壊れるリスクがあるため、互換策（venue 引数追加 or `login_kabu_station()` 等の別 method 分離）を Phase 1 着手前に確定する

## 関連文書

| ファイル | 役割 |
| :--- | :--- |
| `docs/specs/venues/kabusapi.md` | venue 固有仕様（X-API-KEY / 50 銘柄上限 / WebSocket / 流量制限） |
| `docs/architecture/modules/kabusapi-adapter.md` | プロセス境界（Rust / Python）、認証・トークン所在、起動シーケンス |
| `docs/reference/external-apis/kabusapi.md` | kabu の `PushBoardSuccess` ↔ 既存 `DepthSnapshot` IPC マッピング表 |
| `docs/roadmap/kabusapi/open-questions.md` | 未確定事項と決定期限 |
| `docs/roadmap/kabusapi/_invariants-fragment.md` | 各 K-task の不変条件テスト一覧（INV-KABUSAPI-NNN） |

## 一次資料

- 公式 OpenAPI: `.claude/skills/kabusapi/reference/kabu_STATION_API.yaml`
- ポータル: `.claude/skills/kabusapi/ptal/`（howto / push / error / faq）
- Python サンプル: `.claude/skills/kabusapi/sample/Python/`
- コーディング規約・運用ルール: `.claude/skills/kabusapi/SKILL.md`（**R1〜R10 を必ず守る**）
- 参照テンプレ: `.claude/skills/tachibana/SKILL.md`（venue 統合の前例）

## 一行サマリ

kabuステーション venue は **localhost ローカルサーバ・Windows 限定・REST + JSON・X-API-KEY 認証・PUSH WebSocket（50 銘柄上限）** が立花との最大の違い。**Phase 1 はチャート閲覧（板＋気配＋直近約定）に絞ったリードオンリー統合**を検証環境（`localhost:18081`）で成立させる。発注は **Phase 2 以降**。

## 設計原則（立花 venue と共通）

- **venue 固有 I/O は Python 側に集約**（`python/engine/exchanges/kabusapi*.py`）
- **Rust 側は UI / I/O を持たないが、以下の wiring は触る**:
  - `Venue::KabuStation` / `Exchange::KabuStationStock` enum 追加（Phase 1 は `KabuStationStock` 1 バリアントのみ）
  - `engine_client::capabilities` の `venue_capabilities["kabu_station"]` キー追加
  - IPC schema minor bump（`Venue::from_str` / `AdapterHandles::kabu_station: Option<Arc<dyn VenueBackend>>` フィールド追加）
  - 既存 lifecycle wiring（`apply_after_handshake` の venue-agnostic 経路を素通し確認、`Command::RequestVenueLogin.venue` で `"kabu_station"` 受理）
- **IPC venue キー文字列は `"kabu_station"`**（Rust `Venue::KabuStation` と命名整合）
- **クレデンシャル・トークンは Python メモリのみ保持**（Rust 経路に流さない）
- **ログイン UI は Python tkinter サブプロセス**（Rust にダイアログコードを書かない）。**取引パスワード（取消/発注時）の収集 UI も同じ tkinter サブプロセス方式に統一**
- **本番 URL リテラルは `kabusapi_url.py` 1 箇所**（Rust 側に持たない。`localhost:18080` 本番 / `localhost:18081` 検証）
- **debug env は venue prefix 付き**: `DEV_KABU_API_PASSWORD` / `DEV_KABU_PROD`
- **Python 単独モード移行を見据えた構造**

## kabu 固有の前提（立花と違う点）

- **Windows 限定** — kabuステーション本体（Win GUI アプリ）が REST/WebSocket サーバを `localhost:18080`（本番）/ `localhost:18081`（検証）に立てる。Linux/Mac で動かない。CI で本物 API を叩けない（`pytest -m demo_kabu` は HTTPXMock のみ）
- **本体プロセスの起動が前提** — 落ちている / ログアウトで TCP 拒否
- **トークンキャッシュは作らない** — 本体終了/ログアウトで失効。立花の `tachibana_session.json` 相当は kabu には**置かない**（起動毎に `/token` を取り直す）
- **PUSH 銘柄登録上限 50** — 立花にない概念。Python 側で LRU 管理（`kabusapi_register.RegisterSet`）
- **訂正 API なし** — 「取消 → 再発注」で実装。`CLMKabuCorrectOrder` 相当は無い
- **流量制限が明示** — 発注 5/s、余力 10/s、情報 10/s。token-bucket 必須

## フェーズ概要

| Phase | スコープ | 出口条件 |
| :--- | :--- | :--- |
| Phase 0 | 設計文書（spec / architecture / data-mapping / open-questions / invariant 雛形） | 5 文書着地 |
| Phase 1 | リードオンリー MVP（板・気配・直近約定をチャート表示） | `docs/specs/venues/kabusapi.md` §4 受け入れ条件すべて |
| Phase 2 | 発注・取消（取引パスワード収集 UI、`KabuTradePasswordHolder`） | 別途定義 |
| Phase 3 | 市場細分化（東証 / 名証 / 福証 / 札証） | 別途定義 |
| Phase 4 | 本番接続有効化（バナー / `KABU_IS_PRODUCTION` AtomicBool） | 別途定義 |

## 進捗ノート（2026-05-09 時点）

### Phase 1 完了

Phase 1 MVP（リードオンリー統合）は完了済み。完了後に以下の post-fix バグが修正されました:

- **Issue #35**: kabu ログイン済みでもラダーが "Waiting for data..." のまま（`kabusapi_rest.py` / `kabusapi_ws.py` / `server.py` 修正）
- **Issue #36**: kabu VenueReady 時に `GetBuyingPower` / `GetPositions` が送信されなかった（`handlers/dashboard.rs` / `handlers/venue.rs` 修正）
- **Issue #37**: kabu ログイン中に BuyingPower / Positions ペインを後から追加しても auto-fetch が発火しなかった（`handlers/dashboard.rs` / `handlers/venue.rs` / `main.rs` 修正）

### Phase 2 着手（2026-05-09）

Issue #25 / #33 / #34 にて以下を実装:

- **注文パネル venue トグル（Rust フロントエンド, Issue #34）**: OrderEntry / OrderList / BuyingPower / Positions パネルのタイトルバーに 立花 / kabu トグルボタンを追加。`Dashboard` に `order_venue: Venue`, `tachibana_ready: bool`, `kabu_ready: bool` フィールドを追加。両 venue が Ready のときのみトグルを表示
- **注文 venue ルーティング（Python バックエンド, Issue #33）**: `python/engine/server.py` が選択 venue（立花 or kabu）に応じて注文を振り分けるルーティングを実装。テスト: `python/tests/test_server_order_venue_routing.py`

> **スコープ注意**: Phase 2 着手 = venue toggle UI + ルーティング基盤の整備。実弾発注（`POST /sendorder`）は引き続き Phase 2 完了を待つ。取引パスワード収集 UI 等の未実装要素は変わらず Phase 2 スコープ内。

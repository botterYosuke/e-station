# kabuステーション API 統合プラン

三菱UFJ eスマート証券（旧 auカブコム）**kabuステーション API（v1.5）** を本アプリの venue として追加するための計画一式。立花証券 e支店 venue（[../../✅tachibana/README.md](../../✅tachibana/README.md)）と**同じ Python autonomous アーキテクチャ**に揃える。Rust 側は UI / I/O は持たないが、**enum 追加・capabilities 拡張・IPC schema minor bump・既存 lifecycle wiring の更新**は触る（詳細は [plan.md §1.1](./plan.md) と §3）。

## 安全不変条件（実装着手前に必ず読む）

- **本番接続は `KABU_ALLOW_PROD=1` の明示 opt-in を必須とする**。デフォルトは検証環境（`localhost:18081`）に固定し、Python 側で多層ガードを敷く（[plan.md Phase 1 非ゴール](./plan.md) / Phase 2 K13 / Phase 4）。Phase 1 では実弾発注経路を一切作らない
- **取引パスワード `DEV_KABU_TRADE_PASSWORD` は Phase 2 着手前に env 名を予約決定**（API パスワードと別物）
- **`LiveSession.login()` は Phase 1 着手で破壊的にシグネチャ拡張される**（既存 `*, user_id, password` → `*, venue, ...`）。立花の自動化や既存テストが kabu 実装着手と同時に壊れるリスクがあるため、互換策（venue 引数追加 or `login_kabu_station()` 等の別 method 分離）を Phase 1 着手前に確定する（[plan.md §1.3 / §3](./plan.md) U32）

## 文書構成

| ファイル | 役割 |
| :--- | :--- |
| [comparison.md](./comparison.md) | 立花 venue と kabu venue の仕様・運用差異の対比表 |
| [plan.md](./plan.md) | 何をどこに追加するか（ファイル別追加計画とフェーズ分割） |

### 後続作成予定

plan.md §1.4 と一致する後続文書一覧。**Phase 0 出口必須は 5 文書**（spec / architecture / data-mapping / implementation-plan / open-questions）。`invariant-tests.md` は Phase 0 出口で雛形のみ着地し、各 task 詳細は Phase 1 内で追記する。`runbook.md` は Phase 4 着手時に作成する（U7 / U33 / U37）。

| ファイル | 役割 | 着地タイミング |
| :--- | :--- | :--- |
| spec.md | ゴール・非ゴール・スコープ。Phase 1 のリードオンリー範囲を確定 | Phase 0 出口必須 |
| architecture.md | プロセス境界（Rust / Python）、認証・トークン所在、起動シーケンス | Phase 0 出口必須 |
| data-mapping.md | kabu の `PushBoardSuccess` ↔ 既存 `DepthSnapshot` IPC マッピング表 | Phase 0 出口必須 |
| implementation-plan.md | フェーズ分割（Phase 0/1/2/3/4）・受け入れ条件・テスト戦略 | Phase 0 出口必須 |
| open-questions.md | 未確定事項と決定期限 | Phase 0 出口必須 |
| invariant-tests.md | 各 K-task の不変条件テスト一覧 | Phase 0 出口で雛形のみ、各 task 詳細は Phase 1 内で追記 |
| runbook.md | 事故対応・取消手順 | Phase 4 着手時作成 |

## 一次資料

- 公式 OpenAPI: [.claude/skills/kabusapi/reference/kabu_STATION_API.yaml](../../../.claude/skills/kabusapi/reference/kabu_STATION_API.yaml)
- ポータル: [.claude/skills/kabusapi/ptal/](../../../.claude/skills/kabusapi/ptal/)（howto / push / error / faq）
- Python サンプル: [.claude/skills/kabusapi/sample/Python/](../../../.claude/skills/kabusapi/sample/Python/)
- コーディング規約・運用ルール: [.claude/skills/kabusapi/SKILL.md](../../../.claude/skills/kabusapi/SKILL.md)（**R1〜R10 を必ず守る**）
- 参照テンプレ: [.claude/skills/tachibana/SKILL.md](../../../.claude/skills/tachibana/SKILL.md)（venue 統合の前例）

> **リンク先確認の注記**: 上記一次資料は実装着手前に必ず存在確認する（OpenAPI / ポータル / Python サンプル / SKILL.md の 4 系統）。詳細なチェックリストは [plan.md](./plan.md) §「実装着手前のチェックリスト」に集約。

## 一行サマリ

kabuステーション venue は **localhost ローカルサーバ・Windows 限定・REST + JSON・X-API-KEY 認証・PUSH WebSocket（50 銘柄上限）** が立花との最大の違い。**Phase 1 はチャート閲覧（板＋気配＋直近約定）に絞ったリードオンリー統合**を検証環境（`localhost:18081`）で成立させる。発注は **Phase 2 以降**。

## 設計原則（立花 venue と共通）

- **venue 固有 I/O は Python 側に集約**（`python/engine/exchanges/kabusapi*.py`）
- **Rust 側は UI / I/O を持たないが、以下の wiring は触る**（U2 / U8 / U9 / U10 / U39）:
  - `Venue::KabuStation` / `Exchange::KabuStationStock` enum 追加（Phase 1 は `KabuStationStock` 1 バリアントのみ）
  - `engine_client::capabilities` の `venue_capabilities["kabu_station"]` キー追加
  - IPC schema minor bump（`Venue::from_str` / `AdapterHandles::kabu_station: Option<Arc<dyn VenueBackend>>` フィールド追加）
  - 既存 lifecycle wiring（`apply_after_handshake` の venue-agnostic 経路を素通し確認、`Command::RequestVenueLogin.venue` で `"kabu_station"` 受理）
- **IPC venue キー文字列は `"kabu_station"`**（Rust `Venue::KabuStation` と命名整合／U1）
- **クレデンシャル・トークンは Python メモリのみ保持**（Rust 経路に流さない）
- **ログイン UI は Python tkinter サブプロセス**（Rust にダイアログコードを書かない）。**取引パスワード（取消/発注時）の収集 UI も同じ tkinter サブプロセス方式に統一**（U4）
- **本番 URL リテラルは `kabusapi_url.py` 1 箇所**（F-L1 と整合、Rust 側に持たない。`localhost:18080` 本番 / `localhost:18081` 検証／U5）
- **debug env は venue prefix 付き**: `DEV_KABU_API_PASSWORD` / `DEV_KABU_PROD`
- **Python 単独モード移行を見据えた構造**（[../../✅tachibana/README.md §長期方針（将来の Python 単独モード）](../../✅tachibana/README.md#長期方針将来の-python-単独モード) と一貫）

## kabu 固有の前提（立花と違う点）

- **Windows 限定** — kabuステーション本体（Win GUI アプリ）が REST/WebSocket サーバを `localhost:18080`（本番）/ `localhost:18081`（検証）に立てる。Linux/Mac で動かない。CI で本物 API を叩けない（`pytest -m demo_kabu` は HTTPXMock のみ）
- **本体プロセスの起動が前提** — 落ちている / ログアウトで TCP 拒否
- **トークンキャッシュは作らない** — 本体終了/ログアウトで失効。立花の `tachibana_session.json` 相当は kabu には**置かない**（起動毎に `/token` を取り直す）
- **PUSH 銘柄登録上限 50** — 立花にない概念。Python 側で LRU 管理（`kabusapi_register.RegisterSet`）。詳細は [comparison.md §7 PUSH 配信](./comparison.md#7-push-配信時価ストリーム) 参照（一次参照は comparison §7、U38）
- **訂正 API なし** — 「取消 → 再発注」で実装。`CLMKabuCorrectOrder` 相当は無い
- **流量制限が明示** — 発注 5/s、余力 10/s、情報 10/s。token-bucket 必須

詳細は [comparison.md](./comparison.md) を参照。

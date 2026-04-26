# 立花証券 e支店 API 統合プラン

このディレクトリは、**立花証券 e支店 API（v4r8）を本アプリの取引所（venue）として追加する**ための計画一式である。
親計画は [docs/plan/✅python-data-engine/spec.md](../✅python-data-engine/spec.md)（Rust ビュアー + Python データエンジン）。本計画はその上に「日本株（authenticated venue）」を載せるための差分仕様。

## 文書構成

| ファイル | 役割 |
| :--- | :--- |
| [spec.md](./spec.md) | ゴール・非ゴール・スコープ。何を作り何を作らないか |
| [architecture.md](./architecture.md) | プロセス境界（Rust / Python）、認証クレデンシャルの所在、起動シーケンス |
| [data-mapping.md](./data-mapping.md) | 立花のドメイン概念 ↔ 既存 IPC DTO のマッピング、新設 DTO・Venue・MarketKind |
| [implementation-plan.md](./implementation-plan.md) | フェーズ分割・受け入れ条件・テスト戦略 |
| [implementation-plan-T3.5.md](./implementation-plan-T3.5.md) | T3.5（VenueState FSM / U4 Gate / U1 ログインボタン / U2 バナー / U3 auto-fire / U5 E2E）のステップ別計画 |
| [invariant-tests.md](./invariant-tests.md) | T35- prefix の不変条件テスト一覧（Hello/Ready/SetVenueCredentials/VenueReady シーケンス、resubscribe 単一性ほか） |
| [review-fixes-2026-04-25.md](./review-fixes-2026-04-25.md) | 2026-04-25 レビュー指摘の対応記録（H5 path fidelity / H6 keyring slot ほか） |
| [review-fixes-2026-04-26.md](./review-fixes-2026-04-26.md) | 2026-04-26 PlanLoop ラウンド 3 修正記録（行番号 → path::symbol 機械置換、用語使い分け、T35- prefix 必須化、アンカー死活修正） |
| [open-questions.md](./open-questions.md) | 未確定事項と決定期限 |

## 一次資料

- 公式マニュアル: [.claude/skills/tachibana/manual_files/mfds_json_api_ref_text.html](../../../.claude/skills/tachibana/manual_files/mfds_json_api_ref_text.html)
- REQUEST I/F PDF: [.claude/skills/tachibana/manual_files/api_request_if_v4r7.pdf](../../../.claude/skills/tachibana/manual_files/api_request_if_v4r7.pdf)
- マスタ I/F PDF: [.claude/skills/tachibana/manual_files/api_request_if_master_v4r5.pdf](../../../.claude/skills/tachibana/manual_files/api_request_if_master_v4r5.pdf)
- Python サンプル一式: [.claude/skills/tachibana/samples/](../../../.claude/skills/tachibana/samples/)
  - 統合例: `e_api_sample_v4r8.py`
  - 認証: `e_api_login_tel.py`
  - EVENT (HTTP long-poll): `e_api_event_receive_tel.py`
  - WebSocket: `e_api_websocket_receive_tel.py`
  - 履歴日足: `e_api_get_histrical_price_daily.py`
  - マスタ: `e_api_get_master_tel.py`
- コーディング規約・運用ルール: [.claude/skills/tachibana/SKILL.md](../../../.claude/skills/tachibana/SKILL.md)（**R1〜R10 を必ず守る**）

> **重要（実装着手前に確認）**: SKILL.md は `exchange/src/adapter/tachibana.rs`（約 4,350 行）や `data/src/config/tachibana.rs` を「既存の参考実装」として参照しているが、**現リポジトリには存在しない**（git 全履歴で未確認）。本計画はすべて**ゼロから新設**する前提で書かれている。SKILL.md の R3/R4/R6/R10/§Rust 実装の既存ヘルパー節は**仕様の抽象記述**として読み、ファイル参照は実装の道標としては使えないことに注意。SKILL.md 自体の書き換えタスクは [implementation-plan.md T0.2](./implementation-plan.md) に集約。

## 一行サマリ

立花証券は「**認証つき・JST 営業時間・株式市場・板は 1 行ベースのスナップショット型・kline は日足のみ**」という暗号資産 venue とは性質の異なる venue。Phase 1 では **チャート閲覧（kline + 直近約定 + 板スナップショット）に絞ったリードオンリー統合** をデモ環境のみで成立させる。注文機能は v2 以降。

## 長期方針（将来の Python 単独モード）

- **将来、Rust（iced）を使わず Python 単独で動作する "Python-only モード" の新設を予定**している。Phase 1 はあくまで Rust + Python 構成だが、本計画で増やす機能は **「Python 側だけで完結できる構造」を優先**する
- 結果として下記が原則:
  - venue 固有の知識（API 呼出・パース・認証・UI 文言・ログイン画面）は **Python 側に集約**
  - Rust 側はチャート描画・iced UI フレーム・keyring の OS bridge という汎用責務に絞る
  - IPC（`engine-client`）は **薄い transport** に保つ。venue 固有 DTO（`TachibanaCredentialsWire` 等）は将来の Python-only モードで `engine-client` を経由しなくても、`tachibana_auth.py` などが直接使える形を維持
  - tkinter ログインダイアログ・バナー文言・FD frame パースなど、Phase 1 で Python に置く実装は **Python-only モードでもそのまま再利用できる**
- iced と tkinter の 2 つの windowing system が同時稼働することは**許容**（GUI 一貫性の不利を、venue 拡張容易性と Python-only モードへの将来移行コスト低減で正当化）
- Python-only モードは別計画で扱う。本計画の範囲外だが、**設計判断で迷ったら「Python 単独でも動くか？」を判定基準の 1 つに使う**

## 実装前提の固定事項

- **復旧シーケンスの source of truth は `ProcessManager`**。managed mode の再起動時は `Hello -> Ready -> SetProxy -> SetVenueCredentials -> VenueReady -> metadata fetch / resubscribe` を必ず再実行する。**T3.5 にて `VenueState` FSM / U4 Gate 着地済**: [src/venue_state.rs](../../../src/venue_state.rs) で FSM 化、[engine-client/src/process.rs](../../../engine-client/src/process.rs) の `start()` は `SetVenueCredentials` 送信後に `VenueReady` を同期点として待機して resubscribe を発火する経路に更新済。U4 Gate により `VenueReady` 前の業務リクエストはブロックされる
- **`VenueReady` は冪等イベント**。`request_id` で `SetVenueCredentials` と相関させるが UI は初回 / 再送を区別しない。Rust 側の resubscribe は `ProcessManager` 1 箇所に集約し、UI view 側は `VenueReady` イベントで新規 subscribe を発行しない
- **立花 venue の業務リクエストは `VenueReady` 後にのみ許可**。`Ready` はエンジン全体の起動完了、`VenueReady` は立花認証・session validation 完了（マスタ DL は含まない）を表す
- **runtime 中の自動再ログインは禁止**。`p_errno=2` 検知 → `VenueError{venue:"tachibana", code:"session_expired"}` を Rust UI に投げ、ユーザー再ログイン誘導。**定期 `validate_session` ポーリングも実装しない**（自動再ログイン禁止と矛盾するため）。再ログイン fallback は起動直後の session 検証失敗時に **1 回だけ** 許可
- **venue エラーは venue-scoped イベントで返す**。`VenueError { venue, request_id, code, message }` に統一し、旧 `EngineError{code:"tachibana_session_expired"}` 表記は使わない
- **`SetVenueCredentials` payload は typed**。`serde_json::Value` を使わず `VenueCredentialsPayload::Tachibana(TachibanaCredentialsWire)` で Rust 側 `Debug` マスクを効かせる。**内部保持型は `SecretString` でパスワード・仮想 URL をラップ**し、IPC 送出時のみプレーン `String` の `*Wire` DTO に写像する（architecture.md §2.1、F-B1/F-B2）
- **第二暗証番号は Phase 1 では収集も保持もしない**（F-H5、Q11 改訂）。DTO スキーマ上は `Option<SecretString>` で枠を切るが Rust UI / keyring / Python メモリのいずれにも値を入れず、常に `None` を送る。発注しないものを保持して攻撃面（コアダンプ・スワップ・GC 残存）を増やさない。スキーマは破壊変更にならないため Phase 2（発注）で値の収集・保持を有効化する
- **`MarketKind::Stock` 追加は最小変更では終わらない**。enum の網羅 match、UI の市場別表示、indicator 可用性、timeframe 可用性、market filter まで波及する前提で見積もる。T0.1 で `git grep` 棚卸し必須
- **`TickerInfo` フィールド追加は Hash 影響を伴う**。`#[derive(Hash, Eq)]` で `HashMap` キーとして全クレートに広がっているため、`lot_size` / `quote_currency` 追加時は永続 state の migration 影響を T0 で確認する
- **`Timeframe::D1` は既存型を流用**（新規追加不要）。日本語銘柄名は `TickerInfo` ではなく `EngineEvent::TickerInfo.tickers[*]` の各 ticker dict（現状 `Vec<serde_json::Value>`）に Python 側が `display_name_ja: Option<String>` キーを詰める方式で運搬する（T0.2 確定方針）。`TickerListed` という型は存在しない
- **マスタキャッシュ保存先は Rust から Python へ明示的に受け渡す**。現行の `stdin` 初期 payload は `port` / `token` のみなので、T0 で `config_dir` / `cache_dir` を初期 payload に追加する。**現状実装の差分（T3/T4 完了まで未接続）**: [engine-client/src/process.rs](../../../engine-client/src/process.rs) の stdin 書込みと [python/engine/__main__.py](../../../python/engine/__main__.py) の parser はいずれも `{port, token}` のみ。`config_dir` / `cache_dir` は T4（マスタキャッシュ）、`dev_tachibana_login_allowed` は T3（ログインフロー）で同時に追加する。本節および architecture.md §2.1.1 / spec.md §3.1 は **T3/T4 完了後に成立する不変条件** であり、それまで Python 側 fast-path / マスタキャッシュ機能は実装されていない
- **debug ビルドの env 名は venue prefix 付きで確定（Phase 1）**: `DEV_TACHIBANA_USER_ID` / `DEV_TACHIBANA_PASSWORD` / `DEV_TACHIBANA_DEMO` の 3 つのみ。`DEV_TACHIBANA_DEMO` の **既定値は `true`**（spec.md §3.1 と整合。明示的に `false` を指定しない限り demo 環境が選ばれる）。`DEV_TACHIBANA_SECOND_PASSWORD` は Phase 1 では**採用しない**（F-H5、第二暗証番号は収集も保持もしない）。SKILL.md S2/S3 の `DEV_USER_ID` 系は架空ファイル前提の旧表記なので T0 で SKILL.md を本計画側へ書き換える
- **Python テストフレームワーク**は既存 [python/tests/](../../../python/tests/) と同じ `pytest-httpx` (`HTTPXMock`) に揃える。`respx` は採用しない

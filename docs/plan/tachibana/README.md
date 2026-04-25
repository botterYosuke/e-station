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

> **重要（実装着手前に確認）**: SKILL.md は `exchange/src/adapter/tachibana.rs`（約 4,350 行）や `data/src/config/tachibana.rs` を「既存の参考実装」として参照しているが、**現リポジトリには存在しない**（git 全履歴で未確認）。本計画はすべて**ゼロから新設**する前提で書かれている。SKILL.md の R3/R4/R6/R10/§Rust 実装の既存ヘルパー節は**仕様の抽象記述**として読み、ファイル参照は実装の道標としては使えないことに注意。

## 一行サマリ

立花証券は「**認証つき・JST 営業時間・株式市場・板は 1 行ベースのスナップショット型・kline は日足のみ**」という暗号資産 venue とは性質の異なる venue。Phase 1 では **チャート閲覧（kline + 直近約定 + 板スナップショット）に絞ったリードオンリー統合** をデモ環境のみで成立させる。注文機能は v2 以降。

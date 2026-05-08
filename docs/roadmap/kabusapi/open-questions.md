---
title: kabuステーション venue 未確定事項
status: migrated
migrated_from:
  - docs/✅kabusapi/open-questions.md
source_commit: 8fc3349
---

# kabuステーション venue 統合: 未確定事項

Phase 0 出口時点で未確定の事項をまとめる。解消次第、ここに決定事項を転記し、
implementation-plan.md の該当 Q-K 番号に決定事項を書き込む。

| # | 論点 | 候補 | 決定タイミング | 状態 |
| :-- | :--- | :--- | :--- | :--- |
| Q-K1 | PUSH WebSocket 切断後、`PUT /register` 銘柄リストはサーバ側で保持されるか？ | (a) 保持される / (b) 保持されない | ptal/push.html 確認後 | **暫定: (b)** — デフォルト挙動は「再接続後は常に全件 re-register」で固定（U6）。検証結果に依らないフォールバック |
| Q-K2 | `/orders` の約定通知は polling と PUSH のどちらか？ | (a) polling (`GET /orders`) / (b) PUSH WebSocket | OpenAPI 確認後 | **暫定: (a) polling** — kabu WebSocket は登録銘柄の時価のみ。約定は `GET /orders` polling と推定 |
| Q-K3 | kabuステーション本体の早朝強制ログアウト時刻は ptal/howto.html 記載か？ | 記載あり / 記載なし | ptal/howto.html 確認後 | **未確定** — 実機確認が必要。暫定窓として `_invariants-fragment.md` §early-morning-logout に「4:00〜9:00 JST 帯の `local_app_down` を INFO 扱い」と定義（U25） |
| Q-K4 | `Exchange` enum で市場細分化（東証=1/名証=3/福証=5/札証=6）が必要か？ | (a) 必要（Phase 1 から） / (b) 不要（Phase 3 で対応） | Phase 3 着手時 | **決定: (b)** Phase 1 は `KabuStationStock` 1 variant のみ（U2） |
| Q-K5 | `GET /board` が自動 PUSH 登録を発火する仕様（SKILL R6）と RegisterSet 集計の整合 | (a) 暗黙 evict / (b) `KabuRegisterFullError` 投げる | Phase 0 出口 | **決定: (b)** — 既存登録 hit なら `touch()` のみ、新規 + 満杯時は `KabuRegisterFullError` を投げユーザーに登録解除を促す（U24） |
| Q-K6 | CI demo ジョブが構築不可（Windows 限定）。HTTPXMock での疑似 E2E をどこまで作るか？ | 最小限 / 完全 mock | K8.5 着手時 | **決定: `pytest-httpx` + WebSocket mock** で全 9 テストファイルを構築。本物 API は CI では叩かない |
| Q-K7 | プロセスメモリ寿命中のトークン保持 | プロセス再起動時は毎回 `/token` 呼び直し | Phase 0 出口 | **決定: プロセス再起動跨ぎは毎回 `/token` 取得。起動中はメモリに保持** — ファイルキャッシュは作らない |
| Q-K8 | 立花の `MarketKind::Stock` を流用するか、kabu 用に別 variant を作るか？ | (a) 共有（`MarketKind::Stock`） / (b) 新設（`MarketKind::JPStock`） | K1 着手前 | **決定: (a) 共有** — 立花も kabu も同じ `MarketKind::Stock` を使う。東証だけなら区別不要 |

## 決定済み

- [x] **Q-K4**: Phase 1 は `KabuStationStock` 1 variant のみ（Phase 3 で市場細分化）
- [x] **Q-K5**: `fetch_board()` で既存 hit なら `touch()` のみ、新規 + 満杯時は `KabuRegisterFullError`
- [x] **Q-K6**: HTTPXMock + WebSocket mock で全テスト
- [x] **Q-K7**: ファイルキャッシュなし、プロセスメモリのみ
- [x] **Q-K8**: `MarketKind::Stock` 共有（立花と同じ）

## Phase 2 着手前に確定必要な項目

- **DEV_KABU_TRADE_PASSWORD** の env 名予約（Phase 2 着手前に確定、取引パスワード env）✅ 確定・設定済み
- 発注時の取引パスワード収集 UI（tkinter subprocess 方式確定済み、UI 設計は Phase 2）✅ 確定
- `OrderAmendFailed.original_cancelled` を `Option<bool>` 化（`None` = 取消結果不確定）

| Q-P2-5 | 取引パスワード誤りのエラーコード | kabu API v1.5 ptal/error.html を確認し code を確定する | Phase 4 (P4-7) で部分解決。実機検証で確定 code 判明後に再 close | **部分解決 (2026-05-07 P4-7)**: kabu 公式 `ptal/error.html` には取引パスワード (`Password` フィールド) 誤り専用 code が**記載されていない**ことを確認。`4001013` は API パスワード誤り、`4002013` は **MarginTradeType param error** で別物。当初 4002013 をプレースホルダーとして使っていたが誤検出リスクあり (`MarginTradeType` 違反が lockout カウンタを増やす副作用) のため除去。`check_response()` を**メッセージ文字列検出**（"パスワード" + "誤"/"不正" or 英語 "Password is invalid"）に切替。実機運用で確定 code 観測次第、code 比較へ切替予定 |

## Phase 2 設計決定済み

| # | 論点 | 決定 |
| :-- | :--- | :--- |
| Q-P2-1 | 取引パスワード収集タイミング | **セッション開始時 1 回**（立花の `TachibanaSessionHolder` と同じ設計）。発注ごとに毎回聞かない |
| Q-P2-2 | 取引パスワードの保持方式 | メモリのみ。30 分 idle で自動クリア。3 回連続誤入力で 30 分 lockout（立花踏襲） |
| Q-P2-3 | DEV_KABU_TRADE_PASSWORD env | 設定時は tkinter ダイアログをスキップ（DEV_KABU_API_PASSWORD と同じ流儀） |
| Q-P2-4 | 取引パスワード保持クラス名 | `KabuTradePasswordHolder`（`TachibanaSessionHolder` の kabu 版） |

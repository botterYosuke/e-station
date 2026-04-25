# 立花注文機能: Open Questions

## 着手前に確定すべき事項

### Q0. nautilus_trader バージョンを何で固定して型を写すか
本計画は「nautilus 互換」を不変条件にしているが、nautilus は活発開発中で `OrderType` enum の値や field 名がマイナーバージョンで変わりうる。
- 案 A: `nautilus_trader == 1.211.x` を Tpre 着手時に pin し、その時点のソースから型を抽出してハードコード。N2 移行時に最新版へ追従
- 案 B: Tpre 段階でリリース版の最新 stable を採用（本日時点での latest）
- 案 C: 型互換チェック CI を組む（nautilus を CI 環境にだけ install し、`NautilusOrderEnvelope` ↔ `nautilus_trader.model.orders.Order` の dict ラウンドトリップを毎日叩く）

**推奨**: 案 A + 案 C の併用。pin したバージョンに対する互換性を CI で常時保証する。



### Q1. 第二暗証番号の永続化方針 ✅ 確定（2026-04-25）

**決定**: **案 D** — メモリのみ。**初回発注時に iced modal で取得** → 同一プロセス中はメモリ保持 → セッション切れ・forget API・プロセス終了でクリア。

- keyring 永続化は **採用しない**（opt-in も提供しない）
- 詳細は [architecture.md §5](./architecture.md#5-第二暗証番号の取扱い)
- 検討時の選択肢:
  - 案 A: keyring 永続化 → OS 侵害時の被害が大きすぎるため不採用
  - 案 B: 毎回入力 → UX 破綻のため不採用
  - 案 C: keyring + N 時間揮発 → 案 A の懸念を完全には解消しないため不採用

### Q2. `client_order_id` の発行元
- 案 A: HTTP API クライアント（curl ユーザー / Python SDK）が UUID を生成して送る → Rust 側は受け取った値を idempotency key として使う（**flowsurface の方針**）
- 案 B: Rust API 側で UUID を生成し、レスポンスで返す（idempotent re-submit ができない）

flowsurface に倣い **案 A** が筋だが、UI 側で発注フォーム経由の場合は **iced 側で UUID を生成する**実装になる。これを徹底できるか。

### Q3. 発注 UI を iced に出すか Python tkinter に出すか
- 立花のログインダイアログは tkinter（[docs/plan/tachibana/](../tachibana/)）
- 発注フォームを tkinter にすると Python 単独モード方針との整合は取れるが、**チャートと隣接した UX にしづらい**（別ウィンドウ）
- 発注フォームを iced に出すと UX は良いが、暗号資産 venue の発注 UI を将来追加する際の流儀と揃える必要が出る

**推奨**: 注文一覧・確認モーダル・発注フォームは **iced 側**で書く。tkinter はログイン専用とする。確認したい。

### Q4. 注文確認モーダルの強制範囲
- 案 A: 全注文で確認モーダル（堅い、UX は鈍い）
- 案 B: 成行のみ確認、指値はそのまま発注
- 案 C: 起動 config で個別ユーザーが選択

実弾の手戻りができないことを考えると **デフォルトは案 A、上級者は config で OFF** が妥当か。

### Q5. EVENT EC フレームの仕様根拠
- `api_event_if_v4r7.pdf` / `api_event_if.xlsx` は `manual_files/` に同梱されていない（SKILL.md L39 参照）
- Phase O2 着手前に **PDF 入手 or 実 frame キャプチャ or flowsurface パーサ移植**のいずれかが必要
- **対応**: [implementation-plan Tpre.5](./implementation-plan.md#tpre5-event-ec-フレームの仕様根拠を確保q5phase-o2-ブロッカ解消) に O-pre タスクとして昇格済み（O2 着手の前提条件）

### Q6. 発注 HTTP API の認証
- Phase 1 既存の `/api/replay/*` と同じトークンガードに乗せれば良いか
- それとも発注は **追加の confirmation token**（短期使い捨て）を必須にすべきか
- Phase O0 では既存ガード踏襲で着手し、O1 で見直すのが現実的

### Q7. flowsurface 側の冪等性マップ実装の写し方
- `flowsurface/src/api/agent_session_state.rs` は **agent session per replay session** が前提
- 立花注文には replay session 概念はない（実弾は 1 つの口座 = 1 セッション）
- そのまま写すと過剰な抽象になるため、**`OrderSessionState` は singleton（プロセスごと 1 つ）**で簡略化する想定。これで良いか確認

### Q8. 本番接続を許可するときのガード
- `TACHIBANA_ALLOW_PROD=1` だけで本番発注を許すのは弱い
- 案 A: env + 起動時 CLI 引数 `--allow-prod-orders` の併用
- 案 B: env + UI でユーザーが「本番モードを有効化」をチェック（不可逆 / 起動ごとに再確認）
- 案 C: 本番モードは別バイナリ（`flowsurface-live.exe`）として配布、デバッグビルドでは本番を絶対叩けない

少なくとも **案 B 相当の UI 確認**は入れたい。

---

## 着手後に決めれば良い事項

- 監査ログのローテーション戦略（日次 / サイズベース / 圧縮）
- 注文一覧 UI の表示密度・ソート順・フィルタ
- 約定 toast の表示時間・読み上げ（音声通知）
- NISA 口座の枠管理 UI（Phase O4）
- 複数アカウント対応の必要性（Phase O5+ 検討材料）

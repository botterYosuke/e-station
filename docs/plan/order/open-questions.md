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

### Q2. `client_order_id` の発行元 ✅ 確定（2026-04-25）

**決定**: **案 A** — クライアント側で UUID v4 を生成して送る（flowsurface 流）。Rust 側は受け取った値を idempotency key として使い、独自採番しない。

- iced 発注フォームは送信時に `Uuid::new_v4()` を生成する（Rust 側責務）
- curl / HTTP クライアント直叩きは送信側責務
- 詳細は [spec.md §4](./spec.md#4-公開-apihttp)「`client_order_id` 発行元」節

### Q3. 発注 UI を iced に出すか Python tkinter に出すか ✅ 確定（2026-04-25）

**決定**: **iced 側**。注文一覧・確認モーダル・発注フォーム・第二暗証番号入力モーダルはすべて iced で実装。tkinter はログイン専用とする。

- 理由: Q1 の「第二暗証番号入力も iced modal」決定と一貫性を保つ。チャートとの隣接 UX を優先
- 詳細は [implementation-plan.md T1.4](./implementation-plan.md#t14-ui-注文一覧パネル)

### Q4. 注文確認モーダルの強制範囲 ✅ 確定（2026-04-25）

**決定**: **案 A をデフォルト（全注文確認）、config で無効化可**。

- `tachibana.order.require_confirmation = true`（起動 config、デフォルト `true`）
- `require_confirmation = false` に設定した場合のみモーダルを省略可能
- 理由: 実弾取引で手戻りが効かないため。上級者は設定で緩和できる

### Q5. EVENT EC フレームの仕様根拠
- `api_event_if_v4r7.pdf` / `api_event_if.xlsx` は `manual_files/` に同梱されていない（SKILL.md L39 参照）
- Phase O2 着手前に **PDF 入手 or 実 frame キャプチャ or flowsurface パーサ移植**のいずれかが必要
- **対応**: [implementation-plan Tpre.5](./implementation-plan.md#tpre5-event-ec-フレームの仕様根拠を確保q5phase-o2-ブロッカ解消) に O-pre タスクとして昇格済み（O2 着手の前提条件）

### Q6. 発注 HTTP API の認証 ✅ 確定（2026-04-25）

**決定**: **Phase O0 は既存トークンガード踏襲。Phase O1 完了後に再評価**。

- 既存 `/api/replay/*` と同じ Bearer token（localhost-only バインドを維持）
- 追加 confirmation token は見送り（localhost-only の前提下では攻撃面が限定的）
- O1 完了後: 訂正・取消まで実装した時点でセキュリティ要件を再確認し、必要なら変更

### Q7. flowsurface 側の冪等性マップ実装の写し方 ✅ 確定（2026-04-25）

**決定**: **`OrderSessionState` は singleton（プロセスごと 1 つ）**で簡略化する。

- 立花注文には replay session 概念はない。`AgentSessionState` の per-session 抽象は不要
- `Arc<Mutex<OrderSessionState>>` を Axum `State` として渡す設計で十分
- 詳細は [architecture.md §4](./architecture.md#4-冪等性flowsurface-agent_session_staters-の移植)

### Q8. 本番接続を許可するときのガード ✅ 暫定確定（2026-04-25）

**決定**: **案 B（env + UI 確認）を Phase O0 に組み込む**。

- `TACHIBANA_ALLOW_PROD=1` env が未設定なら本番 URL への発注を Python URL builder でブロック（既存設計を維持）
- env が設定されている場合でも、アプリ起動時に「本番モードで起動しています。発注は実弾になります」ダイアログを iced で表示し、明示的な確認を求める（起動ごと）
- 案 C（別バイナリ）は将来検討。Phase O3 でユーザー増加時に判断

### Q9. 東証以外の市場コード写像（Phase O3 で対応）

- `instrument_id` の `<venue>` 部分が `TSE` 以外（`OSE`, `NSE`, `FKE` 等）のときの `sSizyouC` 写像表が未定義
- Phase O0〜O2 は `TSE` のみ受理（HTTP 層で 400 reject）
- O3 着手時に写像表を [architecture.md §10](./architecture.md#10-nautilus_trader-との型マッピング) に追記する

---

## 着手後に決めれば良い事項

- 監査ログのローテーション戦略（日次 / サイズベース / 圧縮）
- 注文一覧 UI の表示密度・ソート順・フィルタ
- 約定 toast の表示時間・読み上げ（音声通知）
- NISA 口座の枠管理 UI（Phase O4）
- 複数アカウント対応の必要性（Phase O5+ 検討材料）

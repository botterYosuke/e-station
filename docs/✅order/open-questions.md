# 立花注文機能: Open Questions

## 着手前に確定すべき事項

### Q0. nautilus_trader バージョンを何で固定して型を写すか ✅ 確定（2026-04-26）

**決定**: **案 A + 案 C の併用**。

- `nautilus_trader == 1.211.x` を参照バージョンとして pin し、ソースから型を抽出してハードコード（Tpre.1 で実施済み）
- 型互換チェック CI を `test_nautilus_order_envelope.py` のハードコード dict テストで担保
- N2 移行時に nautilus 本体を pyproject.toml に追加し、実際の import に切り替える
- 候補バージョン: nautilus_trader 1.211.x（Tpre.1 時点での field 構成を参照）

**Q10 も同時確定**: e-station 内 `docs/✅nautilus_trader/spec.md` を正本とし、
上流 nautilus_trader.model.orders.Order のソースは参照リンクのみとする（案 A）。



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

### Q5. EVENT EC フレームの仕様根拠 ✅ 解決（2026-04-28）

**解決**: samples による仕様代替で Phase O2 実装は完了済み。

- `api_event_if_v4r7.pdf` / `api_event_if.xlsx` は `manual_files/` に同梱されていない（SKILL.md L39 参照）
- **確認結果（2026-04-28）**: flowsurface `exchange/src/adapter/tachibana.rs` に EC 専用パーサは存在しない
- **採用した根拠**: `.claude/skills/tachibana/samples/e_api_event_receive_tel.py`（行 534–568）に EC フレーム仕様（`^A`/`^B`/`^C` デリミタ、p_evt_cmd 値一覧、EC=注文約定通知）が Python コメントで完全に記載されており、Phase O2 実装（`tachibana_event.py._parse_ec_frame`）はこれを根拠として完了済み
- 実 frame キャプチャ（デモ環境接続が可能になった際）は任意で追加可能だが、実装に必須ではない

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
- Tpre.2 着手前に `docs/✅python-data-engine/schemas/` ディレクトリ（または `schemas.py` のあるパス）の実在確認を必須化する（A-L3）

### Q10. nautilus_trader 用語の正本ファイル ✅ 確定（Q0 と同時確定, 2026-04-26）

**決定**: **案 A** — e-station 内 `docs/✅nautilus_trader/spec.md` を正本とし、
上流ソースは参照リンクのみ。Q0 の決定（案 A + C 併用）に統合済み。

---

### Q-CI-1. `cargo test --workspace` の CI ジョブ未設定 ✅ 解決（2026-04-28）

**解決**: `.github/workflows/rust-tests.yml` を新設。`dtolnay/rust-toolchain@stable` + `Swatinem/rust-cache@v2` + `cargo test --workspace` を `pull_request` + `push: branches: [main]` で実行するジョブを追加済み。

---

### Q11. 発注 E2E における第二暗証番号のヘッドレス注入方法（未決定）

**背景**: `.env` にデモクレデンシャルが揃い（2026-04-28 確認）、ログイン E2E (`tests/e2e/tachibana_demo_login.sh`) は実行可能。しかし発注 E2E は第二暗証番号が必要で、現在は iced modal 経由でしか入力できない設計（Q1 案 D）のためヘッドレス実行できない。

**選択肢**:

| 案 | 概要 | メリット | デメリット |
|---|---|---|---|
| **A** | 専用スクリプトが Python エンジンに直接 WebSocket 接続し `SetSecondPassword` + `SubmitOrder` を送信 | GUI 不要・CI 化可能 | エンジン直結なので Rust HTTP 層をスキップ |
| **B** | `DEV_TACHIBANA_SECOND_PASSWORD=xxx` env を Python 側 dev fast path として追加（ログインの `FLOWSURFACE_DEV_TACHIBANA_LOGIN_ALLOWED=1` と同じ思想） | フルスタック E2E が CI 化可能 | env に第二暗証番号が残る（開発用途限定でも管理コスト） |
| **C** | E2E スクリプトは Rust アプリ＋GUI を起動し、第二暗証番号は人手で modal に入力してから curl で発注 | 変更ゼロ | 自動化不可・手動操作が必要 |

**現時点のデフォルト**: 案 C（手動 GUI）で先に動作確認し、CI 自動化が必要になった時点で案 A または B を選択する。

---

### Q12. MarginCreditRepay/MarginGeneralRepay + Sell 時の `sTatebiType` デフォルト挙動（未確認）

**背景（2026-05-01 起票）**: Phase O3 で `sell_btn` に `.on_press` を追加した際、
`cash_margin` に `MarginCreditRepay` / `MarginGeneralRepay` を選んで「売り」を実行した場合、
`tachibana_orders.py` が `sTatebiType` をどのデフォルト値で立花 API へ送信するか未確認。

現行実装では `sTatebiType="*"`（一括返済）が既定値として送られる可能性がある。
意図せず一括返済が実行されるリスクがある。

**選択肢**:

| 案 | 概要 |
|---|---|
| **A** | `sTatebiType` の挙動を `tachibana_orders.py` で確認し、仕様として `architecture.md` に明記する |
| **B** | UI 側で `MarginCreditRepay` / `MarginGeneralRepay` + Sell の組み合わせ時に警告ダイアログを出す |
| **C** | Phase O3 スコープ外として、信用返済 UI 専用の計画（implementation-plan.md T3.x）で扱う |

**現時点のデフォルト**: 案 C で先送り。信用返済 UI の専用フェーズ着手前に案 A の調査を行う。
現物売り（`cash_margin=cash`）のユースケースには影響しない。

---

## 着手後に決めれば良い事項

- 監査ログのローテーション戦略（日次 / サイズベース / 圧縮）
- 注文一覧 UI の表示密度・ソート順・フィルタ
- 約定 toast の表示時間・読み上げ（音声通知）
- NISA 口座の枠管理 UI（Phase O4）
- 複数アカウント対応の必要性（Phase O5+ 検討材料）

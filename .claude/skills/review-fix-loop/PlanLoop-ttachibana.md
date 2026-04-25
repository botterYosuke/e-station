# PlanLoop 呼出 — 立花証券 Phase 1（認証・session・creds 経路）計画レビュー

> このファイルは `PlanLoop.md`（汎用ループ手法）を `docs/plan/tachibana/` に適用するための **bespoke 呼出ファイル**です。
> 手順本体は `.claude/skills/review-fix-loop/PlanLoop.md` を **必ず最初に Read** して従うこと。
> 本ファイルは「対象 / 前提資料 / 観点別の追加チェック / 案件固有の禁止事項」を上書き指定するだけ。

---

## 対象 area

```
docs/plan/tachibana/
├── README.md
├── spec.md
├── architecture.md
├── implementation-plan.md
├── data-mapping.md
├── inventory-T0.md
├── open-questions.md
└── review-fixes-2026-04-25.md   # 直前ラウンドの修正ログ（無ければラウンド 1 として新規作成）
```

スコープ: 立花証券 e支店 API（v4r7/v4r8）を使った認証・仮想 URL 管理・session ライフサイクル・creds 経路の Phase 1 計画。注文機能は別計画（`docs/plan/order/`）で扱うので本ループでは指摘対象外。

## 前提資料（一次資料・照合基準）

- **一次資料**: `.claude/skills/tachibana/SKILL.md` — R1〜R10 / EVENT 規約 / URL 形式 / Shift-JIS / p_no 規約。**SKILL が正**、計画文書側で上書きしない
- **立花サンプルコード**: `.claude/skills/tachibana/samples/` — 認証・URL 取得・EVENT 受信の挙動正本
- **既存実装（Phase 1 で着地済み）**:
  - `python/engine/exchanges/tachibana_auth.py`（`TachibanaSession`, `StartupLatch`, login/refresh ヘルパ）
  - `python/engine/exchanges/tachibana_helpers.py`（`PNoCounter`, `current_p_sd_date` 等）
  - `python/engine/exchanges/tachibana_url.py`（仮想 URL ビルダ）
  - `python/engine/exchanges/tachibana_codec.py`（Shift-JIS / `func_replace_urlecnode` / レスポンス解析）
  - `python/engine/exchanges/tachibana_login_flow.py` / `tachibana_login_dialog.py`（GUI 経由ログイン）
  - `python/engine/exchanges/tachibana_master.py`（マスタ取得）
  - `python/engine/server.py`（IPC ディスパッチ: `SetVenueCredentials` / `RequestVenueLogin` / supervisor 終端）
  - `data/src/config/tachibana.rs`（keyring / `TachibanaCredentials` / `TachibanaCredentialsWire` / F-H5 invariant）
  - `engine-client/src/`（IPC: `backend.rs` / `capabilities.rs` / `dto.rs` / `process.rs` の creds_refresh 経路）
  - `src/main.rs`（起動時 keyring 読込 → ProcessManager → `VenueCredentialsRefreshed` listener）
  - `data/tests/tachibana_keyring_roundtrip.rs` / `engine-client/tests/process_creds_refresh_hook.rs` 他（既存テスト pin）
- **依存先計画**:
  - `docs/plan/order/` — 本計画の認証・session・URL ビルダ・codec を再利用する後続フェーズ。境界条件の整合確認用
  - `docs/plan/nautilus_trader/` — 将来の置換対象（live execution は order/、本計画 Phase 1 では扱わない）
- **直前修正ログ**: `docs/plan/tachibana/review-fixes-2026-04-25.md`（重複指摘除外。無ければラウンド 1 として新規作成）

## 観点ごとの追加チェック項目（PlanLoop.md §5 への上書き）

各サブエージェントのプロンプトに以下を追加する。

### 観点 A 文書間整合性 追加
- README / spec / architecture / implementation-plan / data-mapping / inventory-T0 / open-questions / SKILL の間で矛盾・旧表記
- 用語の揺れ（「session」「セッション」「仮想 URL」「sUrl*」混在 / `p_no` `pNo` `next_p_no()` 表記揺れ）
- フェーズ番号（Phase 1 の T0/T1/T2/T3 サブタスク）が他計画（order/ の Phase O0〜O3、nautilus_trader/ の Phase N1〜N3）と混同されていないか
- 章節リンクのアンカー死活（`Grep '\[.*\]\(\./.*\.md#'` で抜き出し見出し実在を確認）

### 観点 B 既存実装・依存計画とのズレ 追加
- 計画記述の関数名・モジュール名・型名が `python/engine/exchanges/tachibana_*.py` / `data/src/config/tachibana.rs` / `engine-client/src/` の現状と一致しているか
- F-H5（`second_password.is_none()` 強制）/ R6 / `StartupLatch` / `TachibanaSession` / `PNoCounter` の **シンボル名・所在モジュール**を grep で照合
- 既存テスト（`data/tests/tachibana_keyring_roundtrip.rs` / `engine-client/tests/process_creds_refresh_hook.rs` / `python/tests/test_tachibana_*.py`）が pin している不変条件と計画記述の整合
- **行番号参照は陳腐化前提**でシンボル名参照に置換させる
- `docs/plan/order/` の依存宣言（再利用するモジュール）と本計画が予告する API が一致しているか
- 立花サンプルコード（`.claude/skills/tachibana/samples/`）と本計画の挙動規定の食い違い

### 観点 C 仕様漏れ・設計リスク 追加
- **SKILL R1〜R10 全網羅チェック**:
  - R1 実弾保護 / R2 EVENT URL 形式 / R3 永続化規約 / R4 p_no 採番 / R5 sJsonOfmt / R6 業務エラー判定 / R7 Shift-JIS / R8 マスタ / R9 URL エンコード / R10 仮想 URL 秘匿 + 第二暗証番号
- 仮想 URL マスク（WAL / ログ / `reason_text` / クラッシュレポート / panic backtrace）
- Shift-JIS リクエスト・レスポンス両方向のパイプライン規定
- `p_no` 採番（Unix 秒初期値・wall-clock 単調増加・プロセス再起動またぎ）
- 第二暗証番号: メモリ滞留期間 / forget トリガー / lockout 規約 / IPC payload 不在の serializer assert
- session 切れ即停止（`p_errno=2` 検知時の `OrderSessionState` 凍結伝播範囲）
- supervisor 終端規約（`StartupLatch` 2 度呼出 → `os._exit(2)` + creds 漏洩なし）
- keyring 読込・refresh 経路（`SetVenueCredentials` / `VenueCredentialsRefreshed`）の race condition
- `_compose_request_payload` の責務（JSON 構造文字 `{}":,` 非エンコード規約）
- `sKinsyouhouMidokuFlg` ガードの所在
- config キー名がドキュメントに明示されているか（実装で骨抜きになる温床）

### 観点 D テスト不足 追加
- T0/T1/T2/T3 の各実装タスクに対し、受け入れ条件・単体・結合・E2E・回帰テストの**観測点**（実行コマンド・テストファイル名・assert 内容）が明記されているか
- 既存テスト（`tachibana_keyring_roundtrip.rs` / `process_creds_refresh_hook.rs` / `test_tachibana_main_dev_flag.py` / `test_tachibana_login_unexpected_error.py`）が pin している MEDIUM-D2-1（StartupLatch escalation）等の不変条件が計画タスクに紐付いているか
- F-H5（`second_password=None` 強制）の Rust 単体テスト pin が計画に記載されているか
- 仮想 URL マスクの horizontal grep テスト（caplog / WAL / 構造化ログ）の観測点
- supervisor 終端テスト（`os._exit(2)` 経路）が pin されているか
- nautilus 互換境界 lint（禁止語 grep の CI ゲート組込）が `.github/workflows/*.yml` で明記されているか
- `invariant-tests.md` 等の不変条件 ID ↔ test 関数名対応表

## 案件固有の禁止事項（PlanLoop.md §6 に追加）

- **本計画は立花証券単独スコープ**（README.md 長期方針）。他 venue（暗号資産等）への汎用化要望は出さない
- 注文機能（`docs/plan/order/`）の実装詳細を本計画に書き戻してはいけない（**本計画は認証・session・creds 経路まで**）
- nautilus_trader 関連（live execution / SimulatedExchange / REPLAY 仮想注文）の実装詳細を本計画に書き戻してはいけない
- SKILL.md の一次資料（R1〜R10、EVENT 規約、URL 形式、Shift-JIS、p_no 規約）を計画文書側の記述で上書きしてはいけない（SKILL.md が正）
- F-H5 invariant（Phase 1 では `TachibanaCredentialsWire.second_password` を常に `None`）を計画文書側で緩和しない（解除は order/ Phase O1 のスコープ）
- 立花用語（`sCLMID` / `p_eda_no` / `sUrl*` 等）を IPC / HTTP API / Rust UI 層の field 名・型名に漏出させない（注釈での「= 立花 ...」併記は可）

## 起動

`PlanLoop.md` の §0 起動チェック → §Step 1〜Step 5 のループに従って実行する。

---

参考: 過去の order 計画ループ（6 ラウンドで全観点収束）の修正ログ `docs/plan/order/review-fixes-2026-04-25.md` は同手法の代表的な実例。Finding ID 命名規則・ラウンド推移・統一決定の運用例として参照できる。

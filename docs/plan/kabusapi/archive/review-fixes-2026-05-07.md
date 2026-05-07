# kabuステーション API 統合プラン レビュー修正ログ

対象: `docs/plan/kabusapi/{README,plan,comparison}.md`
スキル: `/review-fix-loop` + `/e-station-review`（PlanLoop モード）
開始日: 2026-05-07
状態: **収束（Round 3、HIGH=0 / MEDIUM=0、LOW のみ残存）**

## 推移サマリ

| ラウンド | HIGH | MEDIUM | LOW | 観点投入 | 状態 |
|---|---|---|---|---|---|
| R1 初回 | 13 | 22 | 14 | 4 並列（A/B/C/D） | 修正実施 |
| R2 再レビュー | 5 | 9 | 10 | 3 並列（A+D/B/C） | 修正実施 |
| R3 サニティ | 0 | 0 | 5 | 1 体合算 | **収束** |

繰越: なし。残存 LOW 5 件はいずれも任意改善（comparison §10 capabilities 一次ソース注記の付記など）。

## 残存 LOW（対応不要、Phase 1 着手時に拾えば可）

1. comparison §10 capabilities 行末に「（一次ソース: plan §1.1）」注記追加で保守容易化
2. plan §1.2 `kabusapi_auth.py` 行のエラー型脚注に `4001001 / 4001005` を併記すると一次参照が plan 側にも生まれる
3. その他は確認結果の報告のみ（用語残存ゼロ・アンカー実在・文書集合完全一致）



## ラウンド 1（2026-05-07）

### 統一決定

- **U1**: IPC venue 文字列キー = `"kabu_station"`（Rust `Venue::KabuStation` と命名整合）
- **U2**: Phase 1 は `Exchange::KabuStationStock` 1 バリアントのみ（市場細分化は Q-K4 維持）
- **U3**: `LiveSession` の所在は `python/engine/replay_session.py`（`live_session.py` は実在しない）
- **U4**: 取引パスワード収集 UI は tkinter subprocess 統一（iced modal 不可）
- **U5**: localhost URL 表記は `localhost:18080`（本番）/ `localhost:18081`（検証）に統一
- **U6**: PUSH 再接続後は常に `RegisterSet` 全件 re-register をデフォルト挙動
- **U7**: 後続 5 文書（spec / architecture / data-mapping / implementation-plan / open-questions）は Phase 1 着手前必須
- **U8**: crate ディレクトリは `engine-client/`、use path は `engine_client::dto::...`
- **U9**: SCHEMA_MINOR bump の真の変更点は `Venue` enum + `Venue::from_str` + `AdapterHandles` dispatch（dto.rs venue 文字列 shape は不変）
- **U10**: `apply_after_handshake` は venue-agnostic、kabu 分岐不要
- **U11**: 立花計画への参照は節タイトル併記でアンカー死活を回避
- **U12**: URL リテラル lint を Phase 1 K-task に追加（CI 強制）
- **U13**: `invariant-tests.md` を新規追加し R1-R10 × test 関数マトリクスを起票
- **U14**: 取消成功+再発注失敗時は `OrderAmendFailed{original_cancelled=true}`、自動再試行なし
- **U15**: RegisterSet evict 時は IPC `SubscriptionEvicted{symbol}` を送出
- **U16**: token 期限切れ retry 1 回失敗 → `VenueError{code:"token_expired"}` + tkinter 再ログイン誘導
- **U17**: TCP refused → 5s backoff × 3 回 → `VenueError{code:"local_app_down"}`
- **U18**: plan.md §1.1 の `Exchange::KabuStation*` 注記「先物・OP は **Phase 2 以降**」を「**Phase 3 以降**」に訂正（§Phase 3 と整合）
- **U19**: plan.md §1.4「T0/T1/T2」表記を「Phase 0/1/2」に統一（フェーズ番号体系の二重化排除）
- **U20**: README.md「発注は v2 以降」→「発注は Phase 2 以降」に統一（venue API バージョン v1.5 との混同回避）
- **U21**: 立花計画への章節リンクは GFM アンカー（`#見出し-スラグ`）を必ず付与し、ファイル先頭着地を回避

### Findings 集計

| 観点 | HIGH | MEDIUM | LOW |
|---|---|---|---|
| A 文書間整合性 | 3 | 8 | 5 |
| B 既存実装ズレ | 2 | 3 | 2 |
| C 仕様漏れ・設計リスク | 4 | 6 | 4 |
| D テスト不足 | 4 | 5 | 3 |
| **合計** | **13** | **22** | **14** |

### Finding 一覧

| ID | 観点 | 対象 | 概要 | 修正方針 |
|---|---|---|---|---|
| A-H1 | A | README.md:32 | 立花 README §長期方針 アンカー死活 | 節タイトル併記 / アンカー削除 |
| A-H2 | A | plan.md:9,75,153 | architecture.md §1/§2 引用節番号不整合 | 節タイトル併記（U11） |
| A-H3 | A | README.md:5 / plan.md:3,153 | `docs/✅tachibana/` パス表記 3 形式混在 | `../../✅tachibana/` で統一 |
| A-M1 | A | plan.md:140 | `[open-questions.md]` リンク先未指定 | `[open-questions.md](./open-questions.md)` |
| A-M2 | A | README.md:11 vs plan.md:71-76 | 文書構成表不一致 | README に「後続作成予定」追加 |
| A-M3 | A | plan.md:84-85 | Phase 0 出口判定が曖昧 | 「Phase 1 着手前完了」明記（U7） |
| A-M4 | A | comparison.md:9 / plan.md:14 / README.md:36 | localhost URL 表記揺れ | U5 で統一 |
| A-M5 | A | plan.md:28 / comparison.md:60 | enum 名と Exchange 数値の対応欠落 | U2 採用で再整理、対応表追加 |
| A-M6 | A | plan.md:29-30,132 §3 | engine-client / engine_client 表記混在 | U8 で統一 |
| A-M7 | A | plan.md:30,64 | venue キー文字列 `"kabu"` vs enum `KabuStation` 命名規則ズレ | U1 採用 |
| A-M8 | A | comparison.md:131 / plan.md:142 | Q-K1 と §7 のクロスリンク欠落 | 相互リンク |
| A-L1 | A | README.md:14-17 | 一次資料リンク存在確認手順なし | チェックリスト追加 |
| A-L2 | A | plan.md:69 | `data-mapping.md` 雛形元欠落 | kabu 固有である旨注記 |
| A-L3 | A | plan.md:68 / comparison.md:99-101 | `kabusapi_master.py` 不要方針のクロスリンク欠落 | 相互参照 |
| A-L4 | A | plan.md:151 | チェックリスト位置付け曖昧 | 「Phase 1 着手前」明示 |
| A-L5 | A | comparison.md:140 | sanity check `§10` ハードコード参照 | 節タイトル参照 |
| B-H1 | B | plan.md:65,134 | `python/engine/live_session.py` が実在しない | U3 採用、`replay_session.py` に修正 |
| B-H2 | B | plan.md:51,56 | `tachibana_rest.py` 不存在、1:1 主張齟齬 | `tachibana.py + tachibana_orders.py` に訂正 |
| B-M1 | B | plan.md:28 | Exchange enum 粒度ポリシー逸脱 | U2 採用（1 バリアント） |
| B-M2 | B | plan.md:29 | dto.rs SCHEMA_MINOR bump 誤誘導 | U9 採用、Venue enum + dispatch に書き換え |
| B-M3 | B | plan.md:133 | `apply_after_handshake` 分岐記述誤り | U10 採用 |
| B-L1 | B | plan.md 全体 | 行番号参照陳腐化 | B-H1 解消で自然解消 |
| B-L2 | B | comparison.md:114 | `:19876` ハードコード前提 | 「engine が確保するポート」と書換 |
| C-H1 | C | plan.md:111-117 / README.md:33-37 | 取引パスワードを iced modal で取得は R10 違反 | U4 採用 |
| C-H2 | C | plan.md 全体 | URL リテラル lint タスク未起票 | U12 追加 |
| C-H3 | C | plan.md:142,96 | PUSH 再接続後の再登録挙動が未確定 | U6 採用 |
| C-H4 | C | plan.md:64 | token 期限切れ retry 後の挙動未定義 | U16 追加 |
| C-M1 | C | plan.md:146 (Q-K5) | `/board` GET の RegisterSet 計上ズレ | `kabusapi_rest.fetch_board()` で `RegisterSet.touch()` を必ず呼ぶ |
| C-M2 | C | plan.md:51 (K4) | LRU evict 通知経路未定義 | U15 採用 |
| C-M3 | C | plan.md:112 (K10) | 取消成功+再発注失敗時のロールバック未定義 | U14 採用 |
| C-M4 | C | README/plan | capabilities キー shape 未確定 | キー一覧を表形式で plan.md に追加 |
| C-M5 | C | plan.md:106 | 本体プロセス落ちの検知・復帰未定義 | U17 追加 |
| C-M6 | C | plan.md:66 | ログマスク lint 未起票 | `test_kabusapi_auth_logging.py` を K2 受入条件に追加 |
| C-L1 | C | plan.md:112 | 取引パスワード env 名衝突 | `DEV_KABU_TRADE_PASSWORD` を予約注記（Phase 2 着手前確定） |
| C-L2 | C | plan.md:148 | debug env 連打 UX | K3 受入に「env 設定時はダイアログを開かない」test 追加 |
| C-L3 | C | comparison.md:115 | Win Self-hosted runner 検討欠落 | open-questions.md に追記（Phase 0 出口） |
| C-L4 | C | plan.md:105 | 早朝強制ログアウトのバナー文言未定 | spec.md にバナー文言テーブル予約 |
| D-H1 | D | plan.md:66,92-99 | K1〜K13 観測点欠落 | U13（invariant-tests.md）+ 各 K-task に test ファイル名 + 代表 assert 併記 |
| D-H2 | D | plan.md:98,116 | K7/K13 E2E コマンド欠落 | `test_live_session_kabu.py::test_login_kabu_emits_venue_ready` 等を明記 |
| D-H3 | D | plan.md:99 | `kabu-mock.yml` 内容未定義 | job 名・トリガ・コマンド明記 |
| D-H4 | D | plan.md:66,95 | negative test 観測点欠落 | 50 銘柄超過 / token 期限切れ retry / 再接続再登録 / 流量超過 / ConnectionRefused の test 関数を K4/K5 に紐付け |
| D-M1 | D | plan.md:96 / comparison.md:90 | Q-K1 両分岐 parametrized test 欠落 | `test_reconnect_reregisters[server_drops_list-server_keeps_list]`（U6 で server_drops_list 側はデフォルト保証） |
| D-M2 | D | plan.md:96 (Q-K5) | `/board` 自動登録の RegisterSet 計上 test 欠落 | `test_board_get_increments_register_count` |
| D-M3 | D | comparison.md:51 | Side 値立花↔kabu 衝突 regression test 欠落 | `test_kabusapi_codec.py::test_side_mapping_kabu_buy_is_2_not_3` |
| D-M4 | D | plan.md:64 | SCHEMA_MINOR Rust↔Python 整合 test 未指定 | `test_schema_compat.py` + `cargo test -p engine-client schema_minor_kabu` |
| D-M5 | D | plan.md:116 | `KABU_ALLOW_PROD` 多層ガード test 観測点欠落 | `test_prod_guard_requires_both_envs` |
| D-L1 | D | comparison.md:15 | Shift-JIS 取り違え regression test 欠落 | `test_decode_rejects_sjis_bytes` |
| D-L2 | D | plan.md:66 | bootstrap test 観測点欠落 | `test_bootstrap_orders_snapshot` |
| D-L3 | D | README.md:17 / plan.md | 不変条件 ID × test 関数の対応表欠落 | U13（invariant-tests.md） |
| A-H1' | A | plan.md:28,109-120 | §1.1 注記「先物・OP は Phase 2 以降」と §Phase 3 が不整合 | U18 採用 |
| A-H2' | A | plan.md:75 | フェーズ番号「T0/T1/T2」が Phase 0..4 体系と二重化 | U19 採用 |
| A-M3' | A | README.md:22 | 「v2 以降」表記が venue API バージョン v1.5 と紛らわしい | U20 採用 |
| A-M1' | A | README.md:32 / plan.md:153 | 立花計画リンクの `#` アンカー欠落 | U21 採用 |
| A-L1' | A | plan.md:5 | 「§0. 配置原則」が立花 architecture.md §1 へのリンク化されていない | リンク化 |
| A-L2' | A | plan.md:29 / comparison.md §10 | `VenueLoginStarted` / `VenueLoginCancelled` DTO 拡張が plan.md のみで comparison.md §10 マッピングに無い | comparison.md §10 に追加 |

## ラウンド 2（2026-05-07）

### 統一決定（追加分）

U22〜U41（本ログの本文に列挙、内容は会話履歴参照）

### Findings 集計

| 観点 | HIGH | MEDIUM | LOW |
|---|---|---|---|
| A+D 合算 | 3 | 5 | 3 |
| B | 0 | 1 | 3 |
| C | 2 | 3 | 4 |
| **合計** | **5** | **9** | **10** |

### Finding 一覧

| ID | 観点 | 対象 | 概要 | 修正方針 |
|---|---|---|---|---|
| R2-A-H1 | A | README/plan §1.4 | 後続作成予定文書集合の不一致 | U33 |
| R2-A-H2 | A | comparison §2 / plan §1.3 | 「自動再発行」と「自動再ログイン禁止」の意味論衝突 | U31 |
| R2-A-H3 | A | plan Phase 0 vs §5 checklist | invariant-tests.md の起票点が二重 | U33 |
| R2-A-M1 | D | plan §1.3 | テストファイル一覧が K-task で参照される 5 件と不一致 | U34 |
| R2-A-M2 | D | plan K2 | `Code=4001001` の assert 漏れ | U35 |
| R2-A-M3 | A | README data-mapping 役割 | plan §1.4 と内容齟齬 | U36 |
| R2-A-M4 | A | plan K8 lint 範囲 | `python/` 配下が検査対象から漏れる | U28 |
| R2-A-M5 | A | plan §1.1 capabilities | shape 波及が README/comparison に届かず | comparison §10 / 設計原則に行追加、test ファイル名追加 |
| R2-A-L1 | D | comparison [^side-regression] | `test_kabusapi_codec.py` が §1.3 に無い | U34 で同時解決 |
| R2-A-L2 | A | README/plan の「50 銘柄」二重化 | comparison §7 一次参照に切替 | U38 |
| R2-A-L3 | A | Phase 4 runbook.md 未掲載 | §1.4 に追加 | U37 |
| R2-B-M1 | B | plan §1.3 / §3 LiveSession.login | 既存シグネチャ拡張の破壊的変更が読み取れない | U32 |
| R2-B-L1 | B | plan §3 apply_after_handshake | 内部実装名 `_inner` / `_with_timeout` 注記欠落 | U41 |
| R2-B-L2 | B | plan §1.1 AdapterHandles | `kabu_station` フィールド追加必須が暗黙 | U39 |
| R2-B-L3 | B | plan §3 RequestVenueLogin | Command 列挙子バリアントである旨注記欠落 | U40 |
| R2-C-H1 | C | WS 再接続無限ループ silent failure | 打ち切り条件未定義 | U22 |
| R2-C-H2 | C | OrderAmendFailed 三値表現 | `Option<bool>` で SCHEMA_MINOR 予約 | U23 |
| R2-C-M1 | C | fetch_board() 満杯時挙動未定義 | U24 |
| R2-C-M2 | C | local_app_down リトライと早朝強制ログアウト衝突 | U25 |
| R2-C-M3 | C | ログマスク test 範囲がトークン偏重 | U26 |
| R2-C-L1 | C | capabilities invariant test 不在 | U27 |
| R2-C-L2 | C | URL lint 正規表現が ws / 他パスを取りこぼす | U28 |
| R2-C-L3 | C | comparison §3 に SKILL R7 符号引用なし | U29 |
| R2-C-L4 | C | VenueError.code 名前空間衝突 test 欠落 | U30 |


# docs/plan/✅tachibana 全体 PlanLoop 修正ログ — 2026-04-26

対象: `docs/plan/✅tachibana/` 全計画文書
スキル: `.claude/skills/review-fix-loop/PlanLoop.md`
着手: 2026-04-26
ブランチ: `tachibana/phase-1/T4-B2`

> 本ログは PlanLoop ラウンド単位の Finding と修正概要を記録する。
> 実装コードではなく **計画書の整合修正のみ** が対象。

---

## ラウンド 1（2026-04-26）

### 統一決定
1. FDコード名: GAK*/GBK*（旧暫定）→ GAP*/GBP*（価格）、GAV*/GBV*（数量）、DPP_TIME → DPP:T、DDT → p_date
2. 気配本数: 「5本気配」→「10本気配」に全ファイル統一
3. tachibana_ws.py: 実ファイル未存在。architecture.md §4 の行に「（T5 で新設予定）」を付記（削除せず。SKILL.md 記載あり）
4. current_jst_yyyymmdd: 計画書の「tachibana_helpers.py に新設（推奨）」を「tachibana.py に実装済み、移動は B5 以降で繰越」に訂正

### Finding ID → 修正概要マッピング

| Finding ID | 観点 | 対象ファイル | 修正概要 |
|---|---|---|---|
| H-A1 | A | spec.md §4 | 旧FDコード名 GAK*/GBK*/DPP_TIME/DDT → GAP*/GBP*/DPP:T/p_date に置換 |
| H-A2 | A | spec.md L111 / impl-plan T5 L553,L572 | 「5本気配」→「10本気配」に置換（4箇所） |
| H-B1 | B | architecture.md §4 | tachibana_ws.py 行に「（T5 で新設予定）」を付記 |
| H-B2 | B | impl-plan — current_jst_yyyymmdd 節 | 「tachibana_helpers.py に新設（推奨）」→「tachibana.py に実装済み（L91）、移動は B5 以降繰越」に訂正 |
| C-H1 | C | impl-plan T1 func_replace_urlecnode 節 | JSON 文字列全体を一度 func_replace_urlecnode に通す旨と roundtrip テスト追加を明記 |
| C-H2 | C | architecture.md §2.3 / impl-plan T3 | VenueCredentialsRefreshed None=keyring 保持セマンティクスを両ファイルに明記 |
| D-H1 | D | invariant-tests.md | F-M8b エントリ追加（Tx=T5、test_tachibana_fd_trade.py::test_tick_rule_fallback_*） |
| D-H2 | D | invariant-tests.md | architecture.md §8.3 の6テストファイルに対応する F-Process-* ID 6件を新設 |
| M-A1 | A | impl-plan T5 L573,L582 | GAK1/GBK1 → GAP1/GBP1 に置換 |
| M-A2 | A | spec.md §4 | アンカー #113-ブロッカー扱いと対応方針b3-再オープン → #113-ブロッカー解消記録b3-クローズ に修正 |
| M-A3 | A | open-questions.md Q22 | GAK/GBK → GAP/GBP に訂正 |
| M-B1 | B | architecture.md §5 | proxy.rs 実在確認後、存在しない場合は代替パスに置換または T3 着手前チェックリスト注記追加 |
| M-B2 | B | invariant-tests.md F-M6a | 一次資料節 data-mapping.md §6 → §11 に修正 |
| M-B3 | A | spec.md §2.1 | 「5本気配」→「10本気配」に置換 |
| C-M1 | C | impl-plan T1 check_response 節 | sWarningCode は Phase 1 では logging.warning に流すが戻り値に影響しない旨を明記 |
| C-M2 | C | impl-plan T2 validate_session_on_startup 節 | session=None の cold start は validation スキップしてログインフローに直進を明記 |
| C-M3 | C | impl-plan T7 secret scan 節 | allowlist は tools/secret_scan_allowlist.txt を正本とし .sh/.ps1 両スクリプトが参照する方式を明記 |
| D-M1 | D | impl-plan T6 | テスト実行コマンドと CI ジョブ名を追記、F-Banner1 の Tx タスクを T6 に修正 |
| D-M2 | D | invariant-tests.md | F-L5/SKILL R1/F-Default-Demo の TBD を具体的テスト関数名に解消 |

### LOW 持ち越し（対応不要）
| Finding ID | 観点 | 内容 |
|---|---|---|
| L-A1 | A | impl-plan-T3.5.md 脚注「別 PR で同期予定」が実は解消済（対応不要） |
| C-L1 | C | DPG フィールドの is_buy 判定使用可否が未決（Phase 1 スコープ外として繰越） |
| D-L1 | D | T7 受け入れ A-3（10分連続稼働）の自動テスト未明記（手動確認で許容） |

---

## ラウンド 2（2026-04-26）

### 統一決定
1. impl-plan:678 リスク表 HIGH-2 行: 旧FDコード名を正式名に置換 + アンカー更新 + ✅ 解消マーク付記
2. architecture.md §7.5: VenueCredentialsRefreshed スニペットを §2.3 の正式定義（user_id/password/is_demo フィールド含む）と整合

### Finding ID → 修正概要マッピング

| Finding ID | 観点 | 対象ファイル | 修正概要 |
|---|---|---|---|
| R2-M1 | A+C | impl-plan:678 リスク表 | GAK*/GBK*/DPP_TIME/DDT → GAP*/GBP*/GAV*/GBV*/DPP:T/p_date + アンカー修正 + ✅ 解消済み（2026-04-26）付記 |
| R2-M2 | C | architecture.md §7.5 | VenueCredentialsRefreshed スニペットを §2.3 正式定義と一致させた |

### LOW 持ち越し（対応不要）
| Finding ID | 観点 | 内容 |
|---|---|---|
| R2-L1 | D | invariant-tests.md F-L5/SKILL R1/F-Default-Demo が「T2 未実装（テスト未追加）」状態（T2 着手時に解消予定） |

---

## 新セッション・ラウンド 1（2026-04-26）

### 統一決定
1. tachibana_ws.py は T5 着地済み扱い。「T5 で新設予定」→「実装済み（T5 で tachibana.py に配線）」
2. 新 invariant-tests.md エントリの Tx 列は T5 を基本とする
3. F-M5a の pin 関数名は test_tachibana_holiday_fallback.py の実在関数名に合わせる（計画側を実装に合わせる）
4. impl-plan の B2 完了行に min_ticksize 解決を統合、B5 は「インクリメンタル検索 UI 配線のみ」に整理
5. spec.md §2.1 にインクリメンタル検索（matches_tachibana_filter、T4-B5）を 1 行追加

### Finding ID → 修正概要マッピング

| Finding ID | 観点 | 対象ファイル | 修正概要 |
|---|---|---|---|
| D-H3 | D | invariant-tests.md | HIGH-C3-1/HIGH-D5/MEDIUM-D6 エントリ追加（Tx=T5） |
| D-H4 | D | invariant-tests.md + impl-plan | F-M5a TBD解消、holiday_fallback.py 関数名を計画側に反映 |
| MEDIUM-1 | A | invariant-tests.md | F-Banner1 Tx T7→T6、test 関数名解決 |
| MEDIUM-2 | A | invariant-tests.md | F-M6a 一次資料節の誤参照修正 |
| MEDIUM-B3 | B | architecture.md | tachibana_ws.py「T5 で新設予定」→「実装済み」 |
| MEDIUM-B4 | B | implementation-plan.md | B5 follow-up と B2 完了記録を整理 |
| C-M-1 | C | spec.md + architecture.md | インクリメンタル検索仕様・設計記述を追記 |
| C-M-2 | C | architecture.md | §5 変更箇所表に tachibana_meta.rs / backend.rs 追記 |
| D-M3 | D | invariant-tests.md | ws_timeout エントリ追加（Tx=T5） |
| LOW-1 | A | README.md | implementation-plan-T3.5.md の説明追加 |
| LOW-B5 | B | invariant-tests.md | SKILL R8 pin 列 TBD解消 |
| C-L-1 | C | open-questions.md | Q9 決定済みマーク追記 |
| D-L2 | D | implementation-plan.md | Python テスト CI 組込を T7 に明記 |

### LOW 持ち越し（対応不要）
なし（全 LOW 今ラウンドで対応済み）

---

## 新セッション・ラウンド 2 収束確認（2026-04-26）

1件 MEDIUM 残存 → ラウンド3で即時修正。

## 新セッション・ラウンド 3（2026-04-26）

### Finding ID → 修正概要マッピング

| Finding ID | 観点 | 対象ファイル | 修正概要 |
|---|---|---|---|
| MEDIUM-R3-1 | A+B | invariant-tests.md L36 + L47 | F-M6a および SKILL R2 行の一次資料節を `SKILL.md R2` に、テストパスを `test_tachibana_auth.py::test_login_rejects_non_wss_event_url` に修正 |

---

## 完了サマリ（新セッション）

```
=== 完了 ===
全ラウンド数: 3（ラウンド1: 修正、ラウンド2: 収束確認、ラウンド3: 残存1件修正）
修正した Finding 総数: HIGH 2 / MEDIUM 8 / LOW 4
残存 LOW（対応不要）: 0件

主要な反映成果:
- tachibana_ws.py: T5 着地済みとして architecture.md §4 を更新
- architecture.md §5: tachibana_meta.rs / backend.rs / tickers_table.rs の3行追加
- spec.md §2.1: インクリメンタル検索（matches_tachibana_filter、T4-B5）を追記
- invariant-tests.md: HIGH-C3-1/HIGH-D5/MEDIUM-D6/HIGH-D2-1-WsTimeout エントリ追加（4件）
- invariant-tests.md: F-M5a/F-Banner1/SKILL R8/F-M6a/SKILL R2 の TBD・誤参照解消
- implementation-plan.md: B2/B5 完了記録整理、holiday_fallback テスト関数名修正、T7 CI 組込タスク追記
- open-questions.md: Q9 決定済みマーク追記
- README.md: implementation-plan-T3.5.md の説明追加
ログ: docs/plan/✅tachibana/review-fixes-tachibana-docs-2026-04-26.md
```

---

## 完了サマリ（初回セッション）

```
=== 完了 ===
全ラウンド数: 3（ラウンド1: 修正、ラウンド2: 修正、ラウンド3: 収束確認）
修正した Finding 総数: HIGH 8 / MEDIUM 13 / LOW 3（対応不要）
残存 LOW（対応不要）: 4件

主要な反映成果:
- FDコード名: GAK*/GBK*/DPP_TIME/DDT を GAP*/GBP*/GAV*/GBV*/DPP:T/p_date に全ファイル統一
- 気配本数: 「5本気配」→「10本気配」を spec.md/impl-plan 全箇所で統一
- tachibana_ws.py: 実在しないことを明記、T5 新設予定を architecture.md §4 に付記
- VenueCredentialsRefreshed None セマンティクス: architecture.md §2.3 + impl-plan T3 + §7.5 に明記
- invariant-tests.md: F-M8b 追加、F-Process-* 6件追加、F-M6a 節修正
- func_replace_urlecnode: JSON 全体一度通す規約を明記
- validate_session_on_startup: session=None cold start の条件分岐を明記
- secret_scan allowlist 方式を明記
ログ: docs/plan/✅tachibana/review-fixes-tachibana-docs-2026-04-26.md
```

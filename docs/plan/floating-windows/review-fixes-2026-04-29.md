# review-fixes 2026-04-29

`docs/floating-windows/` 計画書群（README.md / spec.md / architecture.md /
implementation-plan.md / open-questions.md）に対する review-fix-loop の修正ログ。

## レビュー重点（initiator 指定）

- Bevy 化スコープの限定（layout shell + 高頻度描画面のみ）が全文書で整合しているか
- spec §6 機能保持マトリクス（C1〜S1）が Phase 5 acceptance として強く効いているか
- architecture §3.5 pane 種別 3 分類が Phase 2 deliverable として明確か
- architecture §4.1 入力境界契約 INV-INPUT-1〜4 が抜け漏れなく書かれているか
- open-questions Q2/Q3 が抽象論のまま開いていないか
- Phase 2 終了時点で Q2/Q3/分類/入力契約が確定する依存関係が implementation-plan で表現されているか

---

## ラウンド 1（2026-04-29）

残存件数（投入時）: HIGH 8 / MEDIUM 14 / LOW 9

### 統一決定（全 implementer 共通）

- **UD1** src 行番号にシンボル名併記
- **UD2** PaneGrid::split 削除箇所は実機確認 8 箇所（dashboard.rs:948, 964 を追加）。記述を `Grep "panes.split("` 全ヒット表現に統一
- **UD3** architecture §4.1 → §4.5 の不連続を解消、現 §4.5 を §4.2 に繰上げ
- **UD4** spec §2 Phase 5 の「§4.6 入力契約」を「§4.1 入力境界契約」に修正
- **UD5** iced 残置文言を「**本計画ではスコープ外**。Bevy 化したい場合は **別計画として起票が必要**」で全文書統一
- **UD6** closing 状態 SoT は `data::Dashboard` 側に固定。auto-fail / 強制 close を追加
- **UD7** NF4 に `schema_version > 自分が知る最大値` も破棄して default 起動を明示
- **UD8** `tests/manual/floating-windows-CHECKLIST.md` を Phase 5 deliverable として正式化
- **UD9** tracing target = `flowsurface::floating_windows`、level = `INFO` で統一
- **UD10** §6.5 を TAS T1〜T3 / Starter S1〜S3 に分割、§6.6 Comparison、§6.7 popout を新設
- **UD11** Phase 3 暫定 `enum PaneLocation { Main(Uuid), Popout(window::Id, Uuid) }`
- **UD12** Phase 6 完了後 `rg "pane_grid" src/ data/src/ -t rust -l` 空 CI ゲート
- **UD13** INV-INPUT-5/6/7/8 を architecture §4.1 に追加
- **UD14** `host existing renderer` は Q1=(a) 同一 wgpu device 共存前提
- **UD15** iced::canvas host 困難リスクを Q2 / Phase 2 spike deliverable に明示
- **UD16** spec §2 Phase 3 に INV-REPLAY-1/2/3 を追加
- **UD17** Phase 6 acceptance に旧 saved-state 周知（一度だけ通知ログ + CHANGELOG）
- **UD18** Phase 4 行 assert に test 関数名 5 件（inv_input_*, inv_close_*）
- **UD19** `data::PaneKind::renderer_class()` + unit test を Phase 2 deliverable
- **UD20** Q5 数値判定（>120 → (c) / <60 → (d) 許容 / latency ≤ 16ms / 計測スクリプト）

### Findings 反映表

| Finding ID | 観点 | 対象 | 修正概要 | 適用 UD |
|---|---|---|---|---|
| C+F H1 | C+F | architecture §4.1 | INV-INPUT-5/6/7/8 追加 | UD13 |
| C+F H2 | C+F | spec §6.5/§6.6/§6.7 | TAS/Starter 分割 + Comparison + popout 追加 | UD10 |
| C+F H3 | C+F | spec §2 Phase 3 | INV-REPLAY-1/2/3 追加 | UD16 |
| C+F H4 | C+F | architecture §3.5 / Q2 | host existing renderer は Q1=(a) 前提 | UD14 |
| C+F H5 | C+F | Q2 / impl §2 Phase 2 | iced::canvas host 困難リスク + 実機判定 | UD15 |
| D H1 | D | spec / impl Phase 5 | 手動 CHECKLIST 正式化 | UD8 |
| D H2 | D | impl §4 Phase 4 | INV-INPUT 観測点 5 件 | UD18 |
| D H3 | D | impl §4 Phase 4 | INV-CLOSE 観測点 3 件 | UD18 |
| A M1 | A | architecture §4 | §4.5 → §4.2 番号繰上げ | UD3 |
| A M2 | A | spec §2 Phase 5 | §4.6 → §4.1 修正 | UD4 |
| B+E M1 | B+E | impl §3 / spec §2 Phase 6 | split 削除箇所 6→8 | UD2 |
| B+E M2 | B+E | spec §6 / impl §2 | 行番号にシンボル名併記 | UD1 |
| C+F M1 | C+F | spec §2 Phase 6 / README | 旧 saved-state 周知 | UD17 |
| C+F M2 | C+F | architecture §4.2 | closing 状態 SoT + auto-fail | UD6 |
| C+F M3 | C+F | architecture §2 / §4.1 / Q3 | iced 残置文言統一 | UD5 |
| C+F M4 | C+F | impl §2 Phase 3 | PaneLocation 暫定 enum | UD11 |
| C+F M5 | C+F | spec §6.7 | popout 経路機能保持 | UD10 |
| C+F M6 | C+F | spec NF4 / Q8 | schema_version 上限破棄 | UD7 |
| D M1 | D | architecture §3.5 / impl Phase 2 | renderer_class CI pin | UD19 |
| D M2 | D | spec / impl Phase 6 | tracing target 明示 | UD9 |
| D M3 | D | impl §4.1 CI | pane_grid 残存 grep CI | UD12 |
| D M4 | D | Q5 | 数値判定基準 | UD20 |

### 機械検証（Step 4）

- `Grep "§4\.6"` → 0 件（active docs）
- `Grep "§4\.5"` → 0 件（active docs。archive のみ）
- `Grep "INV-INPUT-[5678]"` → 4 件（architecture.md）
- `Grep "INV-REPLAY-[123]"` → 3 件（spec.md）
- `Grep "本計画ではスコープ外"` → 14 件 / 4 ファイル
- `Grep "現状 8 箇所"` → 2 件（spec / impl）
- `Grep "948|964"` → 1 件（impl §3 PaneGrid::split 列挙）

---

## ラウンド 2（2026-04-29）

残存件数（投入時）: HIGH 3 / MEDIUM 9 / LOW 3（R1: HIGH 8 / MEDIUM 14 から減少）

### 統一決定（Round 2）

- **UD21** closing SoT を「GUI Dashboard ランタイム状態」に訂正（R1 UD6 補強）
- **UD22** Q3 選択肢の差分を復元（共通プレフィクスは冒頭 1 回）
- **UD23** legacy-notified flag = `%APPDATA%\flowsurface\.legacy-notified-v1` 別ファイル
- **UD24** CHECKLIST 中身仕様（§6.1〜§6.7 全 ID × 4 列）
- **UD25** Q5 中間域（60〜120 events/s で (b) 8ms debounce default）
- **UD26** §6.7 popout に P5 / P6 追加
- **UD27** architecture §3.5 Comparison renderer_class 暫定 = `host existing renderer` 候補
- **UD28** Phase 4 行 INV-INPUT-5/6/7 観測点 4 件
- **UD29** Phase 3 行 INV-REPLAY 観測点 3 件
- **UD30** split 行シンボル名併記
- **UD31** §6.5 T2/T3 / §6.6 CMP1 unit test 名

### Findings 反映表

| Finding ID | 観点 | 対象 | 修正概要 | 適用 UD |
|---|---|---|---|---|
| A H1 (R2) | A | open-questions Q3 | 選択肢差分復元 | UD22 |
| C+F H1 (R2) | C+F | architecture §4.2 / spec | closing SoT を GUI Dashboard に訂正 | UD21 |
| C+F H2 (R2) | C+F | impl §4 Phase 3 | INV-REPLAY 観測点 3 件 | UD29 |
| B M1 (R2) | B | impl §3 / spec §2 Phase 6 | split 行シンボル名 | UD30 |
| B M2 (R2) | B | architecture §4.1 末尾 | iced UI 参照のシンボル名併記 | UD1 補強 |
| B M3 (R2) | B | open-questions Q2 | Heatmap/Kline シンボル名併記 | UD1 補強 |
| C+F M1 / D M1 (R2) | C+F+D | impl §4 Phase 4 | INV-INPUT-5/6/7 観測点 4 件 | UD28 |
| C+F M2 (R2) | C+F | spec §6.5 / §6.6 | T2/T3/CMP1 unit test 名 | UD31 |
| C+F M3 (R2) | C+F | spec §2 Phase 6 | legacy_notified flag 別ファイル | UD23 |
| C+F M4 (R2) | C+F | spec §6.8 | CHECKLIST 中身仕様 | UD24 |
| C+F M5 (R2) | C+F | open-questions Q5 | 60〜120 中間域 | UD25 |
| C+F M6 (R2) | C+F | spec §6.7 | popout P5 / P6 追加 | UD26 |
| A L1 (R2) | A | spec §2 Phase 6 | split 内訳併記 | UD30 |
| C+F L1 (R2) | C+F | spec §6.7 P4 | 参照範囲 enum 列挙 | UD26 |
| C+F L2 (R2) | C+F | architecture §3.5 | Comparison 暫定割当 | UD27 |

### 機械検証（Step 4）

- `replay_registry_starts_1to1` → impl Phase 3 行 hit
- `inv_input_5_keyboard` → impl Phase 4 行 hit
- `legacy-notified-v1` → spec §2 Phase 6 hit
- `GUI Dashboard` → architecture §1 / §4.2 で closing SoT として登場
- `tas_clears_on_symbol_change` / `comparison_series_add_remove_roundtrip` → spec §6.5 / §6.6 hit
- `Comparison chart pane.*host existing` → architecture §3.5 hit
- Q3 (a)(b)(c) 差分復元（86〜88 行）

---

## ラウンド 3（2026-04-29）

残存件数（投入時）: HIGH 0 / MEDIUM 2 / LOW 2

### 統一決定（Round 3）

- **UD32** spec §6.1 C5 を `INV-INPUT-5/6/7` に絞り、INV-INPUT-8 は non-goal と注記
- **UD33** impl-plan §2 Phase 2 列挙に Comparison chart 追加

### Findings 反映表

| Finding ID | 観点 | 対象 | 修正概要 | 適用 UD |
|---|---|---|---|---|
| M2 (R3) | C+F | spec §6.1 C5 | INV-INPUT-8 を non-goal 扱いに分離 | UD32 |
| L2 (R3) | A | impl-plan §2 Phase 2 | Comparison chart 列挙追加 | UD33 |

### 持ち越し（修正対象外）

- **R3-M1 split 6 vs 8 の commit log 乖離**: commit `0ec5c1d` のメッセージ本文は履歴で書換不可。**現行の active 文書群は全て 8 箇所で整合済**であり、commit log は過去時点の記録としてそのまま残置する。本計画着手後の commit から「8 箇所」表記に揃える。
- **L1 (R3) Phase 6 段落の括弧入れ子可読性**: 内容整合 OK。LOW のため次フェーズで対応判定。

### 機械検証

- `spec.md:170` C5 → `INV-INPUT-5/6/7` + `INV-INPUT-8 ... non-goal` を含む
- `implementation-plan.md:37` Phase 2 列挙 → `Comparison chart` 追加済み

---

## ラウンド 4（2026-04-29 / 最終収束サニティ）

残存件数（投入時）: HIGH 0 / MEDIUM 1 / LOW 2

### 統一決定（Round 4）

- **UD34** impl-plan §4 Phase 5 acceptance 列挙を spec §6 最終列挙に揃える（C1〜S1 → C1〜C5 / K1〜K4 / H1〜H2 / L1〜L2 / T1〜T3 / S1〜S3 / CMP1〜CMP2 / P1〜P6）

### Findings 反映表

| Finding ID | 観点 | 対象 | 修正概要 | 適用 UD |
|---|---|---|---|---|
| MEDIUM (R4) | A | impl-plan §4 Phase 5 行 | spec §6 最終列挙へ更新 + C5（INV-INPUT-5/6/7）参照追加 | UD34 |

### LOW（修正対象外 / 次フェーズ判定）

- impl-plan §4 Phase 2 行に Comparison 個別明示なし（R4 LOW2）
- 上記で C5 言及を追加したため、R4 LOW1（C5 参照欠落）はラウンド内で同時解消

---

## 完了サマリ

**全ラウンド数**: 5（R1 → R5）

**Findings 件数推移**:

| Round | HIGH | MEDIUM | LOW |
|---|---|---|---|
| R1 | 8 | 14 | 9 |
| R2 | 3 | 9 | 3 |
| R3 | 0 | 2 | 2 |
| R4 | 0 | 1 | 2 |
| R5 | 0 | 0 | 0 |

**収束**: 最終 R5 で HIGH/MEDIUM ゼロを確認。

**修正した Finding 総数**: HIGH 11 / MEDIUM 26 件解消。LOW は適宜同時解消、残存 2 件は次フェーズ判定。

**残存 LOW（対応不要 or 持ち越し）**:
- R3-L1: spec §2 Phase 6 段落の括弧入れ子可読性（内容整合 OK）
- R4-LOW2: impl-plan §4 Phase 2 行に Comparison chart の個別明示なし（Phase 2 列挙には含む）

**修正対象外（履歴のため不可変）**:
- R3-M1: commit `0ec5c1d` メッセージ本文の split 6 箇所表記。現行 active 文書群はすべて 8 箇所で整合済。今後の commit から「8 箇所」表記に揃える方針。

**主要な反映成果（規約レベル）**:

- **スコープ境界**: Bevy 化対象は **layout shell + 高頻度描画面（chart surface）に限定**。modal / 認証 / 管理画面は **本計画スコープ外（iced 残置・別計画起票が必要）** で全文書統一
- **Pane 種別レンダリング 3 分類**: `Bevy native` / `host existing renderer` / `keep iced overlay` を architecture §3.5 に確定。`data::PaneKind::renderer_class()` + unit test (`pane_kind_renderer_class_covers_all_variants`) で CI pin。Heatmap / Kline / Comparison = `host existing renderer` 候補（Q1=(a) 前提・iced::canvas 困難リスクを Phase 2 spike で実機判定）
- **入力境界契約 INV-INPUT-1〜8**: hit test 優先順位（iced overlay > Bevy pane chrome > Bevy chart surface > Bevy canvas）、keyboard / drag dead zone / wheel modifier / context menu / touch non-goal を architecture §4.1 に明文化
- **Lifecycle 不変条件**: INV-CLOSE-1（teardown 順序・5s timeout・closing 中 input 不可・auto-fail / 強制 close）、INV-REPLAY-1〜3（registry 1:1 / 種別変更 atomic / replay モード起動順序）。closing 状態 SoT は **GUI Dashboard ランタイム状態**（永続化しない）
- **機能保持マトリクス**: spec §6.1〜§6.7 に C1〜C5 / K1〜K4 / H1〜H2 / L1〜L2 / T1〜T3 / S1〜S3 / CMP1〜CMP2 / P1〜P6 を確定。`tests/manual/floating-windows-CHECKLIST.md` を Phase 5 deliverable として正式化（4 列・全 ID 必須）
- **永続化規約**: schema_version v1。NF4 に `> 自分が知る最大値` も破棄を明示。**`%APPDATA%\flowsurface\.legacy-notified-v1`** 別ファイルフラグで旧 saved-state 周知の重複防止
- **テスト観測点**: Phase 4 INV-INPUT/INV-CLOSE 9 件、Phase 3 INV-REPLAY 3 件、Phase 2 renderer_class 1 件、Phase 5 機能保持マトリクス全 ID + 手動 CHECKLIST。tracing target = `flowsurface::floating_windows`、level = INFO で統一
- **CI ゲート**: bevy-spike-build job（Phase 2）+ Phase 6 完了後 `rg "pane_grid" src/ data/src/ -t rust -l` 空チェック + e2e nightly job
- **数値判定**: drag dead zone 5px / Q5 同期粒度 (`>120 events/s`→(c) / `60〜120`→(b) 8ms debounce / `<60`→(d) 許容、latency ≤16ms) / 計測スクリプト `scripts/measure_window_moved_rate.sh`
- **削除規模**: `panes.split()` **8 箇所**（`src/main.rs:2538` + `src/screen/dashboard.rs:231/519/547/911/927/948/964`、各シンボル名併記）/ `pane_grid::Pane` 実コード ~39 箇所 / 6 ファイル
- **Q1〜Q9 決定タイミング**: Q1=Phase 2 完了 / Q2=Phase 3 着手前（3 分類で確定）/ Q3=Phase 3 着手前（iced 残置確定）/ Q5 数値化済 / Q4・Q6=Phase 5 着手前 / Q7=Phase 4 着手前 / Q8=Phase 6 完了時 / Q9=Phase 6 完了時

**ログ**: docs/floating-windows/review-fixes-2026-04-29.md






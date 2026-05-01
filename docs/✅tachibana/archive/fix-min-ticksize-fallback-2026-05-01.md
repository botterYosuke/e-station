# Ladder 交互 0 修正プラン — スパース表示への方針転換

**起票日**: 2026-05-01  
**起票者**: Claude (Opus 4.7)  
**症状**: 3000 円超の TOPIX100 銘柄で Ladder に「交互 0」が構造的に発生する  
**方針決定**: min_ticksize 修正ではなく **API レスポンスにある価格行のみを表示する（スパース表示）**

---

## 1. 問題の現状（実測）

### 1.1 実測した呼値テーブル（yobine_code='103'、全 TOPIX100 銘柄共通）

```
band[1]  kizun_price<=  1000  yobine_tanka= 0.1 円   decimals=1
band[2]  kizun_price<=  3000  yobine_tanka= 0.5 円   decimals=1
band[3]  kizun_price<= 10000  yobine_tanka= 1.0 円   decimals=0
band[4]  kizun_price<= 30000  yobine_tanka= 5.0 円   decimals=0
 ...
```

### 1.2 現在の動作（`diagnose_tachibana_depth_raw.py` 実行結果）

`resolve_min_ticksize_for_issue(issue_record, yobine_table, snapshot_price=None)` → **常に `bands[0].yobine_tanka = 0.1` を返す**

| 銘柄 | 現在価格 | 実呼値 | min_ticksize | Ladder step(×5) | 結果 |
|---|---|---|---|---|---|
| 9432 NTT | 151.9 円 | 0.1 円 | 0.1 円 | 0.5 円 | ✅ 正常 |
| 7203 トヨタ | 2998.0 円 | 0.5 円 | 0.1 円 | 0.5 円 | ⚠️ 偶然一致 |
| 9984 SoftBank | 5379 円 | 1.0 円 | 0.1 円 | 0.5 円 | ❌ 交互 0 |
| 8411 みずほ | 6705 円 | 1.0 円 | 0.1 円 | 0.5 円 | ❌ 交互 0 |

### 1.3 板データの実測（REST CLMMfdsGetMarketPrice）

SoftBank・みずほの板は**整数円価格のみ**（5379, 5378, 5377…）。  
Ladder が 0.5 円 step の連続グリッドで描画するため `.5` 行が**構造的に常に空 → 交互 0**。

```
【現在の表示（SoftBank ~5379）】
 ask 5380.5   0   ← 存在しない価格行
 ask 5380.0  2900
 ask 5379.5   0   ← 存在しない価格行
 ---（best ask/bid 境界）---
 bid 5379.0  6200
 bid 5378.5   0   ← 存在しない価格行
 bid 5378.0 10600
```

---

## 2. 根本原因と当初検討した修正案（採用しない）

**根本原因**: [`tachibana_master.py` の `resolve_min_ticksize_for_issue`（L200〜）](../../python/engine/exchanges/tachibana_master.py#L200) が  
`bands[0].yobine_tanka = 0.1` を返す（バンドの最初の要素 `bands[0].yobine_tanka`（L237）を返す）。  
`TickMultiplier(5) × 0.1 = 0.5 円` の連続グリッドが描画される。

**当初検討した修正**: スナップショット価格で `min_ticksize` を再解決し IPC で Rust に通知する  
（P1: Python fallback 修正 / P2: `TickerMetaUpdate` IPC 追加 / P3: Rust DTO + backend 変更）

**却下理由**: Python・IPC・Rust の 3 層変更が必要な割に、そもそも立花の 10 レベル板は疎な情報であり、  
「存在しない価格行を 0 で埋める連続グリッド」自体が立花の板の性質に合っていない。

---

## 3. 採用する修正方針 — スパース表示

**「API レスポンスに含まれる価格行のみを表示し、中間の空き行を描画しない」**

```
【修正後の表示（SoftBank ~5379）】
 ask 5389  10300
 ask 5388   8600
 ask 5387   8500
 ask 5386  10200
 ask 5385  12700
 ask 5384  10600
 ask 5383   8900
 ask 5382   6400
 ask 5381   4600
 ask 5380   2900
 ---（spread: 1 円）---
 bid 5379   6200
 bid 5378  10600
 ...
```

### 3.1 方針の利点・欠点

**利点**
- 交互 0 が構造的に消える
- min_ticksize・IPC・Rust DTO への変更が不要
- 立花の 10 レベル板（疎）の性質に素直
- 実装変更が Rust Ladder 描画のみに限定される

**欠点**
- 価格行間のギャップが視覚的に失われる  
  （例: best ask 1261.0 → 次レベル 1263.0 の 2 円ギャップを一目では確認できない）

### 3.2 スプレッド表示で補完

行間ギャップが見えない欠点は、既存の**スプレッド表示**（Ladder の `Spread: X.X` ラベル）で補完する。  
スプレッド値は `best_ask - best_bid` で計算済みのため追加実装不要。

スプレッドが極端に大きい場合の行間 gap ラベル補完は §7 将来対応に記載する。

---

## 4. 実装タスク

変更は **Rust Ladder 描画のみ**。Python / IPC / DTO に手は入れない。

### T1: Ladder 板表示から step ベース再集計を外し、スパース表示に切り替える

**ファイル**:
- [src/screen/dashboard/panel/ladder.rs](../../src/screen/dashboard/panel/ladder.rs)（メイン変更）
- [data/src/panel/ladder.rs](../../data/src/panel/ladder.rs)（GroupedDepth）

#### T1-A: raw depth を直接保持する（regroup_from_raw 迂回）

**現在の動作（問題箇所）**:  
`regroup_from_depth`（L181）が `GroupedDepth::regroup_from_raw(..., step)` を呼ぶ。  
`regroup_from_raw` は価格を `round_to_side_step(side, step)` で `step` 単位に丸め直す（ask: 切り上げ / bid: 切り下げ）。  
`step = min_ticksize(0.1) × 5 = 0.5` のとき、API から来た整数円価格が 0.5 刻みのバケツに入る。  
**このため `grouped_asks/grouped_bids` を直接イテレートしても「存在しない価格行を出さない」保証がない。**

**修正後の動作**:  
Ladder は **raw depth**（`depth.asks` / `depth.bids` — API から来た価格そのまま）を直接保持する。  
`regroup_from_depth` を raw コピーに置き換える。`GroupedDepth::regroup_from_raw` は板表示では呼ばない。

```rust
// 変更前: regroup_from_raw(&depth.asks, Side::Ask, step) → step 丸め済み BTreeMap
// 変更後: self.raw_asks.clone_from(&depth.asks)          → API 価格をそのまま保持
```

**step の責務分離**:  
`step` は `trades` の group 化（`TradeStore::insert_trades`）にのみ使用し続ける。  
板表示には使わない。将来の集計表示機能（step による行集約）は §7 将来対応として分離済み。

bids/asks が両方空の場合（板取得前・板クリア直後）は、イテレーション対象が空なだけで追加分岐不要。既存の空状態表示をそのまま維持する。

#### T1-B: price_to_screen_y と draw_chase_trail を sparse row 対応に改修

**現在の問題**:  
`price_to_screen_y`（L922）は `Price::steps_between_inclusive(price, best_bid/ask, grid.tick)` で  
「価格差 = 等間隔 row」を前提に Y 座標をインデックスから計算する。  
`draw_chase_trail`（L774）はこの関数に依存する。  
スパース表示では行間隔が可変（価格ギャップがある）なため、step 数から Y は復元できない。

**修正後の動作**:  
`visible_rows` が返す行リストに `(price → y)` のマッピングを保持し、  
`price_to_screen_y` は等間隔計算ではなくこのマッピングを参照する。  
chase tracker の始点・終点 Y 座標も同じマッピング経由で解決する。  
`PriceGrid` struct および `build_price_grid` はこの改修で不要になる可能性があり、  
`for idx` ループ全体を廃止する。

`visible_rows` から返す `VisibleRow` に `y: f32` フィールドが既にある（L918 の sort）ため、  
このリストを `price → y` ハッシュマップに変換して chase trail 描画に渡す設計が自然。

### T2: 価格表示精度（decimal places）— 矛盾解消

**判断**: `min_ticksize=0.1` fallback が残るため、SoftBank(5379 円) は **`5379.0`（小数 1 桁）と表示される。**  
これを本修正スコープでは**許容出力**とし、§6 ロールアウト確認もこの前提で記載する。

**根拠**: `min_ticksize` を実呼値（1.0 円）に正しく解決するには Python fallback 修正・IPC 追加・  
Rust DTO 変更の 3 層変更が必要であり（§2 で却下済み）、本修正の変更範囲外。  
T1-A で板の**存在判定**から `min_ticksize` を完全に切り離すため、`5379.0` 表示は UX 的に  
冗長だが機能的に誤りではない（0.1 刻み price grid を描画する問題は解消している）。

`min_ticksize` 表示精度の根本修正（`5379` 表示）は §7 将来対応へ分類する。

### T3: テスト

| テスト | ファイル | 内容 | 実行コマンド |
|---|---|---|---|
| `test_ladder_sparse_no_empty_rows` | `src/screen/dashboard/panel/ladder.rs` の `#[cfg(test)]` | depth が整数価格のみの場合、描画行に `.5` 価格が含まれないことを assert | `cargo test -p flowsurface -- ladder::tests::test_ladder_sparse_no_empty_rows` |
| `test_ladder_sparse_bid_ask_order` | 同上 | asks 昇順・bids 降順で行が並ぶことを assert | `cargo test -p flowsurface -- ladder::tests::test_ladder_sparse_bid_ask_order` |
| `test_ladder_sparse_spread_label` | 同上 | スプレッドラベルが `best_ask - best_bid` を正しく表示することを assert（既存スプレッドロジックがスパース化後も正常動作することの確認テスト） | `cargo test -p flowsurface -- ladder::tests::test_ladder_sparse_spread_label` |
| `test_ladder_sparse_empty_book` | 同上 | bids/asks 両方空の場合に panic せず空描画になることを assert | `cargo test -p flowsurface -- ladder::tests::test_ladder_sparse_empty_book` |
| `test_ladder_sparse_one_side_only` | 同上 | bids のみ or asks のみの場合に片側のみ表示されることを assert | `cargo test -p flowsurface -- ladder::tests::test_ladder_sparse_one_side_only` |
| `test_ladder_sparse_price_format` | 同上 | `min_ticksize=0.1` のとき SoftBank 相当（整数円価格）が `5379.0` と表示されることを assert（`5379.0` は本修正スコープの許容出力、PASS 期待） | `cargo test -p flowsurface -- ladder::tests::test_ladder_sparse_price_format` |

---

## 5. リグレッションガード

既存の Ladder テストがある場合は、連続グリッドを前提とした assert を  
スパース前提に書き直す。  
`/bug-postmortem` フローで **修正前 FAIL → 修正後 PASS** を確認すること。

`grep -n '#[cfg(test)]' src/screen/dashboard/panel/ladder.rs` で既存テストを列挙し、連続グリッド前提の assert（価格が step 刻みで並ぶことを検証しているもの）をスパース前提に書き直す。既存テストが存在しない場合は本項はスキップして T3 のみ実施する。

---

## 6. ロールアウト手順

1. T1 (Ladder スパース化) 実装 → `cargo test --workspace` PASS（`cargo test --workspace` に自動で含まれるため CI 追加作業は不要）
2. T2 (価格フォーマット確認) → `test_ladder_sparse_price_format` が PASS することで確認する。demo 環境の SoftBank 等では `5379.0`（小数 1 桁）表示になる。これは本修正スコープの許容動作（§4 T2 参照）。
3. T3 テスト全件 PASS 確認
4. demo 環境で SoftBank(9984)・みずほ(8411) の Ladder を目視確認 → 交互 0 消失
5. トヨタ(7203)・NTT(9432) の Ladder も目視確認 → 正常表示維持
6. `/bug-postmortem` 実行 → `MISSES.md` に知見追記
7. `/review-fix-loop` でレビュー

---

## 7. 将来対応（本修正スコープ外）

- **行間 gap ラベル（スプレッド超大の場合）**: スプレッドが一定以上（例: 10 円以上）の場合、ask 最下行と bid 最上行の間に「gap: N 円」ラベルを挿入して価格連続性を補完する。ユーザー需要が確認されてから実装する。
- **価格ギャップ可視化（小スプレッド）**: 行間に「gap: 2 円」等のラベルを挟む UI 拡張  
  → ユーザー需要が確認されてから実装
- **`min_ticksize` 表示精度修正**（`5379.0` → `5379` 化）: `resolve_min_ticksize_for_issue` を  
  スナップショット価格で再解決し `TickerMetaUpdate` IPC で Rust に通知する 3 層修正（§2 却下案）。  
  本修正で板存在判定への実害は解消済み。decimal 表示の精度を正したい場合にのみ実施する
- **step による集計表示**: 複数ティックを 1 行に集計する機能（現 HeatmapChart と同様）

---

## 8. 参考

- [SKILL.md — 呼値テーブル仕様](../../.claude/skills/tachibana/SKILL.md)
- [ladder.rs — Ladder 描画](../../src/screen/dashboard/panel/ladder.rs)
- [pane.rs — PaneSetup::new() での price_step 計算](../../data/src/layout/pane.rs)
- [実測スクリプト](../../scripts/diagnose_tachibana_depth_raw.py)

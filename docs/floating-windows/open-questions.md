# Floating Windows 移行: Open Questions

本ドキュメントは Bevy frontend 移行に伴う未決事項を集約する。各 Q は
**選択肢 / 判定基準 / 決定 Phase / 依存** の 4 項目で構成する。

## 統一決定事項（前提）

以下は全 Q に共通する確定事項であり、各 Q の選択肢から除外済み。

- 旧 `saved-state.json` は破棄し、新スキーマには `schema_version: u32` を導入する。
- popout は Phase 6 までスコープ外（非永続）として扱う。スコープ確定は Q6 で行う。
- focus 型は `Option<PaneLocation>` に抽象化する。具体型の確定は Q1 解決後に行う。
- Phase 2 spike には wgpu 共存性 PoC を含める。Q1 が解決するまで Phase 4 には着手しない。

## 決定タイミング表

| Q | テーマ | 決定 Phase | 主要依存 |
|---|--------|-----------|---------|
| Q1 | Bevy frontend の配置方式（wgpu 共存性含む） | Phase 2 完了時 / Phase 4 着手前 | 統一前提（schema_version, focus 型） |
| Q2 | pane 内容の描画責務 | Phase 3 着手前 | Q1 |
| Q3 | 設定 UI の移植順 | Phase 4 着手前 | Q1, Q2 |
| Q4 | popout の内部実装 | Phase 5 着手前 | Q1 |
| Q5 | 過渡期の同期粒度 | Phase 4 中 | Q1 |
| Q6 | popout の永続化と Bevy 化スコープ | Phase 5 着手前 | Q1, Q4 |
| Q7 | Bevy 自動テスト方針 | Phase 3 完了時 / Phase 4 着手前 | Q1 |
| Q8 | `schema_version` バンプ規則の運用方針 | Phase 6 完了時 / 次計画着手前 | なし |
| Q9 | HiDPI / DPR 変化時の Camera 挙動 | Phase 6 完了時 | Q4 |

---

## Q1. Bevy frontend の配置方式

- **選択肢**:
  - (a) 既存 iced アプリ内へ Bevy を組み込み、同一プロセス・同一ウィンドウで描画する
  - (b) 別バイナリ / 別ウィンドウとして段階導入し、IPC 経由で連携する
  - (c) 既存プロセス内で別ウィンドウのみ Bevy 化する（同一プロセス・別ウィンドウ）
- **判定基準**:
  - Phase 2 spike の wgpu 共存 PoC 結果。具体的には iced 0.14（wgpu 27）と
    Bevy（wgpu 23/24）が同一プロセスで build 可能か、runtime で wgpu の
    Instance / Adapter / Device を共存させられるか
  - 共存不可の場合は (b) または (c) に倒す
  - frame pacing と入力レイテンシの実測値
- **決定 Phase**: Phase 2 完了時 / Phase 4 着手前
- **依存**: 統一前提（schema_version 導入、focus 型抽象化）。Q2〜Q7 はすべて Q1 に依存する。

## Q2. pane 内容の描画責務

- **選択肢**:
  - (a) Bevy UI（`bevy_ui` ベース）に全面寄せる
  - (b) 2D 描画（chart 本体）と UI（パネル・メニュー）を分離し、UI のみ別レイヤで処理する
  - (c) 既存の iced ウィジェットを overlay として残す
- **判定基準**:
  - Q1 で決定した配置方式の制約（同一 wgpu device を使えるか、layer 合成方法）
  - chart 描画の現行コスト（`canvas` ベース）と Bevy 移植コストの比較
  - UI と chart の入力ハンドリングが分離可能か
- **決定 Phase**: Phase 3 着手前
- **依存**: Q1

## Q3. 設定 UI の移植順

- **選択肢**:
  - (a) Starter / Heatmap など個別画面から順次移植する
  - (b) 共通 UI 部品（modal, dropdown, slider 等）を先に整備してから画面ごと移植する
  - (c) 設定 UI は最後まで iced に残し、chart 部分のみ先行する
- **判定基準**:
  - Q2 の描画責務の決定に伴う UI コンポーネントの再利用可能性
  - 共通部品の数と複雑度（少なければ (a)、多ければ (b)）
- **決定 Phase**: Phase 4 着手前
- **依存**: Q1, Q2

## Q4. popout の内部実装

- **選択肢**:
  - (a) main window と同じ Bevy frontend を共有し、camera / viewport で切り出す
  - (b) popout ごとに独立した Bevy `World` + camera を持たせる
  - (c) Bevy multi-window 機能で同一 App 内に複数 window を持つ
- **判定基準**:
  - Q1 の配置方式（同一プロセス前提か別プロセス前提か）
  - Bevy multi-window のコスト（GPU リソース重複・event loop 干渉）
  - popout ↔ main 間の状態同期コスト
- **決定 Phase**: Phase 5 着手前
- **依存**: Q1

## Q5. 過渡期の同期

- **選択肢**:
  - App State を source of truth に固定する点は確定。Bevy 側の編集を main の State へ
    commit する**粒度**として以下から選ぶ:
  - (a) ドラッグ完了時 commit（`WindowMoved` の最終値のみ反映）
  - (b) N ms debounce（最後のイベントから N ms 静止したら反映）
  - (c) 16 ms throttle（毎フレーム最大 1 回反映）
  - (d) イベント毎即 commit（`WindowMoved` ごとに State 更新）
- **判定基準**:
  - `WindowMoved` の発火頻度（プラットフォーム差含む）
  - State 更新に伴う再描画 / 永続化（`saved-state.json` 書き込み）のコスト
  - ドラッグ中の視覚的追従性（latency）
- **決定 Phase**: Phase 4 中（実装と並行して計測）
- **依存**: Q1

## Q6. popout の永続化と Bevy 化スコープ

統一前提により popout は Phase 6 までスコープ外（非永続）。Phase 6 以降の扱いを決める。

- **選択肢**:
  - (a) main と同じ Bevy frontend を共有し、永続化対象に格上げする
  - (b) popout ごとに独立 `World` + camera を持ち、永続化も独立スキーマで保存する
  - (c) popout を non-goal として Phase 6 で削除する
- **判定基準**:
  - Phase 4 の接続結果（Bevy frontend が main で安定稼働するか）
  - Bevy multi-window のコスト（Q4 の判定と連動）
  - popout の実利用頻度
- **決定 Phase**: Phase 5 着手前
- **依存**: Q1, Q4

## Q7. Bevy 自動テスト方針

- **選択肢**:
  - (a) `MinimalPlugins` で system 単位の unit test を書く
  - (b) headless harness（`bevy_ci_testing` 等）で end-to-end テストする
  - (c) 自動テストは持たず手動確認のみとする
- **判定基準**:
  - Phase 2 spike で得られた system 構成の単体可テスト性
    （system が `World` 以外の依存を持たずに呼べるか）
  - CI 実行時間と GPU 依存（headless で wgpu adapter が取れるか）
- **決定 Phase**: Phase 3 完了時 / Phase 4 着手前
- **依存**: Q1

## Q8. `schema_version` バンプ規則の運用方針

- **選択肢**:
  - (a) 後方互換ありフィールド追加もバンプする
  - (b) 破壊変更のみバンプする（`#[serde(default)]` で吸収、本計画では暫定こちらを採用 / spec NF4 参照）
  - (c) major / minor の 2 段に分割する
- **判定基準**:
  - Phase 1 v1 切り後 6 ヶ月以内のフィールド追加頻度
  - 追加が頻繁なら (a) または (c)、稀であれば (b) を継続
- **決定 Phase**: Phase 6 完了時 / 次計画着手前
- **依存**: なし

## Q9. HiDPI / DPR 変化時の Camera 挙動

- **選択肢**:
  - (a) DPR を永続化せず viewport clamp（NF6）のみで吸収する（本計画では暫定こちらを採用 / spec §1 参照）
  - (b) DPR を `saved-state` に併記し復元時に補正する
  - (c) physical px ベースに座標系を変更する
- **判定基準**:
  - マルチモニタ DPR 差環境での Phase 6 e2e 結果
  - clamp のみで体感上のズレが許容範囲かどうか
- **決定 Phase**: Phase 6 完了時
- **依存**: Q4

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
| Q1 | Bevy frontend の配置方式（wgpu 共存性含む） | Phase 2 完了時 / Phase 4 着手前（= Phase 3 着手は可、Phase 4 不可） | 統一前提（schema_version, focus 型） |
| Q2 | pane 種別ごとの描画責務（architecture §3.5 の 3 分類で確定） | **Phase 2 完了時 / Phase 3 着手前** | Q1 |
| Q3 | 設定 UI / 一時 UI の Bevy 化スコープ（**iced 残置を確定**） | Phase 2 完了時 / Phase 3 着手前 | Q1, Q2 |
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

## Q2. pane 種別ごとの描画責務

architecture §3.5 の 3 分類（`Bevy native` / `host existing renderer` / `keep iced overlay`）に
各 pane 種別を割り当てて確定する。本 Q は分類のフレームを使い、**抽象論で開いたままに
しない**。

- **選択肢**（pane 種別ごとに 1 つ選ぶ）:
  - `Bevy native`: Bevy renderer / scene / pipeline をフル活用
  - `host existing renderer`: 既存 wgpu / `iced::canvas` 描画を Bevy pane 内に host
  - `keep iced overlay`: Bevy pane 上に iced overlay として残す（一時 UI のみ）
- **判定基準**:
  - Q1 で決定した配置方式の制約（同一 wgpu device の可用性）
  - 各 pane の現行描画コスト（特に Heatmap GPU pipeline `src/widget/chart/heatmap.rs:355`
    （`OverlayCanvas`） / Kline per-frame `src/chart/kline.rs:49,889`
    （`impl Chart for KlineChart` / `fn draw`））と Bevy native への再実装コスト
  - spec §6 機能保持マトリクスを満たせるか
  - `host existing renderer` 分類は **Q1=(a) 同一 wgpu device 共存前提**。
    Q1=(b)/(c) なら退避ルート（Bevy native 再実装 or オフスクリーン render→texture）が必要
  - **iced::canvas は texture 単独書き出し標準 API なし → Kline は host existing renderer
    不能のリスクがあり Phase 2 spike で実機判定**
- **暫定割当**（architecture §3.5 と同期）:
  - Heatmap: `host existing renderer`（GPU pipeline 温存）
  - Kline: `host existing renderer`（per-frame 描画・crosshair・study 反映を温存）
    ※ iced::canvas の texture 書き出し可否次第で `Bevy native` 再実装に倒れる可能性あり（Phase 2 spike で確定）
  - Ladder: `Bevy native` 候補
  - TAS / Starter: `Bevy native` 候補
  - 設定 modal / indicator picker / study configurator / 認証 / Tachibana ログイン: `keep iced overlay` 確定
- **決定 Phase**: **Phase 2 完了時 / Phase 3 着手前**
- **依存**: Q1

## Q3. 設定 UI / 一時 UI の Bevy 化スコープ

本計画では **設定 modal / indicator picker / study configurator / 認証ダイアログ /
Tachibana ログイン UI / 管理画面は Bevy 化しない**（架構: architecture §2 + §4.1 + INV-INPUT-4）。
本 Q は「移植順を決める」ではなく「iced 残置の境界を確定する」ことが目的。
本計画では以下のいずれを取っても、設定 modal / indicator picker / study configurator /
認証ダイアログ / Tachibana ログイン UI / 管理画面の Bevy 化は **本計画スコープ外**。
Bevy 化したい場合は **別計画として起票が必要**。

- **選択肢**:
  - (a) 本計画では iced 残置で確定し、別計画起票を待つ（暫定こちら）
  - (b) chart 部分のみ先行 Bevy 化し、modal / picker は将来別計画で再検討
  - (c) 全 UI を Bevy 側へ移植（本計画では不採用）
- **判定基準**:
  - 一時 UI を Bevy 化するインクリメンタルな価値（dashboard 機能要件 F1〜F10 に直接寄与しない）
  - iced overlay として残しても入力境界契約 INV-INPUT-1〜4 で十分制御できるか
  - modal / picker の再実装コスト
- **決定 Phase**: Phase 2 完了時 / Phase 3 着手前（Q2 と同時に確定）
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
  - `WindowMoved` の発火頻度（プラットフォーム差含む）。
    数値判定: `WindowMoved` 発火 `> 120 events/s` なら (c) 16ms throttle /
    `60 ≤ rate ≤ 120` なら (b) 8ms debounce を default（揺れたら (c) に倒す） /
    `< 60 events/s` なら (d) 即 commit 許容 / 許容 latency ≤ 16ms /
    計測スクリプト `scripts/measure_window_moved_rate.sh` で実測
  - State 更新に伴う再描画 / 永続化（`saved-state.json` 書き込み）のコスト。
    Phase 4 中の判定に saved-state.json の sync write / async write 区別を含める
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
  - `schema_version > 自分が知る最大値` も破棄して default 起動が前提
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

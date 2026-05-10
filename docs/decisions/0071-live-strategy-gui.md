---
id: 0071
title: "Live Strategy GUI — File > Open から live 戦略を起動する UX"
status: accepted
date: 2026-05-08
source_commit: 3149879
old_path: "docs/✅nautilus_trader/archive/✅live-strategy-gui.md"
---

# ADR 0071: Live Strategy GUI — File > Open から live 戦略を起動する UX

## Status

accepted

> **Note**: 起票時点で本 ADR の `source_commit` は原本（`3149879`）を指す。
> issue #42 の最終 merge commit が確定したら、後続 PR の fixup commit で
> `source_commit` を merge SHA に更新してよい（決定の出典は原本のまま、
> accepted 化の trigger となった merge を 2 段で記録するスタイル）。

## Context

`NautilusRunner.start_live()` / `LiveSession.run()` は実装済みだったが、
GUI（Rust / iced）から `StartEngine{engine: "Live"}` を発行する経路が存在せず、
**replay と live で同一戦略ファイルを起動する経験が完全に非対称** だった。

導線が塞がれていた箇所:

1. **入口**: `Action::OpenFile` は replay モードでだけ `.py` picker を開き、
   live モードでは JSON（`saved-state.json`）picker に fall-through していた。
2. **ドロップ**: `NativeOpenStrategyPicked` ハンドラは live モードで `.py` を
   **意図的に drop** しており、フォーム modal を開く経路がなかった。

加えて、live 起動後の UX を支える次の要素も欠落していた:

- `LiveStrategyFormModal`（4 フィールド: `instrument_id` / `strategy_file` /
  `max_qty` / `max_notional_jpy`）の不在
- `EngineStarted` (live) → 4 ペイン自動生成 → `LiveBuyingPower` UI 反映の
  動的バインド経路の不在
- `EngineStopped{strategy_id}` を **session UUID で突き合わせて** UI をクリア
  する仕組み（mode switch / engine restart 由来の停止イベントを誤って拾わない）
- 第二暗証番号モーダル（既存 `SecondPasswordModal`）と live 戦略フォームの順序

issue #42 の Phase 3 / Phase 3.5 / Phase 6 で本 ADR を実装に落とし込み、
deferred から accepted に昇格させる。

## Decision

GUI から live 戦略を起動・停止する最小 UX を、次の構造で実装する。

### 1. 入口（File > Open）の live 分岐

`Action::OpenFile` に live モード分岐を追加し、`.py` picker を開いて
`Message::NativeOpenStrategyPicked` に流す。replay の `.py` picker と完全に
対称（saved-state JSON picker は本 ADR では扱わず、別経路に分離）。

### 2. `LiveStrategyFormModal`（`src/modal/live_strategy_form.rs`）

`replay_form.rs` と同構造の入力 modal。フィールド:

| フィールド | 型 | 検証ルール |
|------------|-----|----------|
| `instrument_id` | `String` | 非空 + `"."` を含む（例 `8306.T`） |
| `strategy_file` | `PathBuf` | 編集不可。`.py` 拡張子で実在 |
| `max_qty` | `String` | `1..=10_000` の整数 |
| `max_notional_jpy` | `String` | `1..=100_000_000` の整数（1 億円上限） |

issue #42 Phase 3 / 3.5 で次のフィールドを後付けで拡張する:

- `strategy_init_kwargs`（JSON 文字列）— `LIVE_SCENARIO` prefill 経路で埋まる
- `prod_mode: bool` + `tachibana_is_production: bool`（Phase 3.5、capability で disable 判定）
- `disabled_reason: Option<String>` — Submit 不可時の理由表示
- `pending_scenario_request_id` — `LoadLiveStrategyScenario` の応答待ち管理

### 3. メニューバー 2 段目（`LiveBarState`）

`EngineStarted{strategy_id}`（live）を受信した時点でメニューバーに 2 段目を
展開する。1 段目（File メニュー）+ 2 段目（戦略ファイル名 / 現在時刻 /
⏸ ▶ ■ ボタン）の構造で、replay bar と同じ高さリズムを維持する。

```text
┌──────────────────────────────────────────────────────┐
│ ファイル（File）▼                                       │  ← 1 段目（既存）
├──────────────────────────────────────────────────────┤
│  <file_stem>   HH:MM:SS   ⏸   ▶   ■                  │  ← 2 段目（新規）
└──────────────────────────────────────────────────────┘
```

### 4. session UUID で `EngineStopped` を突き合わせる

`LiveStrategyFormModal::Submit` で `uuid::Uuid::new_v4()` を生成して
`StartEngine.strategy_id` に渡す。`EngineStopped{strategy_id}` 受信時は
`live_strategy_id` と完全一致する場合のみ UI をクリアする。

これにより:

- 同じ engine プロセスでの **mode switch** や **再接続** 由来の停止イベントは
  UUID 不一致で素通り（誤クリアしない）
- セッションごとに UUID が変わるので、replay → live → replay の往復でも
  状態混線が起きない

### 5. ペイン自動生成と冪等性

`LiveStrategyReady{strategy_id, instrument_id, venue}` 受信で
`auto_generate_live_panes(strategy_id, instrument_id, venue)` を呼び、
4+1 ペイン（CandlestickChart / TimeAndSales / OrderList / BuyingPower /
Positions）を生成する。冪等 key は **三つ組** `(strategy_id, instrument_id, venue)`
で `HashSet<(String, String, String)>` 管理。`EngineRehello` 受信時は
`LiveStrategyRehelloReplay` 内部メッセージで `auto_generate_live_panes` を
冪等再呼出（受け入れ基準 #11 / #17）。

### 6. `SecondPasswordRequired` 経路

engine から `SecondPasswordRequired` を受けた場合、既存 `SecondPasswordModal`
が反応する経路に加え、ステータスバーへ赤帯で固定文言
**「第二暗証番号を設定してください」** を表示する。CLI と GUI で
同一文言を使う（受け入れ基準 #8）。

### 7. capability に応じた prod チェックボックス制御

GUI は engine の `Ready.capabilities.venue_capabilities[<venue>].is_production`
を読み、env `TACHIBANA_ALLOW_PROD=1` で起動された engine プロセスでない
場合は prod モードチェックボックスを **disable** にする
（GUI が env を直接書き換える経路は持たない、issue #42 統一決定 #14）。

## Consequences

### 良い点

- **戦略ファイルを無改変で replay → demo → prod に持ち回せる**（受け入れ基準 #1）
- メニューバー 2 段目のレイアウトを replay bar と揃えることで、視覚的 / 操作的に
  対称な UX を実現
- session UUID 方式により、engine restart / mode switch を含む複雑な lifecycle
  でも UI 状態が常に一貫
- 4 ペイン自動生成の冪等性（三つ組 key）で reconnect 時の重複生成を防止

### コスト

- `LiveStrategyState::Running { strategy_id, instrument_id, venue }` を持つことで
  state machine のサイズが増える（Phase 3 で `Idle` / `Running` の 2 状態に確定）
- `LiveStrategyFormModal` のフィールド拡張（Phase 3 / 3.5 で `strategy_init_kwargs` /
  `prod_mode` / `disabled_reason` / `pending_scenario_request_id` 追加）が将来も
  発生しうる。`Action::Submit` のシグネチャ変更を許容する設計にしておく必要がある。
- メニューバー高さの動的変更（`bar_height(mode, live_strategy_running)`）が
  全ての呼び出し点に影響するため、Phase 9 を 9a / 9b / 9c に細分化して
  ビルド可能性を維持

### 非ゴール（本 ADR では扱わない）

- 複数 live 戦略の同時実行（issue #42 では同 venue concurrent を `EngineBusy` で
  reject するに留める）
- live pause / resume の Python engine 側実装（GUI は IPC stub を発行するだけ）
- ログイン UI（既存 `SecondPasswordModal` をそのまま再利用）
- saved-state JSON picker の live モード対応（File > 設定を開く として分離）

## 関連

- 原本: `git show 3149879:"docs/✅nautilus_trader/archive/✅live-strategy-gui.md"`
- 並列 ADR: [0072 — Execute Live Strategy](0072-execute-live-strategy.md)
- 仕様書: [`docs/specs/live-strategy.md §5`](../specs/live-strategy.md)
- 実装 issue: GitHub issue #42（feat/issue-42-live-strategy ブランチ）

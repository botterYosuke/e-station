---
title: ライブ戦略 venue 横断契約
status: proposed
migrated_from:
  - docs/✅tachibana/spec.md#abstract-contract
source_commit: 236c0d2
---

# ライブ戦略 venue 横断契約

> **status: proposed** — 本ドキュメントは venue 横断の抽象契約をまとめる新規スケルトンであり、内容は各 venue spec から抽象化される過程にある。tachibana / kabusapi / 暗号資産 venue のいずれもこの契約に従う前提で実装されているが、文書としての整合化は移送後の継続課題。

## 1. 目的

「ライブ戦略 = 実 venue（broker / 取引所）に対して subscribe / fetch / 認証を行うモード」全般に共通する抽象契約を、venue 固有 spec から切り出して 1 箇所に集約する。venue 固有事情は各 `docs/specs/venues/<venue>.md` 側に置き、本書はそこからの参照のみを持つ。

## 2. 契約の構成要素（スケルトン）

### 2.1 VenueState FSM

`Idle / LoginInFlight / Ready / Error` の 4 状態を持つ単一 enum。venue ごとの実 state 機械は `src/venue_state.rs::VenueState` を共通利用する。

- 状態遷移の DTO 名は `VenueLoginStarted` / `VenueLoginCancelled` / `VenueReady` / `VenueError`（Python engine event）。
- UI 側の状態管理は `VenueState::{Idle/LoginInFlight/Ready/Error}` 1 本化。
- 詳細な遷移表と冪等性の規約は各 venue spec の「セッション寿命と復旧」節に記載。

### 2.2 venue-ready ゲート

- venue の業務リクエスト（`ListTickers` / `GetTickerMetadata` / `FetchTickerStats` / `Subscribe`）は `VenueReady` 受信後にのみ送ってよい。
- `VenueReady` は冪等イベント。Python サブプロセス再起動検知時のみ Rust 側が状態をリセットし、active subscriptions を 1 度だけ resubscribe する。
- 詳細な timeout / cache / bridge 規約は `engine-client/src/process.rs::ProcessManager` を一次ソースとする。

### 2.3 VenueError DTO 形状

```
VenueError { venue, request_id, code, message }
```

- `code` は UI 側の severity 判定とアクションボタン出し分けにのみ使う。
- `message` は Python 側 venue コードが詰めた user-facing 文言を Rust UI がそのまま描画する。
- venue 固有 `code` 一覧と severity マッピングは各 venue spec の「失敗モードと UI 表現」節を参照。

### 2.4 認証ライフサイクル

- runtime 中の自動再ログインは禁止（再ログインは「ユーザー明示」操作が起点のときだけ許可）。
- アプリ起動直後の session 復元フェーズに限り、復元 session の validate が失敗した場合の再ログインを 1 回だけ許可する。
- 「自動」と「手動（ユーザー明示）」の境界判別基準は `Command::RequestVenueLogin` の受信を起点とするか否かで分ける。

### 2.5 Strategy SDK 接点

ユーザー戦略（Strategy）が live モードで venue とやり取りする際の境界。本節は別 spec（`docs/specs/strategy-sdk.md`）と相互参照。

## 3. venue 固有 hook

### 3.1 tachibana

venue 固有の契約は `docs/specs/venues/tachibana.md` を参照。本書の 2.1〜2.4 はすべて tachibana の旧 spec.md の決定事項を venue 横断に抽象化したものであり、tachibana 側の実装規約（FD frame quote rule / `p_errno=2` 検知 / dead-frame timeout / banner code 一覧）はそちらで完結する。

### 3.2 kabusapi

# kabusapi 共通 hook（`docs/specs/live-strategy.md` 追記断片）

本ファイルは tachibana エージェントが Wave 4 で作成する `docs/specs/live-strategy.md` に append すべき、kabusapi venue 由来の **venue 横断抽象契約** を切り出したもの。本ファイル単独では参照されず、Wave 4 で統合された後は内容が `docs/specs/live-strategy.md` 側に同化する。

## A. IPC venue キー命名規則

- IPC `venue` フィールド文字列は Rust `Venue::*` enum と 1:1 対応する `snake_case`。
- kabuステーション venue の場合: Rust `Venue::KabuStation` ↔ IPC `"kabu_station"`。
- 立花 venue 同様、`Venue::from_str` で受理し、未知 venue は明示的に reject する。
- venue 文字列追加時は SCHEMA_MINOR bump が必要。

## B. venue ログイン共通ライフサイクル

| IPC メッセージ | 方向 | 発火タイミング |
| :--- | :--- | :--- |
| `RequestVenueLogin{venue}` | Rust → Python | GUI から「ログイン」ボタン押下 |
| `VenueLoginStarted{venue}` | Python → Rust | startup_login 開始時 |
| `VenueLoginCancelled{venue}` | Python → Rust | ユーザーがダイアログをキャンセル |
| `VenueReady{venue}` | Python → Rust | 認証完了（トークン取得など） |
| `VenueError{venue, code, message}` | Python → Rust | エラー検出時 |

すべての venue でこの遷移を満たすこと。

## C. `VenueError.code` 共通予約値

| code | 意味 | 期待挙動 |
| :--- | :--- | :--- |
| `"token_expired"` | トークン失効。retry 失敗時にこの code を発火 | tkinter 再ログインダイアログへ誘導。**自動再ログインは禁止**（ユーザー入力を伴う） |
| `"local_app_down"` | venue 提供アプリ（kabuステーション本体等）が落ちている / 接続不可 | 5s × N 回のバックオフ retry 後に発火。早朝強制ログアウト窓では INFO 扱い |

新 venue 追加時はこの命名空間衝突を `test_schema_compat.py` で検証する。

## D. credential 取り扱い原則

- API パスワード / トークン / 取引パスワードは **Python メモリのみ** に保持し、Rust 経路に流さない。
- ファイル永続化禁止（kabusapi はトークン短命、立花は session cache あり等、venue 毎に差異）。
- `caplog` に credential が出ないことをテストで確認（venue ごとに `test_*_logging.py` を持つ）。
- debug env は venue prefix 付き: `DEV_TACHIBANA_*` / `DEV_KABU_*`。

## E. ログイン UI は Python tkinter subprocess に統一

- Rust にダイアログコードを書かない。
- subprocess 隔離（メイン engine プロセスをフリーズさせない）。
- 取引パスワード（取消/発注時）の収集 UI も同じ方式に揃える。

## F. capabilities キー追加プロトコル

- `Ready.capabilities.venue_capabilities[<venue>]` に venue 別 capability dict を追加する。
- 必須キー候補: `requires_local_app: bool` / `max_push_symbols: int|null` / `supports_amend: bool` / `requires_trade_password_for_cancel: bool` / `is_production: bool`。
- 数値の一次ソース（例: PUSH 上限）は venue spec ファイルに置き、Rust 定数 / Python 定数と 1:1 一致を invariant test で保証する。

## G. `SubscriptionEvicted{symbol}` 共通通知

- PUSH 銘柄上限のある venue（kabuステーション = 50 上限）で LRU evict が発生したとき、`SubscriptionEvicted{symbol}` を IPC で送出する。
- UI は当該 symbol のチャートに「再登録が必要」バナーを表示する。
- 立花 venue は現状上限なしのため発火しないが、将来 venue 追加時の共通契約として定義する。

## H. URL リテラル所在原則

- 各 venue の API URL リテラルは Python 側 1 ファイル（kabusapi なら `kabusapi_url.py`、立花なら `tachibana_url.py`）に集約。
- Rust / engine-client / その他 Python ファイルへの URL リテラル漏出は CI lint で阻止。
- `localhost:18080` 本番 / `localhost:18081` 検証など環境別 base URL もこのファイルでのみ定義。


## 4. 出典

- `docs/specs/venues/tachibana.md`（旧 `docs/specs/venues/tachibana/spec.md`、source_commit: 236c0d2）
- `src/venue_state.rs`
- `engine-client/src/process.rs::ProcessManager`
- `engine-client/src/dto.rs::EngineEvent::{VenueReady, VenueLoginStarted, VenueLoginCancelled, VenueError}`

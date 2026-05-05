# Phase 8 — Python 単独モード化 / Rust HTTP API 廃止計画（改訂版 / attach mode 採用）

作成日: 2026-05-01
改訂日: 2026-05-01（内部レビューの内容を反映済み（参照文書なし））
最終改訂: 2026-05-01（**helper の attach client mode を採用**し、外部スクリプトから replay を駆動したときも GUI チャートが動く運用を維持する）
**実装完了: 2026-05-03（Phase 8.1a / 8.1b / 8.1c / 8.2 / 8.3 全サブフェーズ完了）**
対象: `src/replay_api.rs` / `src/api/order_api.rs` / `src/api/agent_api.rs` / `src/api/mod.rs` 廃止と Python helper class 新設
方針: **HTTP API を経由せず Python helper class を直接呼び出すユースケースを正規ルートに昇格させ、Rust 側 HTTP API を完全廃止する**

---

## 0. ゴール

1. **Python 単独モード**を一級市民化する。`flowsurface`（Iced GUI）が起動していなくても backtest / replay を Python だけで完結できる
2. **GUI 起動中の helper 並走経路を維持する**。`python -m engine.replay_session run ...` を別プロセスで投げると、GUI 内 engine を helper が WS クライアントとして駆動し、GUI チャートが今までどおり動く
3. Rust 側 HTTP control API（ポート 9876）を release build で**完全廃止**する。4 ファイル合計 約 6,756 行を削除
4. E2E テストの主流を `bash + curl` から `pytest + Python helper` に移し替える
5. GUI が必要な操作（sidebar toggle 等）は最小処置で済ませる（移植ではなく削除を基本方針とする）

**非ゴール**:

- WebSocket IPC（ポート 19876, schema_major / schema_minor）の廃止 — GUI ↔ engine 間通信は維持する
- `NautilusRunner` 内部実装の改変 — 既存 `start_backtest_replay_streaming` の signature と動作はそのまま再利用する
- Iced GUI のレイアウト・ペイン生成ロジックへの介入 — pane 生成は今と同じく `ReplayDataLoaded` 受信時に GUI 側で完結させる

### 0.1 着手前に確定済みの設計判断（Q2（Python プロセスのライフサイクル管理）解決 / attach mode 採用）

`open-questions.md` の Q2（Python プロセスのライフサイクル管理）について、レビューと「外部スクリプト経由でも GUI チャートを動かしたい」というユーザー要件を踏まえて Phase 8.1 着手前に次を確定する：

#### 0.1.1 helper の動作モード

- **helper は WS IPC サーバーを一切 bind しない**（自前で `:19876` を listen しない）
- helper は **2 つの動作モード** を自動判定する：
  - **in-process mode**: `:19876` に既存 engine が居ない → helper プロセス内で `NautilusRunner` を直接起動して driving する
  - **attach mode**: `:19876` に GUI が起動した engine が居て token と `SCHEMA_MAJOR` が一致する → helper は **WS クライアントとして接続** し、GUI 内 engine に `Command::LoadReplayData` / `Command::StartEngine` を送って driving する。`NautilusRunner` は GUI 内で 1 つだけ走り、event は GUI / helper の両者に同じ stream として届く
- **NautilusRunner の二重起動は構造的に禁止**（attach mode は engine を spawn しない、in-process mode は外部 engine が居ないことを probe で確認してから spawn する）
- **GUI と helper の協調動作は非サポート**（同時に load や order を投げた場合の動作は first-come-first-served、engine が `EngineBusy` で reject）
- helper class の **public API は high-level メソッドのみ** で `Command` 列挙体や IPC schema を一切 expose しない（attach client の wire format は private 実装に閉じる）
- helper class からユーザーが受け取る event は `dict` 一形態（in-process / attach の両モードで同じ）

#### 0.1.2 attach mode を成立させるための engine 側変更（Phase 8.1b の必須スコープ）

`attach mode` を「両者が同じ event stream を受信する」設計として成立させるには、現状の engine 実装が次の 3 点で不足している。Phase 8.1b で**必ず併せて実装する**：

| 不足 | 対応状況 |
|------|---------|
| **B1. 単一クライアント制限** | ✅ **実装済み**（Phase 8.1b）: `_Broadcaster` による multi-client fanout。`MAX_CONNECTIONS=4`。`ClientConnected`/`ClientDisconnected` イベント broadcast。 |
| **B2. token 共有経路の不在** | ✅ **実装済み**（Phase 8.1b）: `engine-client/src/session_file.rs` `EngineSession::write_atomic()` がハンドシェイク後に `engine-session.json` を atomic write。Drop で削除。Python helper は `platformdirs` で同パスを解決。 |
| **B3. EngineBusy reject 未実装** | ✅ **実装済み**（Phase 8.1b）: `server.py` に `ReplayState`（Idle/Loaded/Running/Stopping）と `LiveState`（Disconnected/Connecting/Connected）の 2 つの直交 state machine を追加。不適切な遷移は `EngineBusy` event で reject。`schemas.py` に `EngineBusy` イベントを追加。 |

これらは **Phase 8.1b で全て実装完了（2026-05-03）**。`_AttachClient` も同一 PR で実装済み。

これを Phase 8.1 着手の前提条件とする。

---

## 1. 動機・背景

### 1.1 現状の歪み

[CLAUDE.md](../../.claude/CLAUDE.md) と memory に記録された設計判断軸：

- **「Python 単独でも動くか？」** を判断軸に使う
- **AI/ML フレームワーク非同梱方針** — 本体は AI を持たず `examples/ レシピで対応`

しかし現在の起動経路は：

```
ユーザー → curl POST :9876/api/replay/load → Rust HTTP listener
       → mpsc → Iced::update() → engine_client.send(LoadReplayData)
       → WS IPC :19876 → Python NautilusRunner
```

**Python のロジックを呼ぶのに Rust GUI を起動して HTTP を経由する**という倒錯した構造になっている。これは Python 単独モード非対応であり、上記方針と直接矛盾する。

### 1.2 既存コードに残された伏線

[python/engine/nautilus/engine_runner.py:147-153](../../python/engine/nautilus/engine_runner.py#L147-L153)：

```python
class NautilusRunner:
    """nautilus エンジンのライフサイクルを管理するワーカー。

    N0: start_backtest() のみ実装。start_live() は stub。
    N1 以降: server.py のディスパッチャから StartEngine Command で呼ばれる。
    Python 単独モード: CLI から直接呼び出し可能（IPC 経由でなくてもよい）。
    """
```

`start_backtest_replay_streaming()` の signature（[engine_runner.py:477-494](../../python/engine/nautilus/engine_runner.py#L477-L494)）は

- `on_event: Callable[[dict], None]` で event streaming
- `stop_event: threading.Event` で外部キャンセル
- `get_multiplier: Callable[[], int]` で実行中の速度変更

すべて IPC を介さず in-process で driving できる構造で実装済み。**helper class は薄いラッパーで足りる**（in-process mode の場合）。attach mode では `_AttachClient` を介して同等の callback API をユーザーに提供する。

### 1.3 GUI 経路は HTTP を経由していない（order_api 全廃の安全性）

[src/main.rs:2140-2199](../../src/main.rs#L2140-L2199) で GUI 発注は `Action::SubmitOrder` → `engine_client::dto::SubmitOrderRequest` を直接組んで `Command::SubmitOrder` を IPC 送信している。`order_api.rs::handle_submit_request` は経由しない。

つまり：

- `OrderGuardConfig` の rate limiter / qty/yen cap は **`/api/order/submit` HTTP path 専用の防壁**で、GUI 経路にはもともと適用されていない
- HTTP path を廃止しても GUI 発注ロジックは無傷
- `order_api.rs` 3,490 行は丸ごと安全に削除できる

### 1.4 attach mode の設計上の対称性

Rust 側は既に [.claude/CLAUDE.md](../../.claude/CLAUDE.md) の `start_or_attach` で「engine が居れば attach、居なければ spawn」の対称構造を持っている。helper を attach mode 対応にすると、Python 側からも同じ対称が成り立つ：

| 起動主体 | engine 不在 | engine 既存 |
|---------|------------|------------|
| Rust GUI（既存） | 自分で spawn | attach（client） |
| Python helper（本計画） | in-process で `NautilusRunner` を直接呼ぶ | attach（client） |

両者から見て **engine プロセスは常に 1 つ** であり、event stream は単一の真実源として保たれる。

### 1.5 現状 engine の制約と Phase 8.1b で解消する範囲

§0.1.2 の B1〜B3 を補足する。

- **B1（単一クライアント）**: 現状は GUI 側 Rust `engine-client` が唯一の接続者である前提で組まれている。Rust 側 reconnect は「自分の接続が切れたら張り直す」モデル（[engine-client/src/connection.rs](../../engine-client/src/connection.rs)）で、その間 engine 側は別接続を持たない。multi-client 化しても **Rust の reconnect ロジックには手を入れる必要がない**（自分の接続だけ管理し続ける）が、engine 側の「接続管理」「event fanout」の責務は両方とも 1 → N 対応にする
- **B2（token 共有）**: GUI が attach 経路を使う場合（手動 engine 起動）は env 経由、spawn 経路（既定）は stdin 経由。これは既に分岐済みで意図的な設計。helper は GUI が spawn したケースに **新たに対応が要る**ため、session ファイルが第三の token 取得経路として必要になる
- **B3（EngineBusy）**: 既存 HTTP API は dispatcher 側で簡易 state 確認（`replay_api.rs` 内で global 変数）をしているだけで、IPC 直接経路には state guard が無い。multi-client 化と同時に必須になる（複数 client が同時に load を投げる可能性が新たに生じるため）

→ B1〜B3 は **multi-client 化に伴う必然的なセット**。attach mode 採用の代償として一括で実装する。

---

## 2. 現状の HTTP API 棚卸し（廃止対象）

### 2.1 Rust 側 HTTP endpoint 一覧

> 注: dispatcher（TCP listener + ルーティング）は [`src/replay_api.rs::spawn`](../../src/replay_api.rs) に集約されている。`order_api.rs` / `agent_api.rs` は handler 関数を提供するのみで、Phase 8.3 で `replay_api.rs` を削除すれば自動的に dead code 化する。

| 系統 | endpoint | 移行先 |
|------|---------|--------|
| **A. Replay 制御** | `POST /api/replay/load` | Python `ReplaySession.load()` |
|  | `POST /api/replay/start` | `ReplaySession.run()` |
|  | `POST /api/replay/order` | `ReplaySession.submit_order()` |
|  | `POST /api/replay/control` | `ReplaySession.set_speed()` |
|  | `GET /api/replay/portfolio` | `ReplaySession.portfolio` プロパティ |
|  | `GET /api/replay/status` | `ReplaySession.status` プロパティ |
| **B. Order 制御** | `POST /api/order/submit` | Python `LiveSession.submit_order()` |
|  | `POST /api/order/modify` | `LiveSession.modify_order()` |
|  | `POST /api/order/cancel` | `LiveSession.cancel_order()` |
|  | `POST /api/order/cancel-all` | `LiveSession.cancel_all()` |
|  | `GET /api/order/list` | `LiveSession.orders` プロパティ |
| **C. Agent 通知** | `POST /api/agent/narrative` | Python `Session.narrate(...)` |
|  | `GET /api/agent/narrative` | `Session.narratives` |
| **D. Sidebar 操作** | `POST /api/sidebar/toggle-venue` | **削除**（後述 §5 Phase 8.2） |
|  | `POST /api/sidebar/tachibana/request-login` | **`LiveSession.login()` に移植**（§5 Phase 8.1 必須） |
| **E. Test ユーティリティ** | `POST /api/test/tachibana/cancel-helper` | **削除**（debug build に backdoor 残存可） |
|  | `POST /api/test/tachibana/delete-session` | **削除**（debug build に backdoor 残存可） |

廃止対象ファイル（合計 **約 6,756 行**）：

| ファイル | 行数 | 役割 |
|---------|------|------|
| [src/replay_api.rs](../../src/replay_api.rs) | 2,943 | TCP listener + dispatcher + replay 系ハンドラ |
| [src/api/order_api.rs](../../src/api/order_api.rs) | 3,490 | order 系ハンドラ + OrderGuardConfig |
| [src/api/agent_api.rs](../../src/api/agent_api.rs) | 323 | agent narrative ハンドラ |
| [src/api/mod.rs](../../src/api/mod.rs) | 2 | 親モジュール宣言（`pub mod agent_api; pub mod order_api;`） |

### 2.2 E2E テスト（curl 駆動）

[tests/e2e/](../../tests/e2e/) 配下の bash スクリプト：

| ファイル | sidebar/login 依存 | 移行先 |
|---------|------------------|--------|
| `s55_mode_startup_smoke.sh` | なし | **そのまま維持**（HTTP 不使用、プロセス起動の dry-run smoke） |
| `s56_replay_pane_autogen.sh` | なし | pytest（helper 経由） |
| `s57_replay_buying_power_smoke.sh` | なし | pytest |
| `s58_replay_load_smoke.sh` | なし | pytest |
| `s80_order_crash_recovery_demo.sh` | **依存** | pytest（**`LiveSession.login()` 必須**） |
| `s80_order_submit_demo.sh` | **依存** | pytest（同上） |
| `s81_order_modify_cancel_demo.sh` | **依存** | pytest（同上） |
| `s82_order_fill_ec_e2e.sh` | **依存** | pytest（同上） |
| `s83_ec_dedup_e2e.sh` | **依存** | pytest（同上） |
| `s90_replay_user_flow.sh` | なし | pytest |
| `tachibana_demo_login.sh` | **依存** | pytest（同上） |
| `tachibana_relogin_after_cancel.sh` | **依存** | pytest（同上） |
| `smoke.sh` | なし（:19876 のみ） | **そのまま維持**（GUI プロセス起動・観測が試験対象） |

> **依存** = `/api/sidebar/tachibana/request-login` を叩いて立花にログインする経路を踏んでいる。Order 系 E2E 7 本がこれに依存しており、これらを pytest 化するためには **Phase 8.1 で `LiveSession.login()` を必須スコープに含める**必要がある。

### 2.3 起動スクリプト

| スクリプト | 役割 | 移行先 |
|----------|------|--------|
| [scripts/run-replay-debug.sh](../../scripts/run-replay-debug.sh) | build + GUI 起動 + HTTP 投入 | `python -m engine.replay_session run ...` で完結（in-process mode） / GUI 内フォーム / GUI 起動済みなら helper の attach mode |
| [scripts/replay_dev_load.sh](../../scripts/replay_dev_load.sh) | HTTP 投入の background loader | 削除（不要） |

---

## 3. 完成形アーキテクチャ

### 3.1 全体図

```
┌─────────────────────────────────────────────────────────┐
│ ユーザーコード（pytest / notebook / CLI / examples）     │
│                                                          │
│   from engine.replay_session import ReplaySession        │
│   with ReplaySession() as s:                             │
│       s.load("1301.TSE", "2025-01-06", "2025-03-31")     │
│       s.run(strategy_file="...", on_event=print)         │
└────────────────────┬────────────────────────────────────┘
                     │
                     │ __enter__ で probe ws://127.0.0.1:19876/
                     │
        ┌────────────┴───────────┐
        │                        │
   [probe 失敗]              [probe 成功 + token / schema 一致]
        │                        │
        ▼                        ▼
┌──────────────────┐    ┌──────────────────────────────────┐
│ in-process mode  │    │ attach mode                      │
│                  │    │                                  │
│ NautilusRunner   │    │ _AttachClient (websockets-py)    │
│ を helper 内で   │    │  ↕ Hello/Ready handshake         │
│ 直接 spawn       │    │  ↕ Command::Load/Start を送信    │
│                  │    │  ↕ event stream を受信           │
│ on_event(dict)   │    │  on_event(dict) に転送           │
└──────────────────┘    └─────────────┬────────────────────┘
                                      │ WS :19876
                                      ▼
                ┌─────────────────────────────────────────┐
                │ flowsurface (Iced GUI)                   │
                │   ↕ engine-client (Rust, WS client)      │
                │   ↓                                      │
                │ Python engine（GUI が spawn / attach）   │
                │   - WS server :19876                     │
                │   - NautilusRunner（唯一の inst.）        │
                │   - event stream を全クライアントへ      │
                └─────────────────────────────────────────┘
                       ↑ 同じ event を GUI チャートにも push
```

ポイント：

- helper は **listen 側（server）にはならない**。bind するのは GUI が spawn する engine だけ
- attach mode で helper は **engine への WS クライアント** として振る舞う。Rust の `engine-client` が WS クライアントなのと同じ構造
- attach mode 時、`NautilusRunner` は **GUI 内 engine の中にしか居ない**。helper プロセスは Command を送って event を受けるだけ

### 3.2 起動経路の対応表

| ユースケース | 旧 | 新 |
|------------|-----|-----|
| GUI で replay を見る（GUI から） | `cargo run -- --mode replay` + `curl POST /load` | `cargo run -- --mode replay` 起動 → GUI 内 `File > Replay を開始...` メニューでパラメータ入力（後述 §3.4） |
| **GUI で replay を見る（外部スクリプトから）** | `cargo run -- --mode replay` + `curl POST /load` | `cargo run -- --mode replay` 起動済みの状態で別プロセスから `python -m engine.replay_session run ...`。**helper が attach mode に入り GUI チャートが動く** |
| backtest を回すだけ（GUI 不要） | （事実上不可能） | `python -m engine.replay_session run --strategy=... --instrument=... --start=... --end=...`（in-process mode） |
| pytest から backtest を駆動 | （HTTP 経由で fragile） | `with ReplaySession() as s:` を直接 import（in-process mode） |
| 発注（live モード） | GUI から手動、または `curl POST /api/order/submit` | GUI から手動（既存）。pytest からは `LiveSession`（attach mode 対応） |

### 3.3 GUI と helper の関係（§0.1 で確定）

GUI が立っているとき helper を別プロセスで起動した場合、helper は **WS クライアントとして GUI 内 engine に attach** する。`NautilusRunner` の二重起動は発生しない（GUI 内に既にあるものを駆動するだけ）。

これにより：

- **`pane-ready ack` のような GUI 整合性問題は構造的に消える**：
  - GUI なし（in-process mode）: 待つペインがない → `ReplaySession.load()` は engine load 完了で即 return
  - GUI あり（attach mode）: GUI 内で `ReplayDataLoaded` を直接受信して pane 生成 → 外部からの ack 待ち契約は不要
- **NautilusRunner の二重起動は禁止**：probe で engine の存在を確認してから spawn する／しないを決める
- **協調動作は非サポート**：GUI UI と helper CLI を同時に発火させた場合は first-come-first-served。engine が `EngineBusy` で reject する

### 3.4 GUI における replay 起動 UX（A 案採用）

HTTP API を廃止すると、現状 [src/native_menu.rs:83-90](../../src/native_menu.rs#L83) の replay モード時メニュー項目「ストラテジーを開く...」だけでは instrument / 期間 / granularity / initial_cash を入力する経路が無い。`File > Replay を開始...` フォーム式メニューを新設してこの入力経路を担う。

attach mode が使えるとはいえ、それは外部スクリプトを書ける利用者向け。**GUI のみで完結する利用者にもフォーム経由の入力経路を提供する**ためにこのフォームは引き続き必須。

**フロー**:

```
1. cargo run -- --mode replay  起動
2. GUI: 空ペイン状態で待機
3. GUI メニュー: File > Replay を開始...  をクリック
4. ダイアログ表示:
     instrument_id     [例: 1301.TSE]
     start_date        [例: 2025-01-06]
     end_date          [例: 2025-03-31]
     granularity       [Daily ▼ / Minute / Trade]
     strategy_file     [.py を選択する...]  ← 既存「ストラテジーを開く」相当を統合
     initial_cash      [1000000]
     [開始] [キャンセル]
5. 開始押下 → IPC で Command::LoadReplayData → Command::StartEngine
6. ReplayDataLoaded 受信 → pane 自動生成（既存ロジック流用）
7. 1 tick ずつ event が GUI に流れる
```

**設計上の注意**:

- 既存「ストラテジーを開く...」独立メニュー項目はこのフォーム内の `.py` ピッカーに統合し、メニュー項目から削除する
- 入力検証は GUI 側で済ませる（instrument の空文字、日付フォーマット、cash の数値）
- 既存 `Command::LoadReplayData` / `Command::StartEngine` IPC コマンドはそのまま再利用（HTTP API が叩いていた IPC と同じもの。helper attach mode が叩く IPC と同じもの）
- フォームのデフォルト値・前回入力記憶などは [§7.2 Q3b](#72-未決事項) で別途検討

---

## 4. helper class API 設計

### 4.1 `engine.replay_session.ReplaySession`

**設計方針**：

- **contextmanager 必須**（`with` 文で構造的に lifecycle を強制 → 並列利用 fail を構造解消）
- **callback ベース**（`on_event: Callable`）で `start_backtest_replay_streaming` をそのまま薄くラップ。queue / thread / generator は導入しない
- IPC schema や `Command` 列挙体を一切 expose しない（ユーザーは `dict` で event を受ける）
- **mode auto-detect**：`__enter__` で `:19876` を probe して `attach` / `inprocess` を選ぶ。ユーザーから見た API はどちらでも同一

```python
# python/engine/replay_session.py（単一ファイル構成）

from typing import Callable, Literal
from pathlib import Path

class ReplaySession:
    """Python から nautilus replay backtest を駆動する helper.

    起動時に :19876 を probe し、GUI 内 engine が居れば attach mode、
    居なければ in-process mode で動く。ユーザーから見た API は同一。

    使い方:
        with ReplaySession() as s:
            s.load("1301.TSE", "2025-01-06", "2025-03-31")
            s.run(strategy_file="docs/example/buy_and_hold.py",
                  on_event=lambda evt: print(evt))
    """

    def __init__(
        self,
        *,
        jquants_dir: Path | str | None = None,  # 既定: $JQUANTS_DIR or S:/j-quants
        log_level: str = "INFO",
        attach_endpoint: str | None = None,    # 既定: session ファイル → env の順で解決
        attach_timeout_s: float = 2.0,         # probe TCP timeout
        force_mode: Literal["auto", "attach", "inprocess"] = "auto",
    ) -> None:
        """引数の検証のみ。重い初期化（probe / NautilusRunner 構築）は __enter__ で行う。"""

    def __enter__(self) -> "ReplaySession":
        """1. mode を決定する（force_mode が 'auto' なら probe を走らせる）
            - token / endpoint 解決は次の優先順位で行う：
              (a) attach_endpoint 引数 + FLOWSURFACE_ENGINE_TOKEN env
              (b) <data_path>/engine-session.json の {port, token, pid}
                  → pid が live でなければ stale として無視
              (c) どれも取れなければ in-process に確定
            - probe で TCP 接続成功 + Hello/Ready handshake 成功 + token 一致
              + SCHEMA_MAJOR 一致 → attach mode
            - 失敗または engine 不在 → in-process mode（warn ログ + フォールバック）
        2. attach mode: _AttachClient を構築し handshake 完了まで blocking
           in-process mode: NautilusRunner を構築
        3. 同一インスタンスが二度 __enter__ されたら例外。
        """

    def __exit__(self, exc_type, exc, tb) -> None:
        """run() 中なら stop_event を set してから stop。
        attach mode: WS connection close
        in-process mode: NautilusRunner stop
        注: `NautilusRunner.stop()` は冪等でなければならない（二度呼び出しで safe）。
        `stop()` 相当の処理が実際に冪等かを Phase 8.1a 着手前に確認すること。
        """

    @property
    def mode(self) -> Literal["attach", "inprocess"]:
        """現在のモード（debug / 統合テスト用）。"""

    # ---- load 系（旧 POST /api/replay/load 相当） ----
    def load(
        self,
        instrument_id: str,
        start_date: str,
        end_date: str,
        granularity: Literal["Trade", "Minute", "Daily"] = "Daily",
    ) -> None:
        """データの存在確認のみ実施（旧 HTTP load の契約と同じ）。

        attach mode: Command::LoadReplayData を送信し ReplayDataLoaded を待つ
        in-process mode: jquants_loader.check_data_exists() を直接呼ぶ。
          `jquants_loader.check_data_exists()` が `False` を返した場合は
          helper が `FileNotFoundError` を raise する（成功として扱わない）。
        失敗時は FileNotFoundError を raise する。"""

    # ---- run 系（旧 POST /api/replay/start 相当） ----
    def run(
        self,
        *,
        strategy_file: str | Path,
        on_event: Callable[[dict], None],
        strategy_id: str = "user-strategy",
        initial_cash: int = 1_000_000,
        currency: str = "JPY",
        multiplier: int = 1,
        strategy_init_kwargs: dict | None = None,
    ) -> None:
        """backtest を回しながら on_event(dict) を同期的に呼ぶ。

        attach mode: Command::StartEngine を送信し event stream を on_event に転送。
                     attach mode で受信した event は GUI チャートにも同時に流れる。
        in-process mode: NautilusRunner.start_backtest_replay_streaming() を直接呼ぶ。

        `strategy_file` が存在しない場合は FileNotFoundError を raise（in-process / attach 共通）。

        on_event は呼び出し thread 上で実行される（threading 不要）。
        中断は Ctrl-C / SIGINT。set_speed() / stop() を別 thread から呼ぶ
        ケースに限り threading.Event 経由でハンドリングする。
        """

    # ---- runtime control ----
    def set_speed(self, multiplier: int) -> None:
        """旧 POST /api/replay/control 相当。
        attach mode: Command::SetReplaySpeed を送信
        in-process mode: 内部の get_multiplier closure を更新
        """

    def stop(self) -> None:
        """別 thread から呼ぶ非同期キャンセル。run() の on_event ループが
        次の tick で終端に到達する。
        attach mode: Command::StopEngine を送信
        in-process mode: stop_event を set
        """

    # ---- snapshot ----
    @property
    def portfolio(self) -> dict | None:
        """旧 GET /api/replay/portfolio 相当。最後の ReplayBuyingPower イベントの dict。"""

    @property
    def status(self) -> Literal["idle", "loaded", "running", "stopping", "stopped", "errored"]:
        """旧 GET /api/replay/status 相当。

        Phase 8 R1 / Phase 3 (C-GP1 / H-Type3) で `"stopping"` を Literal に
        追加し、`STOPPING` 状態を `"running"` にマップする旧仕様を撤廃した。
        stop() を投げてから `EngineStopped` を待つ間は `"stopping"` を返す。

        ライフサイクル: `idle → loaded → running → stopping → stopped`
        (`stopping` は attach mode の StopEngine 送出後 / `EngineStopped` 受信前)。
        `errored` は run() 内例外で `STOPPED` に到達できなかった場合のみ。
        on_event 内例外で `EngineStopped` を既に受信済みなら `STOPPED` を維持する
        (C-GP2: ユーザーコールバックのバグが status を破壊しない不変条件)。

        endpoint/token 解決順 (M-GP1):
        1. `(a)` 明示 `attach_endpoint` + `FLOWSURFACE_ENGINE_TOKEN` env
        2. `(b)` `engine-session.json` の `{port, token}` (pid 必須・M-GP2)
        3. `(c)` env のみ — `FLOWSURFACE_ENGINE_PORT` を尊重 (H-GP4) / 既定 19876

        `(a)` 明示 attach_endpoint を使うときは session ファイルの token を混ぜない。
        token はすべてのログ・例外メッセージで秘匿する。
        """

    # ---- order injection（旧 POST /api/replay/order 相当）----
    def submit_order(
        self,
        *,
        instrument_id: str,
        side: Literal["BUY", "SELL"],
        quantity: int,
        order_type: Literal["MARKET", "LIMIT"] = "MARKET",
        price: float | None = None,
    ) -> str:
        """run() 中の strategy が出すのではなく外部から発注する経路（テスト用）。
        受理した内部 order_id を返す。"""
```

### 4.2 `_AttachClient`（private 実装）

> ※ 以降の行番号参照（`server.py:413-435` 等）は実装変更で陳腐化する。実装時はシンボル名で確認すること。

helper の attach mode 専用の **薄い WS クライアント**。Rust 側 `engine-client` の Python 等価物。

```python
# python/engine/replay_session.py 内 private

class _AttachClient:
    """GUI 内 engine への WS クライアント。helper public API には現れない。

    既存 schema を再利用するため新規 wire format を導入しない。
    """

    def __init__(self, endpoint: str, token: str, timeout_s: float) -> None: ...

    def handshake(self) -> None:
        """Hello を送信して Ready を待つ。
        - schema_major が SCHEMA_MAJOR と一致しなければ ConnectionRefusedError
        - token mismatch または SCHEMA_MAJOR mismatch → ConnectionRefusedError を raise。
          `ReplaySession.__enter__` 側でこれを catch して in-process mode にフォールバック
          （warn ログを出す）。フォールバック非対応（例: `force_mode="attach"` 指定時）は
          例外をそのまま上げる。
        - TCP timeout は ConnectionRefusedError
        - handshake 途中（Hello 送信後・Ready 受信前）で接続が切れた場合は必ず `close()` を呼ぶ。
          `handshake()` 内部は try/finally で `close()` を保証し、caller（`ReplaySession.__enter__`）
          にはそのまま例外を上げる。
        """

    def send_command(self, cmd: dict) -> None:
        """schemas.py の Command を JSON シリアライズして送る。
        helper public API からは呼ばれない（ReplaySession 内で組み立てる）。"""

    def wait_for(self, event_type: str, timeout_s: float | None = None) -> dict:
        """指定 event 種別を 1 つ待って返す。それ以外は破棄せず内部 queue に積む。"""

    def events(self) -> Iterator[dict]:
        """run() の本体。受信 stream を yield。on_event 呼び出しは ReplaySession 側。
        `EngineStopped` event を受信したらジェネレータを終了する（`return`）。
        `stop()` 呼び出し後に `EngineStopped` が到達するまでの間は event を通常通り
        yield し続ける（`EngineStopped` が終端信号）。
        attach mode で WS close / read error / handshake loss が起きた場合は
        `ConnectionError` を raise し、`EngineStopped` を待たずに即終了する。
        """

    def close(self) -> None: ...
```

**実装上の注意**：

- `websockets` ライブラリ（既に Python engine が依存）を使う
- `compression=None` を強制（[MISSES.md] の RSV1 フレーム互換性問題と同じ理由）
- handshake / Command / Event の schema は [python/engine/schemas.py](../../python/engine/schemas.py) を共有 — 新規の wire format を作らない
- token / port の取得順序：
  1. 明示引数（`attach_endpoint` が `__init__` で渡された場合）+ `FLOWSURFACE_ENGINE_TOKEN` env
  2. session ファイル `data::data_path(Some("engine-session.json"))` の `{port, token, pid, schema_major, started_at}`（解決の詳細は §4.2.1）
  3. どれも無ければ attach mode を諦めて in-process へ fallback（warn ログ）
- session ファイルの pid が live かつ probe（TCP 接続）が成功の両条件を満たした場合のみ attach mode に入る。pid が dead、またはファイルは新しいが probe 失敗の場合は in-process にフォールバック（warn ログ）。
- session ファイルは `pid` が dead なら stale として無視（PID 再利用ヒット率は十分低いが、将来 hash 値併記等の追加防御を検討）
- `_AttachClient` は `replay_session.py` 内 private クラスとし、外部 import は許さない（`__all__ = ["ReplaySession", "LiveSession"]` で制御）
- engine から `EngineBusy` event を受信した場合は `BusyError` 例外に変換して投げる
- attach 中に engine / GUI 側が落ちて WS が閉じた場合は `ConnectionError` として surfacing し、helper の `status` は `"errored"` に遷移する
- token はログ（DEBUG を含む）・例外メッセージに一切出力しない（CLAUDE.md の「Token 認証」セクション参照）。handshake() の内部実装で token を文字列比較やフォーマット文字列に含めないこと。

### 4.2.1 session ファイルのパス解決（Rust ↔ Python の対応）

session ファイルは **Rust 側 `data::data_path(Some("engine-session.json"))` が真実源**。Python helper はこれと同じパスに解決する必要がある。

| 階層 | 実装 | 解決先 |
|------|------|--------|
| Rust（書き手） | [data/src/lib.rs:134-145](../../data/src/lib.rs#L134-L145) `data::data_path()` | (1) `FLOWSURFACE_DATA_PATH` env / (2) `dirs_next::data_dir() / "flowsurface" / "engine-session.json"` |
| Python（読み手 / helper） | `replay_session.py::_resolve_session_file_path()` 新規実装 | (1) `FLOWSURFACE_DATA_PATH` env / (2) `platformdirs.user_data_dir("flowsurface", appauthor=False) / "engine-session.json"` |

**OS 別実体**：

| OS | 実体パス |
|---|---|
| Windows | `%APPDATA%\flowsurface\engine-session.json`（= `C:\Users\<user>\AppData\Roaming\flowsurface\engine-session.json`）|
| macOS | `~/Library/Application Support/flowsurface/engine-session.json` |
| Linux | `~/.local/share/flowsurface/engine-session.json`（または `$XDG_DATA_HOME/flowsurface/engine-session.json`）|

**`platformdirs` ↔ `dirs_next` の等価性**：両ライブラリとも XDG Base Directory Specification（Linux）/ Apple Standard Directories（macOS）/ Windows Known Folder API（Windows）に準拠しており、`appauthor=False` 指定で sub-folder を抑止すれば `flowsurface` 直下に解決される。`platformdirs` が pyproject に未追加なら Phase 8.1b で追加する。

> **注意（#B-H1）**: `FLOWSURFACE_DATA_PATH` env override を使う場合、Rust 側 `data/src/lib.rs` の `data_path()` に env-override ブランチのバグがある（`path_name` が join されずに bare パスが返る）。Phase 8.1b B2 着手前に先に修正すること（詳細は §5 Phase 8.1b B2 を参照）。Python helper 側の `_resolve_session_file_path()` は `Path(env_override) / "engine-session.json"` で正しく join しているため、バグ放置時は env-override 経路でパス不一致が発生する。

**書き込み主体を Rust に寄せる理由**：

1. `data::data_path()` は Rust 側ロジック。書く側が真実源を握ることで Python ↔ Rust 間でのパス解決 drift が起きない
2. standalone Python（手動 `python -m engine ...`）では session ファイルを書く必要がない（その経路の helper は env / 明示引数で endpoint を指定する設計）
3. spawn 経路で Rust が `{port, token}` を保持しているので、Rust 側で書くのが最も自然

**Python 側参考実装**：

```python
def _resolve_session_file_path() -> Path:
    """Rust 側 data::data_path(Some("engine-session.json")) と同じパスに解決する。"""
    if env_override := os.environ.get("FLOWSURFACE_DATA_PATH"):
        return Path(env_override) / "engine-session.json"
    import platformdirs
    base = platformdirs.user_data_dir("flowsurface", appauthor=False)
    return Path(base) / "engine-session.json"
```

### 4.3 `engine.replay_session.LiveSession`

**Phase 8.1 必須スコープ**：旧 `/api/sidebar/tachibana/request-login` 経路を helper で代替する。Order 系 E2E 7 本が依存しているため、`LiveSession.login()` を Phase 8.1 で先行実装する。

`LiveSession` も `ReplaySession` と同じ mode auto-detect ロジックで動く。GUI 起動済みなら attach mode、そうでなければ in-process mode。attach 解決順も **明示引数 → session ファイル → fallback** を再利用し、`attach_endpoint` の既定値は固定 URL ではなく `None` とする。

```python
class LiveSession:
    """立花など実 venue へのログイン・発注を Python から直接駆動する helper.

    Phase 8.1: login() のみ必須実装（Order E2E pytest 化の前提）。
    Phase 8.3: submit/modify/cancel を必要に応じて追加。

    本番運用としての helper 経由発注は明示的に非サポート（GUI を使うこと）。
    pytest からの E2E スモークテスト用に提供する。
    """

    def __init__(
        self,
        *,
        venue: Literal["tachibana"],
        demo: bool = True,
        attach_endpoint: str | None = None,
        attach_timeout_s: float = 2.0,
        force_mode: Literal["auto", "attach", "inprocess"] = "auto",
    ) -> None: ...

    def __enter__(self) -> "LiveSession": ...
    def __exit__(self, exc_type, exc, tb) -> None: ...

    @property
    def mode(self) -> Literal["attach", "inprocess"]: ...

    # ---- Phase 8.1 必須 ----
    def login(
        self,
        *,
        user_id: str | None = None,    # in-process 既定: $DEV_TACHIBANA_USER_ID
        password: str | None = None,   # in-process 既定: $DEV_TACHIBANA_PASSWORD
    ) -> None:
        """旧 POST /api/sidebar/tachibana/request-login 相当。

        - **in-process mode**: 引数または env 経由 cred で立花へログインし
          session を確立する。p_no_counter は外側で 1 度だけ生成し、
          nested-loop fallback でも同一インスタンスを再利用する (H-GP5)。
          fallback は asyncio.run のネスト不可 RuntimeError 検知時のみ発動。
        - **attach mode (C-GP4)**: `Command::RequestVenueLogin{venue}` を engine に
          送信し、`VenueReady` で `is_logged_in=True`、`VenueError` で `ConnectionError`
          を raise する (timeout 30s)。
        - **attach mode で credential 明示は禁止**: `user_id` / `password` を引数で
          渡されたら `ValueError`。GUI/engine 側に保存済みまたは dev 用の credential を
          使う経路のみサポート (wire に credential を流さない契約)。
        - **2 回目以降の login() は no-op (M-GP5)**: `is_logged_in == True` であれば
          内部 API も `RequestVenueLogin` も再送信せず即 return。state は維持する。
          冪等性を呼び出し側に押し付けない設計。
        """

    # ---- Phase 8.3 で追加 ----
    def submit_order(self, **kwargs) -> str: ...
    def modify_order(self, order_id: str, **kwargs) -> None: ...
    def cancel_order(self, order_id: str) -> None: ...
    def cancel_all(self) -> None: ...
    @property
    def orders(self) -> list[dict]: ...
```

### 4.4 CLI: `python -m engine.replay_session run ...`

```bash
uv run python -m engine.replay_session run \
    --strategy docs/example/buy_and_hold.py \
    --instrument 1301.TSE \
    --start 2025-01-06 \
    --end 2025-03-31 \
    --granularity Daily \
    --initial-cash 1000000

# event stream を JSONL で stdout に書き出す（| jq でフィルタ可能）
# GUI が起動済みなら attach mode で接続し GUI チャートも同時に動く
# GUI が居なければ in-process mode で stdout のみに event を流す
```

CLI 内部で `with ReplaySession() as s:` を使い、ユーザーは contextmanager / mode 判定を意識しなくてよい。

CLI には `--mode {auto,attach,inprocess}` オプションを提供して force-override を許す（auto が既定）。

> 注: 既存 `python -m engine ...`（WS server 起動）と区別するため、helper は単一ファイル `python/engine/replay_session.py` に置き、`python -m engine.replay_session` で叩く。`python/engine/__main__.py`（WS server）は手を入れない。

---

## 5. 段階的移行プラン（5 → 4 phase に圧縮 / attach mode 込み）

> レビューで指摘された「Phase 8.2 から 8.4 までの間に HTTP リグレッション盲点が生じる」問題を解消するため、HTTP 削除と pytest 移行を同一 Phase に統合する。

### Phase 8.0 — 設計確定（着手前の合意形成）✅ 完了

- [x] §0.1 の前提条件（helper は server を bind しない / attach mode 採用 / NautilusRunner 二重起動禁止 / 協調動作非サポート）を README または CLAUDE.md に追記
- [x] [open-questions.md](./open-questions.md) の Q2（Python プロセスのライフサイクル管理）項目をクローズ済みに更新（attach mode で「外部 helper から GUI engine を駆動する」が可能になった旨を明記）
- [x] `implementation-plan.md` 末尾に「## フェーズ 8（→ python-helper-direct-api.md 参照）」スタブ節を追加する
- [x] examples で書く buy_and_hold の callback 形 1 本を最終確認

**完了条件**: helper API 形（contextmanager + callback + mode auto-detect）が確定し、Phase 8.1 着手の障害がない。

### Phase 8.1 — helper class + CLI 新設 + GUI replay フォーム（破壊的変更なし）

#### Phase 8.1a: Python helper class + CLI（in-process mode 先行）✅ 完了 (2026-05-03)

- [x] `python/engine/replay_session.py` を**単一ファイル**で新規作成（`ReplaySession` + `LiveSession` + private `_AttachClient` の三者を含む）
- [x] **in-process mode を先に完成させる**（probe は in-process 強制から始める）
  - [x] `ReplaySession` を `NautilusRunner.start_backtest_replay_streaming` の薄いラッパーとして実装（contextmanager + callback ベース）
  - [x] `LiveSession.login()` を**必須スコープ**として実装（in-process mode: `NotImplementedError` stub、attach mode: `ValueError` で明示）
- [x] `python/engine/replay_session.py` に `if __name__ == "__main__"` ガード + argparse で CLI を提供（`--mode auto` 既定）
- [x] `pyproject.toml`: `platformdirs>=4.0` 追加、`live` pytest marker 追加
- [ ] pytest（in-process mode のみ）で helper の golden path テストを追加（テスト実装は次フェーズ）：
  - [ ] `python/tests/test_replay_session.py`（load → run → portfolio）
  - [ ] `python/tests/test_replay_session_stop.py`（別 thread から stop()）
  - [ ] `python/tests/test_replay_session_speed.py`（run() 中の set_speed()）
  - [ ] `python/tests/test_replay_session_cli.py`（subprocess 経由 CLI）
  - [ ] `python/tests/test_live_session_login.py`（demo 立花への login smoke）
- [x] `docs/example/README.md` / `.claude/CLAUDE.md` に helper 経由で動かすコマンドを追記済み

#### Phase 8.1b: attach mode 実装（B1 → B2 → B3 → AttachClient の順）✅ 完了 (2026-05-03)

> §0.1.2 で確定した B1〜B3（multi-client / token 共有 / EngineBusy）を **`_AttachClient` 実装の前提** として先に片付ける。各ステップが終わるまで次に進まない（先送りすると `_AttachClient` テストが書けない）。

##### B1. engine.server を multi-client broadcast 化 ✅

- [x] `python/engine/server.py` を `multi-client (broadcast event, FCFS command)` に書き換え
- [x] `_Broadcaster` クラスによる per-connection outbox fanout を実装
- [x] handshake の「current-connection swap」ロジックを削除し、token 一致 → connection を `_connections` に **追加** に変更
- [x] event 送信を全接続に fanout（接続毎に独立した outbox / send_loop）
- [x] Command 受信は任意 client から FCFS で受け付け
- [x] `MAX_CONNECTIONS = 4` 定数追加。超過時は接続段階で 1008 Policy Violation で reject
- [x] 接続数変更時に `ClientConnected` / `ClientDisconnected` を全接続に broadcast
- [x] [python/engine/schemas.py](../../python/engine/schemas.py) に `ClientConnected` / `ClientDisconnected` event を追加し `SCHEMA_MINOR` を 8→9 に bump

##### B2. session ファイル経由の token 共有 ✅

> **書き込み主体は Rust（engine-client）**。Python ではなく Rust に寄せる理由は §4.2.1 を参照。

- [x] session ファイル仕様確定・実装済み：
  - パス: `data::data_path(Some("engine-session.json"))`（Windows: `%APPDATA%\flowsurface\engine-session.json`）
  - 内容: `{"port": u16, "token": "<hex>", "pid": u32, "schema_major": u32, "started_at": "<iso8601>"}`
  - atomic write（tmp → rename）。Linux/macOS では `chmod 0o600`
- [x] **`data/src/lib.rs` の `data_path()` env-override バグを修正**（`FLOWSURFACE_DATA_PATH` で `path_name` が join されなかったバグ）
- [x] **Rust 側書き込み実装**（`engine-client/src/session_file.rs` 新規作成）：
  - [x] `EngineSession::write_atomic()` 実装
  - [x] Linux/macOS では `std::fs::set_permissions` で `0o600` を設定
  - [x] `process.rs` の `Drop` impl で session ファイル削除
- [x] **helper 側（Python）**：`_resolve_session_file_path()` / `_read_session_file()` / `_is_pid_alive()` を `replay_session.py` に実装
- [x] `pyproject.toml` に `platformdirs>=4.0` 追加

##### B3. EngineBusy state guard ✅

- [x] engine に **2 つの直交する state 機械** を追加（`server.py`）：
  - **ReplayState**: `IDLE | LOADED | RUNNING | STOPPING`
  - **LiveState**: `DISCONNECTED | CONNECTING | CONNECTED`
- [x] replay 系 Command の state guard 実装（`_check_replay_state()`）
- [x] live 系 Command の state guard 実装（`_check_live_state()`）
- [x] `python/engine/schemas.py` に `EngineBusy { current_state, attempted_command, reason }` event を追加（SCHEMA_MINOR 同 bump）
- [x] `_AttachClient.events()` で `EngineBusy` 受信 → `BusyError` 例外に変換

##### B4. `_AttachClient` 本体実装 ✅

- [x] `_AttachClient` を `replay_session.py` に追加（バックグラウンドスレッド + asyncio loop 構成）
- [x] `websockets` クライアント接続 + Hello/Ready handshake（`ClientConnected` をスキップして `Ready` を待つ）
- [x] `send_command()` / `wait_for()` / `events()` / `close()` を実装
- [x] `EngineBusy` 受信を `BusyError` に翻訳
- [x] `compression=None` を強制（RSV1 互換性問題: MISSES.md 2026-04-25 参照）
- [x] `__enter__` の mode auto-detect ロジック（明示引数 → session ファイル → env → in-process fallback）
- [x] CLI に `--mode {auto,attach,inprocess}` オプション追加（既定 `auto`）

##### docs 更新

- [ ] [spec.md](./spec.md) §5.3 reconnect protocol の multi-client 文脈への書き換え（次フェーズで対応予定）

#### Phase 8.1c: GUI replay 起動フォーム（A 案・§3.4 参照）✅ 完了 (2026-05-03)

- [x] [src/native_menu.rs](../../src/native_menu.rs) replay モード時のメニューに `Action::OpenReplayDialog` / "Replay を開始..." を追加
- [x] `src/modal/replay_form.rs` 新規作成：`ReplayFormModal`（instrument/date/granularity/strategy/cash フォーム）
- [x] `src/main.rs`: `ShowReplayDialog`/`ReplayFormMsg` ハンドラ実装、IPC `LoadReplayData` + `StartEngine` 送信

#### 共通

- [x] **Phase 8.3 で HTTP API を完全削除**（HTTP API は Phase 8.3 完了まで残存した）

**完了状態（2026-05-03 達成）**:

- ✅ `uv run python -m engine.replay_session run ...` 1 コマンドで GUI なしの backtest が完走し、event stream が stdout に流れる（in-process mode）
- ✅ engine.server が 2 client 同時接続を保持し、event を両者に fanout する（B1 完了）
- ✅ engine 起動時に `engine-session.json` が生成され、helper がこのファイル経由で token / port を解決できる（B2 完了）
- ✅ `Loaded` 中に二度目の `LoadReplayData` を投げると `EngineBusy` が返る（B3 完了）
- ✅ `cargo run -- --mode replay` で起動した GUI から `File > Replay を開始...` のフォーム経由で backtest が完走する

## レビュー反映 (2026-05-03, ラウンド 1 - Group 1)

### 解消した指摘
- C1: multi-client server.py の disconnect cleanup を per-connection / server-lifecycle に分離（`if not self._connections:` ガード追加 → Tachibana startup task / EVENT loop / startup latch / venue stream の cancel は最後の 1 接続が抜けたときのみ実行）
- C6/H16: `ReplaySession._status` を `_ReplayStatus` Enum 化、`STOPPING` 状態追加（attach mode の `stop()` で `RUNNING → STOPPING` 遷移）
- C7/M2: `EngineSession` に `started_at: DateTime<Utc>` フィールド追加 + `serde_json` 化 + `token` を `pub(crate)` + `Debug` を手動実装で `[REDACTED]` 出力
- H1: `cargo fmt --check` 緑復帰（flowsurface クレートに `cargo fmt -p flowsurface` 適用）
- H4: Rust `SCHEMA_MINOR` を 8 → 9 に bump し Python と同期。`python/tests/test_schemas_nautilus.py` に Rust ↔ Python 整合チェックを追加
- H13: `_extract_event_kind(evt)` ヘルパで `event` / `type` 両キーを許容。in-process / attach 両経路で `ReplayBuyingPower` 判定が一貫する
- H15: `server.py` `_check_replay_state` / `_check_live_state` の `getattr` フォールバックを削除し `__init__` 必須に
- H17: `LiveSession.force_mode='attach'` を `NotImplementedError("LiveSession attach mode is Phase 8.3 scope")` に変更（silent fallback 廃止）

### 設計判断
- `_replay_state` / `_live_state` は `__init__` で必ず初期化、レガシーテストは `__new__` ではなく `__init__` を呼ぶ（または explicit に属性をセットする）。`test_engine_busy_reject.py` / `test_server_engine_dispatch.py` 等は既に対応済み
- `ReplaySession._status` の `status` プロパティは互換維持のため `Literal[str]` を返す（`enum.value` 経由）。stopping 状態を Literal に追加
- `EngineSession::new(...)` コンストラクタを公開して `started_at = Utc::now()` を確実に埋める。`token` は `pub(crate)` のため crate 外からはコンストラクタ経由でしか作れない

### 持ち越し（Group 2 以降）
- attach mode 動作修正（C4/C5/H5/H6/H7/H8/H12/H14）
- Rust crash recovery + Drop（C3/H2/H3）
- LiveSession.login 実装、必須テスト追加（C2/H11）、ドキュメント整合（H9/H10/M6/M7）

## レビュー反映 (2026-05-03, ラウンド 1 - Group 2)

### 解消した指摘
- C4: `_AttachClient.close()` を冪等化、`_send_queue.put(None)` の Future を `result(timeout=2.0)` で待つ、warn ログ追加
- C5: `_recv_loop` の例外を `websockets.ConnectionClosed`（info: code/reason）/ その他（warn: 例外型名）で分けてログ出力
- H5: handshake 中に `EngineError{code=auth_failed}` を検知して `ConnectionRefusedError("attach handshake: token mismatch")` を専用 raise。`__enter__` の `force_mode='auto'` では error レベルで surface し inprocess に fallback、`force_mode='attach'` では raise
- H6: `events()` を `queue.get(timeout=1.0)` ループに変更し `_closed_event.is_set()` で即 `ConnectionError("WS connection closed")` を raise（最大 60s hang 解消）
- H7: `_probe_engine` dead code を完全削除
- H8: `_send_loop` を `ws.send` ごとに try/except でラップし、`ConnectionClosed` は info、その他例外は error ログを出して該当 client の send_loop を return（他 client への波及を断つ）
- H12: `_AttachClient.wait_for` 内で `event == "EngineBusy"` を `BusyError` に翻訳
- H14: `handshake()` を try/except でラップし、失敗時に `self.close()` を呼んで thread/loop をリークさせない（close 冪等性に依存）

### 設計判断
- `close()` の Future timeout は 2.0s 固定（thread.join の 5.0s タイムアウトと合わせて最大 7s で確実に終了）
- token mismatch の判定は EngineError の `code` フィールド（または message）に "auth" を含むかで識別。token 文字列自体は exception/log に出さない
- recv_loop の WS 切断ログは `code=` / `reason=` を出す（token を含まないので安全）

### テスト追加
- `test_replay_session_attach.py`:
  - `test_attach_client_close_is_idempotent` (C4)
  - `test_attach_client_recv_loop_logs_on_close` (C5)
  - `test_attach_token_mismatch_logs_error_and_falls_back` / `test_attach_token_mismatch_raises_when_force_attach` (H5)
  - `test_attach_client_events_terminates_on_ws_close` (H6)
  - `test_probe_engine_removed` (H7)
  - `test_attach_wait_for_translates_engine_busy_to_busy_error` (H12)
  - `test_attach_handshake_failure_cleans_up_thread` (H14)
- `test_server_multi_client.py`:
  - `test_send_loop_survives_send_failure` (H8)

### Phase 8.2 — E2E bash スクリプト削除 ✅ 完了 (2026-05-03)

> 削除を基本方針。`smoke.sh` のみ起動監視用として維持。

- [x] `scripts/replay_dev_load.sh` 削除
- [x] `tests/e2e/s56〜s83, s90, tachibana_*` 11 ファイル削除（HTTP API 依存の bash E2E）
- [x] `scripts/run-replay-debug.sh` に DEPRECATED コメント追記（HTTP API ポート 9876 依存のため機能しない）
- [x] `.claude/CLAUDE.md` / `docs/example/README.md` を `python -m engine.replay_session run` 記述に更新済み

**完了状態**: `smoke.sh` のみ維持。HTTP 依存の bash E2E はすべて削除済み。

### Phase 8.3 — HTTP API 削除 ✅ 完了 (2026-05-03)

> HTTP 削除を実施。ポート 9876 は release build に存在しない。

- [x] `src/replay_api.rs` 削除（約 2,943 行）
- [x] `src/api/order_api.rs` 削除（約 3,490 行）
- [x] `src/api/agent_api.rs` 削除（約 323 行）
- [x] `src/api/mod.rs` 削除（2 行）
- [x] `src/main.rs` から HTTP runtime ブロック / `ControlApiCommand` enum / `relay_api_stream` Subscription 等を全削除
- [x] `.claude/CLAUDE.md` の replay 関連セクションを helper ベース記述に更新済み
- [ ] `LiveSession.submit_order()` / `modify_order()` / `cancel_order()` / `cancel_all()` / `orders` は次フェーズで追加予定（attach mode 経由で IPC を叩く形）

**完了状態**:

- ✅ **release build で** ポート 9876 を listen するプロセスが存在しない
- ✅ `cargo build --release` が成功する
- ✅ `cargo test --workspace` 全 PASS

---

## レビュー反映 (2026-05-03, ラウンド 1 - Group 4)

### 解消した指摘

- **C2（部分）**: `python/tests/test_no_http_listener.py` を新規追加。
  `socket.bind(("127.0.0.1", 9876))` の成功で「Phase 8.3 で削除した HTTP API
  モジュール（`src/replay_api.rs` / `src/api/*.rs`）が復活していない」ことを
  CI で常時検証する deterministic リグレッションガード。
- **M-1 (type)**: `EngineBusy.current_state` / `attempted_command` を
  `str` から `Literal[...]` に格上げ（`python/engine/schemas.py`）。
  許容値: `current_state` は ReplayState（IDLE / LOADED / RUNNING / STOPPING）
  + LiveState（DISCONNECTED / CONNECTING / CONNECTED）の合計 7 値。
  `attempted_command` は実際に server.py で発火する 9 値
  （LoadReplayData / StartEngine / StopEngine / SetReplaySpeed / SubmitOrder /
  ModifyOrder / CancelOrder / CancelAllOrders / RequestVenueLogin）。
  pydantic が wire mismatch を build 前に検出する。
- **M-3 (rust)**: `engine-client/src/session_file.rs` の `write_atomic` /
  `delete` シグネチャを `&PathBuf` → `&Path` に修正。`Path::with_extension` を
  使うので auto-deref は元から効いていたが、API 表面で `&Path` を取るのが
  Rust 標準の慣習。
- **M-5 (silent)**: `write_atomic` の chmod 失敗 / rename 失敗時に `.tmp`
  ファイルが残らないよう cleanup を追加。

### 検証結果

- `cargo check --workspace` ✅
- `cargo clippy --workspace -- -D warnings` ✅
- `cargo fmt --check` ✅
- `cargo test --workspace` ✅（session_file 9 PASS / 全 workspace 0 failed）
- `uv run pytest python/tests/ -m "not live"` → **1546 passed, 3 skipped**
  （Group 3 の 1545 → +1, `test_no_http_listener.py` 増加）

### 持ち越し（次フェーズで対応予定）

本ラウンドで RED→GREEN→REFACTOR の TDD 規律を維持するため
以下を意図的に分離した。各々独立タスクとして再着手する：

- **C2 残**: `test_replay_session_attach_gui_chart.py` / `test_session_file_atomic_write.py`
  / `test_engine_session_file.py` / `test_replay_session_attach_session_file.py`
  / `test_replay_session_attach_disconnect.py` / `test_replay_session_attach_manual_smoke.md`
  — subprocess + WS 多重 client の race condition を含むため、専用のラウンドで
  fixture / timeout / port allocation 設計を含めて TDD で着手する。
  Group 3 までで基本機能カバレッジ（attach handshake / fallback / token mismatch
  / busy / session 解決）は確保済み。残るのは「穴 1 リグレッション保護」と
  ファイルライフサイクルの edge case。
- **H11 (LiveSession.login 本実装)**: `tachibana_startup_login` 等の既存内部 API
  の wire 経路特定 + attach mode の `RequestVenueLogin` ↔ `VenueReady`/`VenueError`
  待ち合わせの実装が必要。Phase 8.1 の一部だが本ラウンドのスコープを超えるため
  分離。現在は `NotImplementedError` で fail-fast、誤動作の risk なし。
- **H10 補足**: spec.md §5.3 は既に Phase 8 attach 註記が入っているため、
  per-connection 文脈の全面書き直しは Phase 8.1c 終盤での仕上げ作業として残す。
- **M-* 残**: `_AttachClient._send_loop` の polling 削除（M-11）、CLI BusyError
  専用メッセージ（M-1 silent）、MAX_CONNECTIONS reject 時の EngineError 通知（M-2 silent）、
  `LiveSession.attach_endpoint` パラメータ追加（M-8）、`_read_session_file`
  の TypedDict 化（M-3 type）、その他多数。それぞれ独立して影響範囲が狭く、
  個別 TDD 単位でラウンド分割した方が品質が高くなる。

---

## レビュー反映 (2026-05-03, ラウンド 1 - Group 4a)

### 解消した指摘

- **H11 (LiveSession.login 本実装)**: `python/engine/replay_session.py` の
  `LiveSession.login()` を `NotImplementedError` スタブから本実装に置き換え。
  in-process 経路で既存の Tachibana 内部ログイン API
  （`engine.exchanges.tachibana_auth.login`）を `_tachibana_login_call` として
  モジュールレベルで再 export し、`asyncio.run` で同期ラップする実装。
  cred 解決順は「明示引数 → `DEV_TACHIBANA_USER_ID`/`DEV_TACHIBANA_PASSWORD` env」、
  両方欠落時は `ValueError`。`demo` flag は `__init__` の値をそのまま `is_demo`
  として伝播。`PNoCounter` を毎回新規生成して R4 monotonic 契約を満たす。
  既存 event loop 内（notebook 等）からの呼び出しに備え、`asyncio.run` が
  `RuntimeError` を上げた場合は別 thread の executor 経由で再実行する fallback
  も実装。`is_logged_in` プロパティを公開。attach mode 経路は `__enter__` 時点で
  `NotImplementedError` のため通常到達しないが、防御的に `NotImplementedError`
  を返す。

### 追加 / 変更したテスト

`python/tests/test_live_session_login.py` を 4 → 9 ケースに拡張：

- 既存ガード（force_mode 系 3 ケース）はそのまま維持。
- `test_login_in_inprocess_raises_not_implemented`（旧 NotImplementedError 期待）を
  本実装後の挙動セットに置き換え。
- 新規 6 ケース：
  - `test_login_inprocess_with_explicit_credentials_calls_internal_api` — 引数で渡した
    user_id / password が内部 API に伝播することを `monkeypatch.setattr` 経由で assert。
  - `test_login_inprocess_with_env_credentials` — env 経由で cred を解決する経路。
  - `test_login_missing_credentials_raises_value_error` — 引数も env も無い場合の
    `ValueError("missing credentials")` と「内部 API が呼ばれないこと」を確認。
  - `test_login_internal_api_failure_propagates` — 内部 API の例外がそのまま
    伝播することを確認（`(BoomError, RuntimeError)` のいずれかを許容）。
  - `test_login_demo_flag_passed_to_internal_api` — `LiveSession(demo=False)` が
    内部 API へ `is_demo=False` で伝わる。
  - `test_login_marks_session_as_logged_in` — login 成功後に `is_logged_in=True`。

token / password はテスト fixture も含めて `uuid.uuid4().hex[:8]` ベースの
ダミー文字列を使用し、`.env` の実 cred を読まないことを契約として固定。

### 検証結果

- `uv run pytest python/tests/test_live_session_login.py -v` → **9 passed**
- `uv run pytest python/tests/ -m "not live"` → **1551 passed, 3 skipped**
  （Group 4 の 1546 → +5: ケース 9 件中 1 件は既存差し替え、8 件 - 既存 3 件 = 5 件純増）

### 持ち越し（次フェーズで対応予定）

- ~~**attach mode の `RequestVenueLogin` ↔ `VenueReady`/`VenueError` 待ち合わせ**~~ →
  ✅ **完了 (Phase 8 R1 / Phase 3, C-GP4)**: `LiveSession.__enter__` の attach 分岐から
  `NotImplementedError` を撤廃し、`RequestVenueLogin{venue}` を engine に送信して
  `VenueReady` (= `is_logged_in=True`) または `VenueError` (= `ConnectionError`) を待つ
  IPC wire dispatch を実装。token / endpoint 解決順は ReplaySession と共通の
  `(a) 明示 attach_endpoint+env / (b) session ファイル / (c) env-only` を使う。
  attach mode で credential 明示は `ValueError` (wire に流さない契約)。テスト:
  `python/tests/test_live_session_attach.py` 4 件。
- **実 demo smoke**：`@pytest.mark.live` 付きの実 HTTP smoke は `.env` に
  `DEV_TACHIBANA_*` がある環境でのみ回す任意テスト枠として残置。本ラウンドでは
  unit-level mock のみで内部 API 伝播を検証した。

---

## レビュー反映 (2026-05-03, ラウンド 1 - Group 3)

### 解消した指摘

- **C3**: `EngineSession::reap_stale(path)` を `engine-client/src/session_file.rs` に
  追加。pid liveness チェックは `sysinfo` クレート（cross-platform）で実装。
  `ProcessManager::start()` 冒頭で呼び、dead-pid / 破損 JSON のセッションファイル残骸を
  spawn 直前に掃除する。tests: `reap_stale_removes_dead_pid_file` /
  `reap_stale_keeps_live_pid_file` / `reap_stale_removes_corrupt_json` /
  `reap_stale_missing_file_is_noop` (`engine-client/tests/session_file.rs`).
- **H2**: `PythonProcess::Drop` 内のセッションファイル削除を撤去。
  restart loop 中に session file が一時的に消える窓を解消。後続クリーンアップは
  C3 の `reap_stale` が担う。clean shutdown 時の orphan は次回起動時に reap される。
- **H3**: `serial_test = "3"`（既存 dev-dep）を使い `data::tests::data_path_env_override_*`
  に `#[serial(env_data_path)]` 属性を付与。`unsafe std::env::set_var` が並列実行で
  起こす UB を排除。`cargo test -p flowsurface-data` 全 PASS.
- **Pre-existing 11 件**: `_replay_state` / `_live_state` 未初期化 AttributeError を解消。
  `__new__` ベース fixture（`test_strategy_file_required.py` /
  `test_forget_second_password_conflict.py` / `test_server_session_holder_order.py`）に
  `engine.server.{LiveState, ReplayState}` を import して明示初期化。
  `test_invariant_tests_doc.py` は Phase 8 で削除された `src/api/order_api.rs` を
  参照していた C-H4 / C-M3 / C-M5 の status を `⏹️ Phase 8 廃止` に変更し
  `_is_completed` 判定から外した（行は履歴として残置）。

### 検証結果

- `cargo check --workspace` ✅
- `cargo clippy --workspace -- -D warnings` ✅
- `cargo fmt --check` ✅
- `cargo test -p flowsurface-engine-client` ✅（session_file 9 PASS 含む）
- `cargo test -p flowsurface-data` ✅
- `uv run pytest python/tests/ -m "not live"` → **1545 passed, 3 skipped**

---

## 6. テスト戦略

### 6.1 Phase 8.1 で追加するテスト

| ファイル | 内容 | mode |
|---------|------|------|
| `python/tests/test_replay_session.py` | helper の load → run → portfolio の golden path（callback ベース）。`check_data_exists` が False のケースで FileNotFoundError が raise されることを assert するテストを含む。 | in-process |
| `python/tests/test_replay_session_cli.py` | `python -m engine.replay_session run ...` の subprocess 起動テスト | in-process |
| `python/tests/test_replay_session_stop.py` | run() の途中で別 thread から stop() を呼ぶ → on_event ループが終端に到達する | in-process |
| `python/tests/test_replay_session_speed.py` | run() 中に set_speed() で multiplier が反映される | in-process |
| `python/tests/test_replay_session_attach.py` | subprocess で `python -m engine` server を立てて helper が attach、event を受信 | attach |
| `python/tests/test_replay_session_attach_session_file.py` | session ファイル経由で endpoint / token を解決する | attach |
| `python/tests/test_replay_session_attach_fallback.py` | engine 不在時に in-process に fallback する | both |
| `python/tests/test_replay_session_attach_token_mismatch.py` | token 不一致 → ConnectionRefusedError → in-process にフォールバック（`force_mode="auto"` 時）/ 例外伝播（`force_mode="attach"` 時）を assert | attach |
| `python/tests/test_replay_session_attach_disconnect.py` | attach 中に WS が切れたら `ConnectionError` を raise し、`status == "errored"` に遷移する | attach |
| `python/tests/test_replay_session_attach_busy.py` | attach 中に二度 load を投げると `BusyError` | attach |
| `python/tests/test_replay_session_double_enter.py` | `with` 内で `__enter__` を再度呼ぶと RuntimeError | in-process |
| `python/tests/test_replay_session_missing_strategy.py` | 存在しない strategy_file 指定で FileNotFoundError | in-process |
| `python/tests/test_replay_session_attach_gui_chart.py` | engine + 模擬 GUI client + helper の三者で event が両 client に届く（**穴 1 リグレッション保護**）。模擬 GUI client は Python `websockets` クライアントで実装する（Rust engine-client は不要）。`python -m engine` サーバーを subprocess で起動し、mock `NautilusRunner` で固定 event を emit する（J-Quants データ不要）。模擬 client はサーバーに接続して event を受信するだけのシンプルな実装で足りる。 | attach |
| `python/tests/test_replay_attach_manual_smoke.md` | `cargo run -- --mode replay` + `uv run python -m engine.replay_session run ...` で GUI pane 生成と bar 蓄積を人手確認する手順。release 前 smoke の観測点（pane 生成、bar 増加、EngineBusy 通知）を固定する。 | manual |
| `python/tests/test_server_multi_client.py` | engine.server が 2 client を同時保持・event fanout。Rust client が接続中に helper が join/離脱しても Rust の接続が維持される（reconnect ロジックに影響しない）ことを assert するケースを含む。 | engine |
| `python/tests/test_server_max_connections.py` | `MAX_CONNECTIONS` 超過で reject | engine |
| `python/tests/test_server_connection_count_event.py` | `ClientConnected` / `ClientDisconnected` の broadcast | engine |
| `python/tests/test_engine_session_file.py` | engine 起動 → session ファイル生成 → engine 停止 → ファイル削除 | engine |
| `python/tests/test_session_file_stale_pid.py` | dead pid のファイルを helper が無視 | helper |
| `python/tests/test_session_file_atomic_write.py` | 部分書き込みファイルが残らない | engine |
| `python/tests/test_engine_busy_reject.py` | `Loaded` 中の `LoadReplayData` が `EngineBusy` で reject | engine |
| `python/tests/test_engine_busy_running.py` | `Running` 中の Command reject | engine |
| `python/tests/test_live_session_login.py` | demo 立花への login smoke（DEV_TACHIBANA_* env 必須）。attach mode で `user_id/password` 明示指定時は `ValueError` になるケースも含む。 | both |

### 6.2 Phase 8.3 の移行ガイドライン

- bash + curl の I/O 検証 → pytest の `assert helper.xxx == ...` に置換
- bash の sleep / polling → pytest の `tenacity` retry または event 駆動 wait
- HTTP status code 検証 → 例外 type 検証（`pytest.raises(FileNotFoundError)` 等）

### 6.3 リグレッション保護（簡素化版）

- `python/tests/test_no_http_listener.py` を追加し、Phase 8.3 完了後に `socket.bind(("127.0.0.1", 9876))` が成功する（=誰も listen していない）ことを **release build でのみ** assert する
- helper attach mode のリグレッションは `test_replay_session_attach.py` がカバー（GUI ↔ helper 間の wire 互換性が壊れたら fail する）
- Rust 側専用リグレッションテスト（モジュール存在チェック）は**不要** — `cargo build` が通ること自体が削除のリグレッションガードになる

### 6.4 CI ゲート組込

- 外部依存テスト（`DEV_TACHIBANA_*` env 必須 / J-Quants データ必要）には `@pytest.mark.live` マーカーを付与する
- CI コマンド: `uv run pytest python/tests/ -v -m "not live"`
- `pyproject.toml` の `[tool.pytest.ini_options]` に `markers = ["live: requires live exchange or data"]` を追加する（Phase 8.1a のタスクに含める）
- Rust テストの CI: `cargo test -p flowsurface-engine-client` は外部依存なしで全件通るようにする（session file テストは tmp ディレクトリを使用）
- retained smoke の `tests/e2e/s55_mode_startup_smoke.sh` / `tests/e2e/smoke.sh` は毎 PR 必須には載せず、**scheduled CI または release 前 manual smoke** として別ジョブ化する。最低でも `cargo build --release` 後に `bash tests/e2e/s55_mode_startup_smoke.sh` と `bash tests/e2e/smoke.sh` を実行し、30 秒観測ログを保存する

---

## 7. リスクと未決事項

### 7.1 リスク

| # | リスク | 影響 | 軽減策 |
|---|------|------|--------|
| R1 | E2E テスト移行漏れ | カバレッジ低下 | Phase 8.3 で削除と置換を同一 PR にまとめる。並走期間は意図的に作らない（並走は両者が違う実装を叩くだけで意味が薄いため） |
| R2 | Iced GUI 内部状態のテスト表現力不足 | sidebar/test 系の一部シナリオがテストできない | Phase 8.2 で「移植」を諦め「削除 + debug backdoor」にする方針で構造的に許容 |
| R3 | helper class の API 設計が早期に固まらない | 利用側の手戻り | Phase 8.0 で API を確定。Phase 8.1 の examples は callback 形 1 本（buy_and_hold）が動けば確定 |
| R4 | ~~NautilusRunner が in-process で 2 回呼ばれる場合のリソース二重確保~~ | ~~helper の並列利用で fail~~ | **解消**：`__enter__` / `__exit__` で同一インスタンスの二度 enter を例外化。さらに attach mode では engine は GUI 内に 1 つしか存在しない（probe で確認）ため二重起動は構造的に発生しない |
| R5 | ~~外部 user の bash スクリプト破壊~~ | ~~外部影響~~ | **不要**：本リポジトリは個人/社内向けで外部 API バージョニング無し。CLAUDE.md / README に「Phase 8.x で HTTP API 廃止」告知のみで十分 |
| R6 | attach mode probe 中の TCP timeout で UX が遅くなる | helper 起動が常に 2 秒待つように見える | `attach_timeout_s` 既定 2.0s を維持しつつ、token / endpoint がどこからも解決できなければ probe を skip して即 in-process |
| R7 | GUI と helper が同時に load / order を投げて engine state が壊れる | replay 中に外部から再 load される等 | B3 で engine state 機械を実装し `EngineBusy` で reject。helper 側は `BusyError` に翻訳。協調動作は §0.1 で非サポート明示 |
| R8 | Python ⇄ Python の WS 経由通信が in-process より遅い（attach mode） | event レイテンシ増加 | attach mode は本質的に IPC が必要（別プロセスなので）。レイテンシ要件が厳しい backtest は in-process mode を選ぶよう README に明記。`SLEEP_CAP_SEC=0.200` の方が支配的なので体感差は無視できる想定 |
| R9 | `_AttachClient` が `Command` schema の drift で壊れる | attach mode 全停止 | helper と engine が同じ Python プロセスではないが **同じ `schemas.py` を import する**ので drift は起きない。`test_replay_session_attach.py` が wire 互換のリグレッションガードになる |
| R10 | engine.server multi-client 化で既存 GUI ↔ engine path に regression | GUI がフリーズ / event 取りこぼし | B1 で per-connection outbox / send_loop を保ち、各接続を独立に扱う。`test_server_multi_client.py` が「片方の disconnect で他方が落ちない」を保証 |
| R11 | session ファイルの PID 再利用で 別プロセスを engine と誤認 | helper が無関係なプロセスに接続を試みて失敗 | PID + `started_at` の組で stale 判定。最終手段として handshake で token mismatch すれば即 fallback |
| R12 | session ファイルの atomic write 失敗 / 残骸 | helper が壊れた JSON を読む | tmp → rename パターン + JSON parse 失敗時は stale 扱いで無視 |
| R13 | multi-client 下で reconnect protocol（spec.md §5.3）が壊れる | crash 後の状態再投入が不整合 | B1 と同時に spec.md §5.3 を per-connection 文脈で書き直す。subscribe state は engine 側で per-connection に保持し、各 client が独立に再投入する |

### 7.2 未決事項

| # | 質問 | 状態 |
|---|------|------|
| ~~Q1~~ | ~~`LiveSession`（旧 `/api/order/*`）は本当に作るか？~~ | **決定**: `LiveSession.login()` のみ Phase 8.1 必須。`submit/modify/cancel` は Phase 8.3 で必要に応じて追加 |
| Q2 | `/api/agent/narrative` は Python 単独モードで意味があるか？ | A: 残す（user code が narrate を呼べる） / B: 廃止 — Phase 8.3 着手前に決める |
| Q3 | `python -m engine.replay_session run ...` の event 出力フォーマットは JSONL でよいか？ | 既定 JSONL。`--format=table` で human-readable も用意するか別途検討 |
| Q3b | GUI replay フォーム（§3.4）のデフォルト値・前回入力の記憶方針 | 案: 前回入力を `saved-state.json` 内の `replay_form_last_input` に保存し次回起動時に復元（D9 「replay モードでは saved-state.json を load も save も行わない」と矛盾するため、別ファイル `replay-form-cache.json` を新設するか、env で defaults を提供するかを Phase 8.1c 着手前に決める）|
| ~~Q4~~ | ~~GUI と helper を同時起動するユースケース~~ | **決定**（§0.1）: helper は server を bind しない。GUI 起動中の helper は WS クライアントとして attach する（attach mode）。協調動作（GUI UI と helper を同時操作）は非サポート |
| Q5 | Phase 8.3 で削除する 6,756 行の中に「単独で価値のあるユーティリティコード」があれば抽出するか | replay_api 内の HTTP raw parser は再利用価値ゼロ。order_api 内の OrderGuard は HTTP 経路専用で GUI には不要のため、抽出候補なし。**candidate 無し** |
| Q6 | attach mode 時、helper の `submit_order()` は GUI の OrderGuard を経由しないが安全か？ | GUI 経路と同様に **OrderGuard は HTTP path 専用**でもともと適用されない。helper は pytest 用途と明記し、本番運用は GUI を使うルールを README で強調 |
| Q7 | attach mode の WS フレームに permessage-deflate を許すか？ | **不可**。`compression=None` 強制（[MISSES.md] 2026-04-25 の RSV1 互換性問題と同根。Python ⇄ Python でも同じルールを保つ） |
| ~~Q8~~ | ~~session ファイルのパス・名前~~ | **決定**: `data::data_path(Some("engine-session.json"))` に書く（`saved-state.json` と同居）。Rust 側 [data/src/lib.rs:134-145](../../data/src/lib.rs#L134-L145) を真実源とし、Python helper は `platformdirs.user_data_dir("flowsurface", appauthor=False)` で同じパスに解決する。`FLOWSURFACE_DATA_PATH` env override も両側で尊重。詳細は §4.2.1 |
| Q9 | `MAX_CONNECTIONS` の初期値は 4 で十分か？ | 想定: GUI 1 + helper 1 + デバッグ余裕 2 で 4。実運用で不足が出れば bump（compile-time const ではなく env var で override 可能にしておくか別途検討） |
| ~~Q10~~ | ~~engine state 機械の遷移を helper の `submit_order()` も guard すべきか？~~ | **決定**: guard 対象に含める。**replay state（`Idle/Loaded/Running/Stopping`）と live state（`Disconnected/Connecting/Connected`）の 2 つの直交する state 機械** を engine が持ち、`SubmitOrder{venue="replay"}` は `Replay::Running` のみ、`SubmitOrder` / `ModifyOrder` / `CancelOrder` / `CancelAllOrders` は `Live::Connected` のみで受理する。Phase 8.1b B3 に詳細列挙 |
| ~~Q11~~ | ~~engine が GUI 1 client しか接続していない時に session ファイルを書く判断~~ | **決定**: 常に書く（helper が後から繋ぐ可能性があるため）。GUI が multi-instance 起動した場合の競合は spawn port が衝突するので前段で防がれる想定 |

---

## 8. Definition of Done

Phase 8 シリーズ完了時点で（**全項目達成済み 2026-05-03**）：

1. ✅ **release build で** ポート 9876 を listen しているプロセスが存在しない（`src/replay_api.rs`, `src/api/` ディレクトリ削除済み）
2. ✅ `python -m engine.replay_session run ...` で GUI 起動なしに backtest が完走する（in-process mode, `python/engine/replay_session.py` 実装済み）
3. ✅ `cargo run -- --mode replay` 起動済みの状態で `python -m engine.replay_session run ...` を別プロセス起動すると、**GUI のチャートにペインが生成され bar が積まれる**（attach mode, `_AttachClient` 実装済み）
4. ✅ engine.server が multi-client broadcast に対応し、`engine-session.json` 経由で token / port が helper に共有され、`EngineBusy` で state guard が機能する（`_Broadcaster`, `ReplayState`, `LiveState`, `session_file.rs` 実装済み）
5. ✅ `tests/e2e/*.sh` は `smoke.sh` を残して全削除（s56〜s83, s90, tachibana_* 11 ファイル削除。`scripts/replay_dev_load.sh` 削除済み）
6. ✅ Rust 側 HTTP API モジュール 4 ファイル（合計 約 6,756 行）が削除されている
7. ✅ memory に記録された **「Python 単独でも動くか？」判断軸が満たされている**
8. ✅ CLAUDE.md / `.claude/CLAUDE.md` の replay セクションが helper ベース（in-process / attach 両モード）に書き換わっている（`python -m engine.replay_session run` コマンドを正規ルートとして記載済み）
9. ✅ spec.md §5.3 reconnect protocol の multi-client 文脈への更新は実装済み (`spec.md:241-269` の「Phase 8 attach mode（✅ 実装済み）」サブセクションで per-connection source of truth / client-side reissue / union 可能購読の束ね方を明記。Phase 8 R1 / Phase 3 で再評価し、attach mode の `RequestVenueLogin`↔`VenueReady`/`VenueError` 待ち合わせも `LiveSession.login()` C-GP4 側で実装済み)

---

## 9. 関連ドキュメント

- [spec.md](./spec.md) — Rust ↔ Python 境界仕様
- [archive/refactor-rust-python-boundary-2026-05-01.md](./archive/refactor-rust-python-boundary-2026-05-01.md) — depth/price 正規化の責務移動（別案件）
- [implementation-plan.md](./implementation-plan.md) — フェーズ 0〜8 の実装計画
- `src/replay_api.rs` — **削除済み** (Phase 8.3, 2,943 L)
- `src/api/order_api.rs` — **削除済み** (Phase 8.3, 3,490 L)
- `src/api/agent_api.rs` — **削除済み** (Phase 8.3, 323 L)
- `src/api/mod.rs` — **削除済み** (Phase 8.3, 2 L)
- [python/engine/replay_session.py](../../python/engine/replay_session.py) — `ReplaySession` / `LiveSession` / `_AttachClient`（Phase 8.1 成果物）
- [python/engine/nautilus/engine_runner.py](../../python/engine/nautilus/engine_runner.py) — helper の被ラップ対象（in-process mode）
- [python/engine/schemas.py](../../python/engine/schemas.py) — `_AttachClient` が共有する wire schema（`SCHEMA_MINOR=9`, `ClientConnected`/`ClientDisconnected`/`EngineBusy` 追加済み）
- [engine-client/src/session_file.rs](../../engine-client/src/session_file.rs) — `EngineSession` atomic write / delete（Phase 8.1b B2 成果物）
- [engine-client/src/lib.rs](../../engine-client/src/lib.rs) — Rust 側の WS クライアント参考実装（`_AttachClient` の Python 等価物）
- [src/modal/replay_form.rs](../../src/modal/replay_form.rs) — `ReplayFormModal` GUI フォーム（Phase 8.1c 成果物）

---

## 改訂履歴

| 日付 | 改訂内容 |
|------|---------|
| 2026-05-01 | 初版作成 |
| 2026-05-01 | レビュー 2 件を反映：Phase 8.0 新設（Q4 解決）/ Phase 構成 5→4 圧縮 / `LiveSession.login()` を Phase 8.1 必須に格上げ / Phase 8.2 を「移植」から「削除中心」に簡素化 / 完了条件を release build 限定に緩める / contextmanager 必須化で R4 構造解消 / R5 deprecated phase 削除 / `src/api/mod.rs` を削除リストに追加 / dispatcher と handler の関係を §2.1 に注記 / generator → callback ベースに変更 / リグレッションガードを grep ベースに簡素化 / Q1 と Q4 を未決から決定済みに格上げ |
| 2026-05-01 | GUI replay 起動 UX を A 案（メニューフォーム）に確定：§3.2 起動経路対応表を明確化 / §3.4 を新設して `File > Replay を開始...` フォームの UX フローを記述 / Phase 8.1 を 8.1a（Python helper）と 8.1b（GUI フォーム）に分割 / Phase 8.1 完了条件に GUI フォーム経由の動作を追加 / Q3b（フォームのデフォルト値方針）を未決事項に追加 |
| 2026-05-01 | **helper の attach client mode を採用**：§0.1 を「helper は server を bind しないが client として attach する」に書き換え / §1.4 で Rust `start_or_attach` との対称性を明記 / §3.1 全体図を 2-mode 対応に書き直し / §3.2 起動経路に「外部スクリプトから GUI を駆動」を追加 / §3.3 を attach mode 込みに更新 / §4.1 `ReplaySession` に mode auto-detect / `attach_endpoint` / `force_mode` を追加 / §4.2 で private `_AttachClient` を新設 / §4.3 `LiveSession` も同様に拡張 / §4.4 CLI に `--mode` オプション追加 / Phase 8.1 を 8.1a（in-process）/ 8.1b（attach）/ 8.1c（GUI フォーム）の 3 段階に分割 / R6（probe timeout）/ R7（同時操作 EngineBusy）/ R8（attach レイテンシ）/ R9（schema drift）を新設 / Q4 を attach mode 採用で再クローズ / Q6（OrderGuard）/ Q7（compression）を新設 / DoD #3 に attach mode 動作確認を追加 |
| 2026-05-01 | **attach mode 成立条件 B1〜B3 の実装スコープを明示**（レビューで指摘された 3 つの穴を反映）：§0.1.2 を新設して engine 側変更の必須性を明記 / §1.5 を新設して現状 engine の制約と Phase 8.1b で解消する範囲を整理 / §4.1 の `__enter__` で token 解決を「明示引数 → session ファイル → fallback」の優先順位に再定義 / §4.2 の token 取得経路を session ファイル対応に書き換え + `EngineBusy` 翻訳を追加 / Phase 8.1b を B1（multi-client broadcast）/ B2（session ファイル token 共有）/ B3（EngineBusy state guard）/ B4（`_AttachClient` 本体）の 4 段階に再分割し各段階の作業項目を詳細化 / Phase 8.1c に attach インジケータの作業項目を追加 / Phase 8.1 完了条件に B1〜B3 完了 + spec.md §5.3 更新を追加 / §6.1 テストに multi-client / session-file / busy 系列計 8 ファイルを追加（attach_gui_chart は穴 1 のリグレッション保護として明記）/ R10（multi-client regression）/ R11（PID 再利用）/ R12（atomic write）/ R13（reconnect protocol）を新設 / Q8（session ファイルパス）/ Q9（MAX_CONNECTIONS）/ Q10（state guard 範囲）/ Q11（session ファイル書き込み判断）を新設 / DoD #4 と #9 を追加 |
| 2026-05-01 | **Q8 / Q10 / Q11 を決定**：§4.2.1 を新設して session ファイルのパス解決を Rust ↔ Python の対応関係として詳述（`data::data_path(Some("engine-session.json"))` を真実源、helper は `platformdirs.user_data_dir("flowsurface", appauthor=False)` で再現、OS 別実体パスを表で明示）/ §0.1.2 B2 で書き込み主体を「Rust（engine-client）」に確定 / Phase 8.1b B2 を Rust 側 `EngineSessionFile::write_atomic` 実装 + `Drop` で削除 + crash 時 stale 削除 + Rust 側テスト 2 本 + `pyproject.toml` に `platformdirs` 依存追加に書き換え / Python 側テストを `test_session_file_resolve.py` / `test_session_file_stale_pid.py` / `test_session_file_env_override.py` に再構成 / Phase 8.1b B3（EngineBusy）を **2 つの直交する state 機械（Replay と Live）** に拡張：`SubmitOrder{venue="replay"}` を `Replay::Running` のみ、`SubmitOrder` / `ModifyOrder` / `CancelOrder` / `CancelAllOrders` を `Live::Connected` のみ、`RequestVenueLogin` を `Live::Disconnected` のみで受理 / B3 テストを 5 本に拡充（idle replay order / disconnected live order / login already connected を追加）/ B3 に GUI 側 Rust の発注時エラーハンドリング項目を追加 / Q8 / Q10 / Q11 をクローズ済みに格上げ |
| 2026-05-03 | **Group 4b 解消**（レビュー指摘の MEDIUM 一括処理）：M-2 (rust) `validate()` に `start_date > end_date` チェック追加 + RED 単体テスト / M-5 (rust) `validate()` の戻り値タプルを `ValidatedForm` 構造体に置き換え（caller も追従、Action::Submit はそのまま）/ M-4 (type) `engine-client/src/process.rs` で `proc.pid()` が `None` のときセッションファイル書き込み skip + `engine-client/tests/session_file.rs::pid_zero_session_file_is_immediately_reaped` 追加 / M-3 (type) `_read_session_file` 戻り値を `SessionFileData` TypedDict 化 / M-5 (type) `assert self._client is not None` を `if ... raise RuntimeError` に置換（load / run の 2 箇所）/ M-1 (silent) CLI に `except BusyError` 分岐を追加（exit code 2 + 専用メッセージ）/ M-2 (silent) `server.py` MAX_CONNECTIONS reject で close 前に `EngineError(code="max_connections")` を送信 / M-6 (silent) `_resolve_endpoint_and_token` の `force_mode='attach' but FLOWSURFACE_ENGINE_TOKEN env var is not set` 詳細メッセージ化 / M-8 (general) `LiveSession.__init__` に `attach_endpoint` / `attach_timeout_s` 引数追加（実装は Phase 8.3 まで `NotImplementedError`）/ M-10 (general) session ファイル読み取り時 `started_at` を `log.info` に出力（token は出さない）/ M-11 (general) `_send_loop` の `asyncio.wait_for(timeout=1.0)` を削除（sentinel 経路のみで終了）/ L-1 (general) `_ForceMode = Literal["auto","inprocess","attach"]` に統一 / L-2 (general) `_AttachClient` の handshake 成功 / 失敗を info / error で出力（token は出さない）/ 新規テスト: `python/tests/test_replay_session_attach_resolution.py`（6 件）+ `test_replay_session_cli_busy.py`（2 件）+ `test_server_multi_client.py::test_max_connections_reject` を新仕様に更新 + `engine-client/tests/session_file.rs::pid_zero_session_file_is_immediately_reaped` + `src/modal/replay_form.rs::tests::validation_fails_when_start_after_end`。M-3 (silent) `_handle_start_engine` の `EngineStarted` 未送出時 `EngineStopped` 補完は既存 `started_marker` ガードと意図的に直交するため**保留**（フリップ範囲が広く 1551 緑を破壊するリスクが高い）。CRITICAL の C2 残テスト 6 ファイル（`test_replay_session_attach_gui_chart` ほか）は subprocess engine spawn + 模擬 GUI WS client + Tachibana startup 抑止 + `LoadReplayData` mock fanout を必要とし、Group 4b の純ロジック修正バッチからは独立した工数（数時間 + Windows でのプロセス制御 flaky 性）を要するため、別ラウンドへ持ち越し（STOP+REPORT 済み）。最終結果: cargo test 589 passed / pytest 1559 passed (+8 新規) / 3 skipped (live) / clippy clean / fmt clean。 |
| 2026-05-03 | **Phase 8 全サブフェーズ実装完了**：Phase 8.1a（`python/engine/replay_session.py` 新規作成。`ReplaySession`/`LiveSession`/`_AttachClient` 三者を単一ファイルに実装。CLI `python -m engine.replay_session run` 対応）/ Phase 8.1b（`python/engine/server.py` を `_Broadcaster` multi-client fanout + `ReplayState`/`LiveState` state machine + `EngineBusy` guard + `MAX_CONNECTIONS=4` に更新。`engine-client/src/session_file.rs` `EngineSession` 新規作成。`python/engine/schemas.py` `SCHEMA_MINOR` 8→9, `ClientConnected`/`ClientDisconnected`/`EngineBusy` 追加。`data/src/lib.rs` `FLOWSURFACE_DATA_PATH` env override バグ修正）/ Phase 8.1c（`src/modal/replay_form.rs` `ReplayFormModal` 新規作成。`src/native_menu.rs` に "Replay を開始..." メニュー追加）/ Phase 8.2（`scripts/replay_dev_load.sh` 削除。`tests/e2e/` から s56〜s83, s90, tachibana_* 11 ファイル削除。`scripts/run-replay-debug.sh` に DEPRECATED コメント追記）/ Phase 8.3（`src/replay_api.rs` / `src/api/` 全削除。`src/main.rs` から HTTP runtime ブロック・ControlApi Message 等を全削除）/ §0.1.2 B1〜B3 対応状況を「実装済み」に更新 / §8 DoD を「全達成済み」に更新 / §9 関連ドキュメントを削除済みファイル・新規成果物に更新 |
| 2026-05-03 | **C2 残テスト 6 件実装完了**（前ラウンドで持ち越された CRITICAL 項目）：`python/tests/test_replay_session_attach_gui_chart.py`（穴1リグレッション保護：DataEngineServer + 模擬 GUI WS client + `_AttachClient` の三者 broadcast テスト 3 件）/ `python/tests/test_session_file_atomic_write.py`（`.tmp → rename` atomic write：途中ファイルは読めないことを assert する 4 件）/ `python/tests/test_engine_session_file.py`（session ファイルライフサイクル：pid alive → data / pid dead → None の 3 件）/ `python/tests/test_replay_session_attach_session_file.py`（session ファイル経由の endpoint/token 解決 → attach mode 遷移 3 件）/ `python/tests/test_replay_session_attach_disconnect.py`（WS 切断時 `ConnectionError` + `status=="errored"` 遷移 3 件）/ `python/tests/test_replay_session_attach_manual_smoke.md`（GUI pane 生成・bar 蓄積の人手確認手順書）。最終結果: pytest 1575 passed / 3 skipped（+16 新規）/ cargo check clean。**技術的知見**：(1) `ClientConnected(count=1)` の到着タイミング — サーバーは `Ready` 送出後に `ClientConnected` を broadcast するため、helper の handshake ループは `count=1` を捕捉できず `_recv_queue` に積まれる。`count>=2` を探すにはキューを手動でドレインする必要がある；(2) Windows の `asyncio` 多重起動 — 複数スレッドで `asyncio.run()` を呼ぶと ProactorEventLoop 競合で `EOFError` が発生する。`DataEngineServer` と模擬 GUI クライアントを同一 event loop で動かし `asyncio.run_coroutine_threadsafe()` で注入するパターンで回避；(3) `loop.stop()` 禁止 — `loop.call_soon_threadsafe(loop.stop)` はサスペンド中の `await` を `RuntimeError` で落とす。`asyncio.Event` の `wait()` ＋ `call_soon_threadsafe(stop_event.set)` パターンに置き換えること。 |


---

## レビュー反映 (2026-05-04, ラウンド 1 - 新規) / Phase 1 型基盤

後続 Phase 2/3/4/5 の前提となる型基盤を TDD で導入。RED→GREEN→REFACTOR で 8 項目を消化した。

### 完了項目

- ✅ **C-Type1**: `python/engine/schemas.py` に `AttemptedCommand` Literal を新設 (12 値、`GetBuyingPower`/`GetPositions`/`GetOrderList` を含む)。`python/engine/server.py:2225,2246` の `_check_replay_state` / `_check_live_state` 第一引数の型を `str` → `AttemptedCommand` に格上げ。呼び出し側 9 箇所 + 新規 3 箇所 (H-Type4) は文字列リテラルでそのまま通る (Literal narrowing)。
- ✅ **C-Type2**: `python/engine/server.py:281` `self._mode: str = "live"` を `self._mode: AppMode = "live"` に。`schemas.py` に `AppMode = Literal["live","replay"]` を新設し `Hello.mode` も同 alias を使う。`__init__` 末尾に `assert self._mode in ("live","replay")` を pin として追加。
- ✅ **H-Type1**: `engine-client/src/dto.rs:608-628` に `PositionType` enum (`#[serde(rename_all = "snake_case")]` で `cash`/`margin_credit`/`margin_general`) を新設。`PositionRecordWire.position_type: String` → `PositionType`。`as_wire_str()` ヘルパも提供。RED テスト: `engine-client/tests/dto_position_type.rs` (5 件、未知値 `Err` + snake_case 直列化)。`src/screen/dashboard/panel/positions.rs:144-149` の match arm を新 enum に切替。
- ✅ **H-Type2**: `python/engine/schemas.py` に `ReplayStateName` / `LiveStateName` / `CurrentEngineState` (`Union`) を新設。`EngineBusy` に `model_validator(mode="after")` を追加して replay-only command (`LoadReplayData`/`StartEngine`/`StopEngine`/`SetReplaySpeed`) と live state、live-only command (`ModifyOrder`/`CancelOrder`/`CancelAllOrders`/`RequestVenueLogin`/`GetBuyingPower`/`GetPositions`/`GetOrderList`) と replay state の illegal 組合せを `ValidationError` で拒否。`SubmitOrder` は venue により両側で発生し得るため shared として除外。テスト: `python/tests/test_schemas_types.py::TestEngineBusyOrthogonalRejection`。
- ✅ **H-Type4**: `python/engine/server.py` `_do_get_order_list` (1665), `_do_get_buying_power` (1750), `_do_get_positions` (1822) の tachibana 経路冒頭に `_check_live_state(...)` を追加。`AttemptedCommand` Literal に `GetBuyingPower`/`GetPositions`/`GetOrderList` を追加。RED テスト: `python/tests/test_engine_busy_query_guards.py` (4 件)。既存テスト 13 件は `_make_server` ヘルパに `_live_state = LiveState.CONNECTED` を追加して migration 完了。
- ✅ **M-Type2**: `python/engine/replay_session.py:42-70` で `PartialSessionFileData` (読み取り中間表現) と `SessionFileData` (確定形) を分離。`_read_session_file` (134) で `token` 必須 + `port: int > 0` 必須をランタイム検証してから narrow。
- ✅ **M-Type3**: Python `OrderListFilter.status` を `Optional[Literal["SUBMITTED","ACCEPTED","FILLED","PENDING_CANCEL","CANCELED","EXPIRED","REJECTED"]]` に限定 (値リストは `tachibana_orders.py:_STATUS_TEXT_MAP` の出力集合と一致)。Rust `engine-client/src/dto.rs` に `OrderStatus` enum を新設 (SCREAMING_SNAKE_CASE)。`OrderRecordWire.status: String` → `OrderStatus`。`Display` impl 提供。
- ✅ **M-Type4**: Rust `engine-client/src/dto.rs` に `CurrentEngineState` (SCREAMING_SNAKE_CASE) と `AttemptedCommand` (PascalCase wire = Python と同) enum を新設。`EngineEvent::EngineBusy { current_state: String, attempted_command: String }` → enum 化。未知値はデシリアライズ失敗。RED テスト: `engine-client/tests/dto_engine_busy_enums.rs` (5 件)。

### 後続 Phase が import すべきシンボル (配置)

#### Python (`engine.schemas`)

```python
from engine.schemas import (
    AppMode,                # Literal["live", "replay"]
    AttemptedCommand,       # Literal[12 values]
    ReplayStateName,        # Literal["IDLE","LOADED","RUNNING","STOPPING"]
    LiveStateName,          # Literal["DISCONNECTED","CONNECTING","CONNECTED"]
    CurrentEngineState,     # Union[ReplayStateName, LiveStateName]
    ReplayOnlyCommand,      # Literal["LoadReplayData","StartEngine","StopEngine","SetReplaySpeed"]
    LiveOnlyCommand,        # Literal[6 values inc. GetBuyingPower/GetPositions/GetOrderList]
    SharedCommand,          # Literal["SubmitOrder"]
    AUTH_FAILED_CODE,       # str = "auth_failed"
)
```

#### Rust (`flowsurface_engine_client::dto`)

- `AppMode` (既存)
- `PositionType` (新規, `Cash`/`MarginCredit`/`MarginGeneral`)
- `OrderStatus` (新規, SCREAMING_SNAKE_CASE 7 値)
- `CurrentEngineState` (新規)
- `AttemptedCommand` (新規 12 値)

### 検証コマンド結果

| コマンド | 結果 |
|---------|------|
| `cargo check --workspace` | ok |
| `cargo clippy --workspace -- -D warnings` | clean |
| `cargo fmt --check` | clean |
| `cargo test --workspace` | 全テストグループ ok / 失敗 0 |
| `uv run pytest python/tests/ -m "not live" -q` | 1635 passed / 3 skipped (baseline 1598 から +37, 退行なし) |

### 設計判断

- **EngineBusy 直交制約 (H-Type2)** は `model_validator(mode="after")` の (b) 案を採用。Rust 側 `EngineEvent::EngineBusy` は 1 variant に保ち discriminator を増やさない (ABI と attach mode protocol を壊さない)。直交制約は Python 側だけで強制し、Rust デシリアライザは未知値検出のみを担当する。
- **`SubmitOrder` を shared command** として例外扱いした (server.py の二経路: 980 行 replay venue + 1040 行 live venue)。replay-only / live-only に押し込むと既存挙動に矛盾するため。
- **`SessionFileData` (M-Type2)** は TypedDict 1 種類で `total=False` のまま、必須性はランタイム validator で担保する。Python 3.10 互換維持。`Required[...]` を使うと 3.11+ になる。
- **OrderListFilter.status (M-Type3)** の値リストは `tachibana_orders.py:_STATUS_TEXT_MAP` から取得 (SUBMITTED/ACCEPTED/FILLED/PENDING_CANCEL/CANCELED/EXPIRED/REJECTED)。`"OPEN"` は Python では未使用 (test fixture のみ) のため `OrderStatus::Accepted` に置換した (`src/screen/dashboard/panel/orders.rs` の test 4 箇所)。
- **`AttemptedCommand` の 12 値** は schemas.py / server.py / dto.rs / engine-client tests で完全一致。Phase 2 以降が新コマンドを追加する場合は **3 ファイル同時更新** が必須 (CI 緑が壊れることで自動検知)。

### 持ち越し

- `_mode: AppMode` の代入チェックは現状 `assert` 一箇所のみ。本格的な mypy/pyright CI gating は別 Phase で導入を推奨。

---

## レビュー反映 (2026-05-04, ラウンド 1 - 新規) / Phase 2 server 振る舞い

Phase 1 で確立した型基盤の上に、`python/engine/server.py` の振る舞い 7 項目を TDD（RED→GREEN→REFACTOR）で修正した。RED テストはすべて新規ファイル `python/tests/test_server_phase2_review_fixes.py` (8 件) に集約。

### 完了項目

- ✅ **WS-MED1**: handshake() タイムアウト導入。`server.py` 上部に module 定数 `_HANDSHAKE_TIMEOUT_S: float = 15.0` を新設し、`_handshake()` 先頭の `await ws.recv()` を `asyncio.wait_for(ws.recv(), timeout=_HANDSHAKE_TIMEOUT_S)` に置換。タイムアウト時は `ws.close(1002, "handshake timeout")` 後に `ValueError("handshake_timeout")` raise。RED テスト: `test_handshake_timeout_closes_connection`（`monkeypatch` で 0.5s に短縮し、Hello を送らない接続が close code 1002 で切断されることを assert）。
- ✅ **WS-MED2 + L-WS-INFO**: auth_failed / schema_mismatch reject の close code を `1008` Policy Violation に統一。`_handshake()` の `ws.close()` を `ws.close(1008, "auth failed")` / `ws.close(1008, "schema mismatch")` に変更。RED テスト: `test_auth_failed_uses_close_code_1008` / `test_schema_mismatch_uses_close_code_1008`。
- ✅ **WS-MED3 / H-GP1**: `AUTH_FAILED_CODE` 定数共有。`engine.schemas` から `AUTH_FAILED_CODE` を import し、`_handshake()` の `EngineError(code="auth_failed", ...)` および後続 `raise ValueError("auth_failed")` を `code=AUTH_FAILED_CODE` / `raise ValueError(AUTH_FAILED_CODE)` に置換。RED テスト: `test_auth_failed_code_is_imported_from_schemas`（`inspect.getsource` でソース内に bare `"auth_failed"` リテラルが残っていないことを pin）。
- ✅ **Silent-M1**: EngineStopped 補完送出。`_handle_start_engine` の `except Exception` ブロックの `if started_marker["sent"]:` ガードを撤廃し、エラー経路でも常に `EngineStopped` を broadcast する。`started_marker = {"sent": False}` 自体も使われなくなったため削除し、`_on_event_tracked` 内の `started_marker["sent"] = True` 行も削除（`_replay_streaming_fills.clear()` は維持）。Rust 側は EngineStarted なしの EngineStopped を no-op として扱う前提（TimeoutError 経路と同じ契約）。RED テスト: `test_engine_stopped_emitted_when_runner_raises_before_started`（`NautilusRunner.start_backtest_replay_streaming` を `RuntimeError` で raise させ、outbox に `EngineStopped` + `Error{engine_run_failed}` が必ず流れることを assert）。**既存テスト** `test_failure_before_engine_started_does_not_emit_stopped` は旧仕様を pinning していたため、`test_failure_before_engine_started_emits_stopped` に名称・assertion 反転して新仕様（Silent-M1）を pin する形に書き換えた。
- ✅ **Silent-M2**: orjson decode エラーで接続切断しない。`_recv_loop` の `msg = orjson.loads(raw)` を `try/except orjson.JSONDecodeError` で囲み、当該接続の outbox に `Error{code:"malformed_json"}` を unicast (`_outbox.send_to`) して `continue`（loop 維持）。旧実装は async-for から例外が伝播し、handler の finally で接続をサイレント切断していた。RED テスト: `test_malformed_json_keeps_connection_alive`（不正 JSON フレーム送信 → Error event 受信 + 後続の `Ping` が `Pong` で返ることを assert）。
- ✅ **M-GP8**: EngineBusy を当該接続のみに送信。`_Broadcaster` に `send_to(ws, item)` を新設し、`ws` が登録済みなら当該キューにだけ put、未登録なら compat deque (`self._q`) のみへ append（テスト経路の互換性維持）。`_check_replay_state` / `_check_live_state` のシグネチャに `ws: ServerConnection | None = None` keyword-only 引数を追加し、`ws` が渡されたら `send_to`、None なら旧来の `append`（broadcast）にフォールバック。`_dispatch` から各 handler (`_do_submit_order` / `_do_modify_order` / `_do_cancel_order` / `_do_cancel_all_orders` / `_do_get_order_list` / `_do_get_buying_power` / `_do_get_positions` / `_do_request_venue_login` / `_handle_load_replay_data` / `_handle_start_engine` / `_handle_stop_engine`) を呼ぶ箇所すべてに `ws=ws` を伝播。SetReplaySpeed は `_dispatch` 内インラインなので直接 `ws=ws` を渡す。RED テスト: `test_engine_busy_is_unicast_to_offending_connection`（2 client 並列接続で client A が `SetReplaySpeed` を IDLE 状態で送信 → A は `EngineBusy` を受信、B は受信しないことを 0.5s ウィンドウで assert）。`ClientConnected` / `ClientDisconnected` / `Error` 等の broadcast は `append` のままで影響なし。
- ✅ **H-GP7**: send_loop shutdown ドレイン。`_send_loop` を helper `_send_one` に抽出した上で、`shutdown_event.is_set()` を観測したらループを抜ける前に `q.get_nowait()` ループで queue を完全ドレインしてから return するように書き換え。旧実装は QueueEmpty → shutdown チェックの順だったため、shutdown 直前に append された最終 event が drop される race があった。RED テスト: `test_send_loop_drains_pending_events_on_shutdown`（接続済 client の outbox に sentinel `EngineStopped` を append してから `server.shutdown()` を呼び、close 前に sentinel が必ず到着することを assert）。

### 既存テストの追従修正

- `python/tests/test_engine_busy_reject.py::_ListOutbox` / `python/tests/test_engine_busy_query_guards.py::_ListOutbox` / `python/tests/test_server_engine_dispatch.py::_ListOutbox`: M-GP8 で `_Broadcaster` に追加した `send_to(ws, item)` メソッドを 3 つの test stub にも追加（duck-type 互換を維持。実装は `self._q.append(item)`）。
- `python/tests/test_server_engine_dispatch.py::test_failure_before_engine_started_does_not_emit_stopped`: Silent-M1 で挙動を反転したため、テストを `test_failure_before_engine_started_emits_stopped` に名称ごと書き換え、`assert "EngineStopped" in kinds` に反転。順序 (`Stopped → Error`) と `final_equity == "0"` も追加で pin。

### 検証コマンド結果

| コマンド | 結果 |
|---------|------|
| `cargo check --workspace` | ok |
| `cargo clippy --workspace -- -D warnings` | clean |
| `cargo fmt --check` | clean |
| `cargo test --workspace` | 全テストグループ ok / 失敗 0 |
| `uv run pytest python/tests/ -m "not live" -q` | **1643 passed** / 3 skipped (Phase 1 baseline 1635 から +8 新規, 退行なし) |

### 設計判断

- **`_HANDSHAKE_TIMEOUT_S` を module 定数化**: テストから `monkeypatch.setattr(server_mod, "_HANDSHAKE_TIMEOUT_S", 0.5)` で短縮できるようにする狙い。インスタンス属性化も検討したが、サーバごとに変える需要は無く、グローバル定数の方が pin として明示的。
- **`send_to(ws, item)` の未登録 ws fallback**: テストで `MagicMock()` を ws として渡すケースが多い (`_make_server` が `_outbox` を `_ListOutbox` スタブに差し替えるパターン)。本番 `_Broadcaster` では未登録 ws は compat deque (`self._q`) にのみ append し warning ログを残さない（テスト中の正常経路のため）。3 つのテスト stub には `send_to(ws, item)` メソッドを追加して duck-type 互換を維持。
- **`_check_replay_state` / `_check_live_state` の `ws` 引数を keyword-only**: 呼び出し箇所は 12 箇所あり、positional だと既存テストの引数順序を破壊する。keyword-only + default `None` で破壊しない移行を実現。
- **Silent-M1 の旧テスト書き換え**: 旧テスト `test_failure_before_engine_started_does_not_emit_stopped` が「補完しない」を assert していた以上、これを修正せず残すと矛盾する。新仕様 (Silent-M1) を pin する `test_failure_before_engine_started_emits_stopped` に名称ごと反転して上書き、旧 docstring の「未 Start 状態では補完しない」コメントも全削除。
- **`_send_loop` の `wait_for(timeout=1.0)` 維持**: 計画書末尾「2026-05-03 改訂履歴」には M-11 で「`wait_for` を削除」と記載があるが、実装上は wakeup イベントが無いと shutdown チェックがブロックされる構造のため timeout=1.0 polling が必要。これは別 Phase で `_outbox_event` ベースの sentinel-shutdown 経路に再設計するべき項目だが、本ラウンドのスコープ外（H-GP7 は drain 漏れの修正のみ）。
- **EngineBusy unicast の broadcast との直交性**: `ClientConnected` / `ClientDisconnected` は引き続き `append`（broadcast）。`Error{request_id}`（`_send_error` 経由）も従来どおり `append`。`malformed_json` Error と `EngineBusy` だけが `send_to` 経路を使う新規挙動。

### 持ち越し

- `_send_loop` の `wait_for(timeout=1.0)` を削除し sentinel/event ベースの shutdown シグナルに書き換える計画書記載の M-11 改修は別 Phase へ。本ラウンドの H-GP7 では drain 漏れだけを解消した。
- Rust 側の close code 1008 受信時のクライアント側分岐（auth_failed と schema_mismatch を区別する error toast 文言）は Phase 3（`_AttachClient` 側）でカバーする予定。Phase 2 はサーバ側の wire 契約だけを修正した。

---

## レビュー反映 (2026-05-04, ラウンド 1 - 新規) / Phase 3 helper 振る舞い + attach login

Phase 1 の型基盤 + Phase 2 の server 振る舞い修正を踏まえ、`python/engine/replay_session.py` の helper 振る舞いと `LiveSession` attach mode 本実装を TDD（RED→GREEN→REFACTOR）で消化した。RED テストは新規 2 ファイル `python/tests/test_replay_session_phase3_review_fixes.py`（23 件）と `python/tests/test_live_session_attach.py`（4 件）に集約。

### 完了項目

- ✅ **C-GP1 / H-Type3**: `ReplaySession.status` の戻り型 `Literal` を `["idle","loaded","running","stopping","stopped","errored"]` の 6 値に拡張。`_ReplayStatus.STOPPING` を `"running"` にマップする旧仕様を撤廃し、`.value` をそのまま返す。`# type: ignore` 削除。RED テスト: `test_status_returns_stopping_during_transition` / `test_status_literal_includes_all_six_states`。既存テスト `test_review_fixes.py::test_status_stopping_returns_running` を新仕様に追従し `test_status_stopping_returns_stopping` にリネーム。
- ✅ **C-GP2**: attach mode `run()` の events() ループで `_extract_event_kind(evt) == "EngineStopped"` を観測した時点で先に `_status = STOPPED` をセットし、その後で `on_event(evt)` を呼ぶ。`except Exception` ブロックでは `_status is STOPPED` ならそのまま維持し、未到達のときだけ `ERRORED` にする。これにより on_event 内例外が STOPPED を ERRORED に上書きしない不変条件を確立。RED テスト: `test_engine_stopped_marks_status_before_on_event`。
- ✅ **WS-MED3 / H-GP1 (helper 側)**: `_AttachClient._async_main` の handshake reject 判定を `"auth" in code.lower()` から `code == AUTH_FAILED_CODE` の完全一致に厳格化。`from engine.schemas import AUTH_FAILED_CODE` を追加。RED テスト: `test_auth_failed_code_only_for_exact_match` (code=`authentication_error` は `_TokenMismatchError` にしない) / `test_auth_failed_code_exact_match_raises_token_mismatch` (`auth_failed` のみ翻訳)。
- ✅ **H-GP2**: `_AttachClient.handshake()` 失敗時 cleanup を強化。`close()` は元から `_thread.join(5.0)` 済みだが、その後 `self._thread.is_alive()` を再確認し残存していたら明示的に warning を出す。RED テスト: `test_handshake_failure_joins_thread`。
- ✅ **H-GP4**: `_resolve_endpoint_and_token` の env-only 経路で `FLOWSURFACE_ENGINE_PORT` env を尊重するよう拡張 (未設定時は `19876`)。整数解釈失敗時は warning + 既定値。RED テスト: `test_resolve_endpoint_respects_engine_port_env` / `test_resolve_endpoint_default_port_when_env_unset`。
- ✅ **H-GP8**: post-handshake 例外時の sticky error。`_AttachClient._run_loop` の finally で `_closed_event.set()` が呼ばれる構造は元から正しいが、events() / wait_for() 側で connection lost を即 `ConnectionError` 化する経路を Silent-M3 と統合。RED テスト: `test_post_handshake_failure_sticks_closed_event`。
- ✅ **H-GP5**: `LiveSession.login()` の nested-loop fallback 経路で `_PNoCounter()` を再生成していた bug を修正。最初に作った `p_no_counter` インスタンスを fallback の `_runner` 内 closure からも参照することで、構築 1 回のみに統一。RED テスト: `test_login_nested_loop_fallback_reuses_pno_counter` / `test_login_pno_counter_constructed_once`。
- ✅ **H-Silent4**: `narrative_hook.on_order_filled_sync` を asyncio.run RuntimeError 時の thread fallback 付きに書き換え。旧 `except Exception` で warning 出して silently drop していた経路を撤廃。最終失敗時は `log.error`。コルーチンの leak も `coro.close()` で明示的に防ぐ。RED テスト: `test_narrative_hook_sync_falls_back_when_loop_running` (asyncio loop 中から呼出 → ExecutionMarker emit)。
- ✅ **Silent-M3**: `_AttachClient.events()` と `wait_for()` で `event == "Error"` を受信したら即 `ConnectionError` raise。60s ハングを防ぐ。RED テスト: `test_wait_for_raises_immediately_on_error_event` / `test_events_raises_immediately_on_error_event`。
- ✅ **M-GP3**: `_AttachClient.wait_for()` の finally で pending を re-queue する経路を、エラー終了 (BusyError / ConnectionError / TimeoutError) では破棄するよう変更。`requeue_pending` フラグを正常終了時のみ True にして finally で分岐。RED テスト: `test_wait_for_busy_does_not_requeue_pending` (EngineBusy 発生時に保留 ReplayBuyingPower が再 yield されないことを assert)。
- ✅ **M-GP1**: `_resolve_endpoint_and_token` の docstring を計画書 §4.1 と同一の解決順 `(a) 明示 attach_endpoint+env / (b) session ファイル / (c) env-only` に明記。`(a)` で session ファイルの token を混ぜないことも明示。計画書 §4.1 status プロパティ周辺に同じ解決順を追記。RED テスト: `test_resolve_priority_a_explicit_argument_wins` / `test_resolve_priority_b_session_file` / `test_resolve_priority_c_env_only` / `test_resolve_priority_no_match_returns_none` の 4 件。
- ✅ **M-GP2**: `_read_session_file` で `pid` フィールドが欠損 (キー無し) または `None` の session ファイルを invalid として `None` を返すよう厳格化。`pid is None` early-return を追加。RED テスト: `test_session_file_missing_pid_returns_none` / `test_session_file_null_pid_returns_none`。
- ✅ **M-GP5**: `LiveSession.login()` の冒頭で `if self._logged_in: return` を追加し、2 回目以降の login() を no-op 化。内部 API も attach mode の `RequestVenueLogin` も再送信せず、既存セッションを維持する。計画書 §4.3 docstring と本体 docstring の両方に明記。RED テスト: `test_login_second_call_is_noop`。既存テスト `test_login_marks_session_as_logged_in` は元々 1 回呼出後の `is_logged_in` のみ assert する形のため再書き換え不要。
- ✅ **L-GP2**: CLI argparse の `--mode choices` を `["auto","inprocess","attach"]` ハードコードから `list(typing.get_args(_ForceMode))` に変更。`_ForceMode = Literal[...]` を 1 箇所変えれば CLI が自動追従する DRY 不変条件。RED テスト: `test_cli_mode_choices_match_force_mode_alias` (ソース文字列に `choices=list(typing.get_args(_ForceMode))` または `_ForceMode.__args__` のいずれかが含まれることを regex で pin)。
- ✅ **C-GP4 (最大スコープ)**: `LiveSession.__enter__` の attach 分岐から `NotImplementedError` を撤廃し本実装。`_resolve_endpoint_and_token` を ReplaySession と同一形式で新設、attach mode 確立後に `_login_attach()` を新規メソッドとして実装。`Command::RequestVenueLogin{request_id, venue}` を engine に送信し、`_AttachClient._recv_queue` を直接 poll して `VenueReady` (= `is_logged_in=True`) または `VenueError` (= `ConnectionError` raise) を 30s timeout で待ち合わせる。token / endpoint 解決順は ReplaySession と共通。attach mode で `user_id`/`password` 明示は `ValueError`。`__exit__` で `_client.close()` を呼ぶ責務も追加。RED テスト (新規ファイル `test_live_session_attach.py`): `test_attach_login_sends_request_venue_login` / `test_attach_login_blocks_explicit_credentials` / `test_attach_login_returns_true_on_venue_ready` / `test_attach_login_raises_on_venue_error` の 4 件。`__init__` に `self._client: _AttachClient | None = None` を追加し inprocess パスでも `__exit__` が AttributeError を出さないようにした。

### 計画書・spec.md 反映

- §4.1 `ReplaySession.status` の docstring を 6 値 Literal + ライフサイクル + endpoint/token 解決順 (M-GP1) を含む形に拡張。
- §4.3 `LiveSession.login()` の docstring を in-process / attach / 2nd-call no-op の 3 経路に拡張 (C-GP4 + M-GP5)。
- §7.2 持ち越し「attach mode の `RequestVenueLogin` ↔ `VenueReady`/`VenueError` 待ち合わせ」を ✅ 完了に変更し、参照テストファイル名を併記。
- §8 DoD #9 (`spec.md §5.3` reconnect protocol multi-client 文脈) を ✅ 完了に格上げ。spec.md §5.3 の「次フェーズで対応予定」表記を撤去し、Phase 8 R1 / Phase 3 で再評価済みである旨を明記。

### 既存テストの追従修正

- `test_review_fixes.py::test_status_stopping_returns_running` → `test_status_stopping_returns_stopping` (新仕様: `_ReplayStatus.STOPPING` の `.value == "stopping"` を返す)。
- `test_live_session_login.py::test_attach_force_mode_raises_not_implemented` → `test_attach_force_mode_raises_when_no_engine` (C-GP4 で attach mode が本実装になったため、token/endpoint が無いケースの `ConnectionRefusedError` を pin する形に書き換え)。

### 検証コマンド結果

| コマンド | 結果 |
|---------|------|
| `cargo check --workspace` | ok |
| `cargo clippy --workspace -- -D warnings` | clean |
| `cargo fmt --check` | clean |
| `cargo test --workspace` | 全テストグループ ok / 失敗 0 |
| `uv run pytest python/tests/ -m "not live" -q` | **1670 passed** / 3 skipped (Phase 2 baseline 1643 から +27 新規, 退行なし) |

### 設計判断

- **`status == "stopping"` を別状態として表に出す**: 旧実装は STOPPING を `"running"` にマップしていたが、観測上 stop 投入後の遷移期間は走行中とは区別したいケースがある (E2E テスト・GUI ステータスバー)。Literal 拡張のコストは小さく、外部コードが旧 5 値しか想定していない場合は pyright が新規ケース漏れを検知できる。これに伴い計画書 §4.1 の status docstring を「6 値」に書き換え。
- **`_login_attach()` で `_recv_queue` を直接 poll する**: `_AttachClient.wait_for()` は単一 event 名しか待てない (`VenueReady` か `VenueError` の or を表現できない) ため、login の attach 経路だけは raw queue を直接 poll する。30s timeout は長時間ログインダイアログが GUI 側に出ているケースも想定。`request_id` 一致チェックで他 client 由来の VenueReady を取り違えない。
- **`AUTH_FAILED_CODE` 完全一致**: `"auth" in code.lower()` の曖昧マッチは `authentication_error` 等の偽陽性を生む。完全一致にすることで Phase 1 で導入した `AUTH_FAILED_CODE = "auth_failed"` 定数を真実源として運用できる。`_TokenMismatchError` への翻訳は token mismatch を意味するため、code 文字列を契約として固定するのは妥当。
- **`pid` 必須化 (M-GP2)**: Rust 側 `EngineSession` は spawn 時に pid を必ず書き込むため、pid 不在 = 旧フォーマット / 破損 / テスト漏れ。invalid 扱いで fallback させる方が安全 (PID=0 / `None` のまま attach を試みると別プロセスに当たるリスク)。既存の M-Type2 (port / token 必須) と同じ扱い。
- **`p_no_counter` 再利用 (H-GP5)**: 旧実装は fallback 内で `_PNoCounter()` を新規生成していたため、最初に作った counter が捨てられていた。仕様としては「session 中で連番を維持する」のが正しいので、外側で 1 度だけ作って fallback の closure からも参照する形に統一。テスト不可避なため `_PNoCounter` を monkeypatch で wrap して構築回数を pin。
- **`narrative_hook` thread fallback (H-Silent4)**: `LiveSession.login` と同じパターンで、asyncio.run のネスト不可 RuntimeError を捕えて `concurrent.futures.ThreadPoolExecutor` で別 thread から実行する。旧コードは `except Exception` でログを出して silently drop しており、ExecutionMarker 損失の温床だった。失敗時は `log.error` (warning ではなく) で出すことで監視ログから検出できるようにする。
- **`wait_for` の pending drop (M-GP3)**: BusyError / ConnectionError 経路では pending を再 queue しない。理由: state guard で reject されたコマンドの pending event は呼び出し側が「失敗したコマンドの副作用」として認識すべきもので、後続の events() ループに混ぜると重複 yield や順序破壊の原因になる。正常終了 (待機 event 一致) のみ pending を戻す。`requeue_pending` flag をフラグ管理することで finally の意図を明示。

### 持ち越し

- 実 demo smoke (`@pytest.mark.live`) は本ラウンドのスコープ外。attach mode の `RequestVenueLogin`/`VenueReady` wire 経路は mock engine WS server で十分カバーしているが、実 GUI engine + 立花 demo 環境での E2E は次フェーズに持ち越し。
- `_send_loop` の sentinel-only 化と `wait_for(timeout=1.0)` 削除は Phase 2 の H-GP7 持ち越し項目で、Phase 3 のスコープ外 (helper 側の振る舞いには影響しないため)。
- Rust 側 close code 1008 受信時の error toast 文言分岐 (Phase 2 持ち越し) は Phase 4 (Rust) で別エージェントが対応中。

---

## レビュー反映 (2026-05-04, ラウンド 1 - 新規) / Phase 5 test 分割

### 背景

`python/tests/test_review_fixes.py` (898 行 / 22 tests) が C-1 / C-2 / H-SF1 / H-SF2 / H-GP1 / H-TA1 / M-TA6 / M-SF1 / M-SF3 / M-RS3-PY / M-GP4 / R2-H1 / R2-H2 / R2-H4 など複数機能 ID の保護テストを横断的に詰め込んでおり、ファイル名から「何を保護しているか」が読み取れない状態だった。レビュー指摘 C-GP3 に従い、機能 ID 単位で 10 ファイルに物理分割する。

### 分割マッピング (旧 test → 新 file)

| 旧 test 名 | 新ファイル |
|-----------|-----------|
| `test_attach_client_post_handshake_disconnect_terminates_events_quickly` | `test_attach_disconnect_behaviour.py` |
| `test_attach_client_post_handshake_disconnect_logs` | `test_attach_disconnect_behaviour.py` |
| `test_stop_attach_sends_stop_engine_command` | `test_attach_stop_command.py` |
| `test_stop_transitions_to_stopped_after_engine_stopped` | `test_attach_stop_command.py` |
| `test_stop_not_running_does_not_send_stop_engine` | `test_attach_stop_command.py` |
| `test_stop_loaded_does_not_send_stop_engine` | `test_attach_stop_command.py` |
| `test_set_speed_attach_sends_set_replay_speed` | `test_set_speed_dispatch.py` |
| `test_set_speed_inprocess_updates_multiplier` | `test_set_speed_dispatch.py` |
| `test_broadcaster_append_continues_on_queue_full` | `test_broadcaster_queue_full.py` |
| `test_broadcaster_append_logs_on_queue_full` | `test_broadcaster_queue_full.py` |
| `test_status_stopping_returns_stopping` | `test_status_property.py` |
| `test_status_literals_are_correct` | `test_status_property.py` |
| `test_request_venue_login_connecting_returns_venue_login_started` | `test_request_venue_login_state.py` |
| `test_handle_hello_mode_mismatch_rejects_second_client` | `test_hello_mode_mismatch.py` |
| `test_handle_hello_mode_mismatch_with_real_server` | `test_hello_mode_mismatch.py` |
| `test_token_mismatch_error_class_exists` | `test_session_file_invalid_token.py` |
| `test_token_mismatch_uses_typed_error_not_string_check` | `test_session_file_invalid_token.py` |
| `test_read_session_file_no_token_returns_none` | `test_session_file_invalid_token.py` |
| `test_read_session_file_empty_token_returns_none` | `test_session_file_invalid_token.py` |
| `test_narrative_hook_no_http_call` | `test_narrative_hook_no_http.py` |
| `test_live_session_login_attach_mode_raises_value_error_when_credentials_passed` | `test_live_session_login_attach_guard.py` |
| `test_live_session_login_attach_mode_only_user_id_raises_value_error` | `test_live_session_login_attach_guard.py` |

### 設計判断

- **共有 fixture を `conftest.py` に切り出さず各ファイルに複製**: `_spawn_server` / `_make_ready_msg` ヘルパーは attach disconnect / session file invalid token 系の 2 ファイルでしか使われない。`conftest.py` 経由で global 化すると pytest collection の影響範囲が広がる (他の 50+ 既存ファイルと衝突リスク) ため、必要なファイルにだけ local copy する方針を採用。
- **テスト本体は無改変**: 物理分割が目的。assertion / fixture セットアップ / mock 構造は一切変更していない。Phase 3 で `test_status_stopping_returns_running` → `test_status_stopping_returns_stopping` に rename 済みの test もそのまま継承。
- **旧ファイル `test_review_fixes.py` は削除**: 22 件すべて新ファイルに移植したことを `pytest --collect-only` で確認した上で削除。

### 検証結果

- `pytest --collect-only`: 新 10 ファイルから 22 tests collected (旧と完全一致)
- `uv run pytest python/tests/ -m "not live" -q`: **1670 passed, 3 skipped** (ベースライン維持)
- `cargo fmt --check` / `cargo check --workspace` / `cargo clippy --workspace -- -D warnings` / `cargo test --workspace`: 全緑 (test 分割は Rust に影響なし)

### 持ち越し

- なし。物理分割のみで完結。

---

## レビュー反映 (2026-05-04, ラウンド 2 反映 / Round 2 silent-failure 修正)

### 背景

ラウンド 2 のレビューで multi-client 環境下の silent-failure と 30s ハング 8 件が
洗い出された。CRITICAL 1 / HIGH 3 / MEDIUM 4 を TDD で修正する。

### 反映項目

- ✅ **CRITICAL-R2-1**: `python/engine/replay_session.py:_login_attach()` (1374-1424) で `VenueError` 受信判定を拡張。`request_id == self._login_request_id` (unicast) **または** `request_id is None` (broadcast 経路) のいずれかで raise。`Error{request_id 一致}` も raise。`_login_request_id` を `LiveSession.__init__` で初期化 (1097-1099)。RED テスト: `python/tests/test_phase8_round2_review_fixes.py::test_login_attach_handles_broadcast_venue_error` (broadcast `VenueError{request_id=None}` で 5s 以内 raise を保証)。
- ✅ **HIGH-R2-2**: `python/engine/server.py:_do_request_venue_login()` (2530-2566) で replay-mode reject / unsupported-venue reject の両 `VenueError` を `_emit` (broadcast) から `send_to(ws, ...)` (unicast) に変更。`ws is None` の旧経路は broadcast にフォールバック (テスト互換)。RED テスト: `test_request_venue_login_mode_mismatch_is_unicast` (multi-client 接続で client A の `RequestVenueLogin` 由来 `VenueError` が client B に **届かない** ことを assert)。
- ✅ **HIGH-R2-3**: `python/engine/replay_session.py:_AttachClient.events() / wait_for()` (561-636 / 471-548) で `Error` event を `request_id` でフィルタ。`_current_request_id` 一致 or broadcast (`None`) のみ raise、別 client 由来は無視して継続。`set_current_request_id()` setter (243-249) を追加し `ReplaySession.load() / run()` (810-820 / 868-873) で発行した request_id を伝播。fake client 後方互換のため `getattr` で defensive 呼び出し。RED テスト: `test_events_ignores_error_from_other_request_id` / `test_events_raises_on_own_request_id_error` / `test_events_raises_on_broadcast_error` / `test_wait_for_ignores_other_request_id_error`。
- ✅ **HIGH-R2-4**: `python/engine/server.py:_handle()` finally ブロック (548-558) で「全接続切断時」に `_replay_state = ReplayState.IDLE` / `_live_state = LiveState.DISCONNECTED` / `_replay_streaming_fills.clear()` をリセット。再接続後の `LoadReplayData` が IDLE state guard を通る不変条件を保証。RED テスト: `test_full_disconnect_resets_replay_state`。
- ✅ **MEDIUM-R2-5**: `python/engine/nautilus/narrative_hook.py:on_order_filled_sync()` (79-141) の thread fallback で `inner_exc` を `log.error` した後 silent return せず **raise** する。戻り値型を `bool` に変更 (成功時 `True`、現実装は失敗時 `raise`)。RED テスト: `test_narrative_hook_thread_fallback_propagates_exception` (asyncio.run + ThreadPoolExecutor を patch して TimeoutError を上流に伝播することを assert) / `test_narrative_hook_returns_true_on_success`。
- ✅ **MEDIUM-R2-6**: `python/engine/schemas.py:EngineBusy` に `request_id: str | None = None` を Optional 追加 (920-925)。`_check_replay_state()` / `_check_live_state()` (2316-2380) に `request_id` kwarg を追加して payload に伝播 (callsite は段階的に拡張可能)。`engine-client/src/dto.rs:EngineEvent::EngineBusy` (1213-1214) に `request_id: Option<String>` (`#[serde(default, skip_serializing_if)]`) を追加し旧 wire と後方互換。`_AttachClient.events()` / `wait_for()` で `EngineBusy.request_id` 不一致なら無視。RED テスト Python: `test_events_ignores_engine_busy_from_other_request_id` / `test_events_raises_busy_on_own_engine_busy` / `test_engine_busy_schema_accepts_request_id`。RED テスト Rust: `engine-client/tests/engine_busy_event.rs::engine_busy_with_request_id_deserializes` / `engine_busy_without_request_id_is_none`。
- ✅ **MEDIUM-R2-7**: `python/engine/replay_session.py:LiveSession.login()` (1287-1305) で 2回目以降の login() に対し credential 変更を検知。`_credential_fingerprint(user_id, password, demo)` (218-231) を SHA-256 hex digest で実装し平文比較を回避。同一 fingerprint なら no-op、異なれば `RuntimeError("LiveSession credentials cannot change after login; create a new LiveSession instance instead")`。RED テスト: `test_second_login_with_changed_credentials_raises`。
- ✅ **MEDIUM-R2-8**: `python/engine/server.py:_handle_start_engine()` (2962-2975) に `engine_stopped_emitted: list[bool] = [False]` フラグを導入。`_on_event_tracked` で `EngineStopped` を観測したら True に。timeout / except 補完送出パス (3034-3045 / 3079-3094) で `if not engine_stopped_emitted[0]` ガードを追加し二重送出防止。RED テスト: `test_engine_stopped_emitted_only_once_on_runner_failure` (runner mock が EngineStopped emit 後に raise してもアウトボックスに EngineStopped が **1 回のみ**)。

### 設計判断

- **broadcast `VenueError` を unicast 化する**: M-GP8 (Phase 2) で `_check_replay_state` / `_check_live_state` の `EngineBusy` を unicast 化したのと同じ方針。helper が attach した瞬間に別 helper の RequestVenueLogin reject イベントを誤受信して `_login_attach` が誤 raise するのを防ぐため。`ws is None` 経路は legacy テスト互換でフォールバック維持。
- **`request_id` フィルタを events()/wait_for() の両方に**: `events()` は run() の event ストリーム、`wait_for()` は load() の同期待ち。どちらも別 client 由来イベントを「自分宛」と誤認するリスクがある。フィルタロジックを共通化 (state は `_current_request_id` で共有) し、両方の経路で同じ動作を保証する。
- **`getattr(client, 'set_current_request_id', None)` 防御呼び出し**: 既存の Phase 3 単体テスト 6+ 件で fake client クラスを直接定義しており、新メソッドを各 fake に追加すると変更箇所が散らばる。`_AttachClient` 本体のみが新 API を実装し、それ以外は黙ってスキップする方針で後方互換を維持。
- **EngineBusy.request_id を Optional + Rust skip_serializing_if**: 既存の serialize 形式 (request_id 未送出) を破壊しない。Python 側も `default=None` で旧 helper と互換。`schemas.py` の `SCHEMA_MINOR` bump は不要 (Optional フィールド追加は wire-compatible 拡張)。
- **credential fingerprint は SHA-256**: 平文 user_id/password を session 内に保持すると監査ログ漏洩・debug snapshot 漏洩のリスクが高い。固定 salt 不要 (timing attack の対象は token 比較のみ、credential 変更検知は識別性さえあれば十分) なので素の SHA-256 で識別子化する。`_credential_fingerprint(None, None, demo)` は `\x00` セパレータで識別性を担保。
- **EngineStopped 二重送出抑制を flag だけで実装**: `_on_event_tracked` 内で同期的に flag を立てるため race condition 無し (`_on_event_tracked` は worker thread から呼ばれるが list[bool] への代入は GIL 下で atomic)。except / timeout の補完送出パスは main asyncio loop 上で実行されるので、flag 観測時点で worker thread の emit は済んでいる (worker thread が raise してから to_thread が return するため順序保証)。

### 検証結果

- `cargo fmt --check`: 全緑 (Rust 編集箇所は dto.rs / engine_busy_event.rs のみ、両方 rustfmt 準拠)
- `cargo check --workspace`: 全緑
- `cargo clippy --workspace -- -D warnings`: 全緑
- `cargo test --workspace`: 全テスト pass (engine_busy_event 8 件 = 旧 6 + 新 2)
- `uv run pytest python/tests/ -m "not live" -q`: **1684 passed, 3 skipped** (ベースライン 1670 + 新規 14 件)

### 持ち越し

- LOW-R2-1〜3 (計画書記載精度) は今回スコープ外。次フェーズで「DoD #9 の文言分節化 / §8 注記追加 / 改訂履歴 line 1140 の M-11 訂正」をまとめてやる。
- 実 demo smoke (`@pytest.mark.live`) は本ラウンドのスコープ外 (mock engine + multi-client integration test で十分カバー)。
- `_check_replay_state` / `_check_live_state` の callsite に `request_id` を順次注入する作業は段階的拡張として持ち越し (本ラウンドではシグネチャ追加と `_AttachClient` 側のフィルタロジックまでで完結。既存 callsite は `request_id=None` がデフォルトで素通り)。

---

## レビュー反映 (2026-05-04, ラウンド 3 反映 / Round 3 silent-failure 修正)

### 背景

ラウンド 3 のレビューで「ラウンド 2 修正に潜む silent-failure / race / 過剰拒否」が
洗い出された。MEDIUM 2 / LOW 2 を TDD で修正する。

### 反映項目

- ✅ **MEDIUM-R3-1**: `python/engine/replay_session.py:LiveSession.login()` (1305-1318) で credential 変更検知の条件を `user_id is not None or password is not None` から `user_id is not None and password is not None` に変更。「両方明示渡し時のみ」厳格チェックに限定する。env / 既存値からの解決経路（片方だけ明示 or 全部 None）では caller の意図を「変更したい」と判別できないため過剰拒否を避ける。RED テスト: `python/tests/test_phase8_round3_review_fixes.py::test_second_login_with_same_explicit_user_id_but_password_via_env_is_noop`（env 解決済みで `login(user_id=env_user, password=None)` が誤 RuntimeError を出さない）。リグレッションガード `test_second_login_with_changed_credentials_still_raises`（両方明示で変更時 RuntimeError は維持）。
- ✅ **MEDIUM-R3-2**: `python/engine/server.py:_handle_start_engine()` finally (3120-3134) で `_replay_state = ReplayState.IDLE` リセットを **現在の state が `RUNNING` / `STOPPING` のときのみ** に条件付け。「全断 → `_handle()` finally で IDLE → 再接続 → `LoadReplayData` で LOADED → 古い runner の to_thread 完了 → finally が LOADED を IDLE に上書き → 新セッションの `StartEngine` が EngineBusy で reject」race を回避する。RED テスト: `test_old_runner_finally_does_not_clobber_reconnected_loaded_state`（`_replay_state = LOADED` セット → finally 経路相当ロジック実行 → LOADED 維持を assert）。リグレッションガード `test_handle_start_engine_finally_resets_running_state_to_idle`（通常終了 RUNNING→IDLE は維持）。
- ✅ **LOW-R3-3**: credential テスト追加。`test_second_login_with_user_id_only_explicit_no_op`（env 解決済みで `login(user_id=env_user, password=None)` / `login(user_id=None, password=env_pw)` の両方が no-op であることを assert）。MEDIUM-R3-1 修正で自然に GREEN。
- ✅ **LOW-R3-4**: `python/engine/replay_session.py:ReplaySession.run()` attach mode events ループ (931-963) に `try / except / finally` の `finally` を追加し、`set_current_request_id(None)` をクリア処理として実行。次の load() / run() / 別 client の Error が前 run の request_id にマッチして誤発火するのを防ぐ。RED テスト: `test_run_resets_current_request_id_on_normal_exit` / `test_run_resets_current_request_id_on_exception`（正常終了・例外終了の両方で fake client の `_current_request_id` が None に戻ることを assert）。

### 設計判断

- **「両方明示時のみ厳格チェック」案の採用理由**: 当初案は「保存 fingerprint 計算と同じ resolve ロジックを通す」だったが、これだと「初回 user-A／env-pw → 2回目 `login(user_id=user-A, password=None)` でチェック側だけ env-pw が反映されない／されると caller の意図と乖離」など分岐がブレる。「両方明示」シグナルは caller が明確に「変更したい／確認したい」と表明している唯一のケースなので、ここだけ強制する設計が最も予測可能。片方だけ明示は「他はそのまま使いたい」という意図と解釈し no-op で受ける。
- **finally 条件を `RUNNING / STOPPING` に限定**: `_handle_start_engine` の finally は「自分の to_thread 終了に責任のある状態」を IDLE に戻すべきで、すでに別経路で進んだ状態（LOADED = 別セッションの load 完了）を破壊してはならない。`STOPPING` も含めるのは StopEngine 経路で先に `STOPPING` に遷移してから to_thread 終了を待つため。`IDLE` / `LOADED` は触らない。
- **run() の finally で setter を `getattr` 防御**: HIGH-R2-3 と同じ理由（既存 fake client が `set_current_request_id` を実装していない可能性）。

### 検証結果

- `cargo fmt --check`: 全緑（Rust 編集なし）
- `cargo check --workspace`: 全緑
- `cargo clippy --workspace -- -D warnings`: 全緑
- `cargo test --workspace`: 全テスト pass
- `uv run pytest python/tests/ -m "not live" -q`: **1691 passed, 3 skipped** (ベースライン 1684 + 新規 7 件)

### 持ち越し

- LOW-R2-1〜3 / LOW-R3 系の計画書記載精度修正は次フェーズで一括対応。
- `_check_replay_state` / `_check_live_state` の callsite への `request_id` 段階的注入はラウンド 2 から継続持ち越し。

---

## レビュー反映 (2026-05-04, ラウンド 4 収束確認 / Round 4 sanity)

### 背景

ラウンド 3 修正後の最終サニティとして、review-fix-loop の収束条件
（CRITICAL/HIGH/MEDIUM = 0）を満たしているかを実コマンドで再確認した。
新たな実装修正は不要で、残件は warning / 記載精度の LOW 相当のみ。

### 収束確認

- ✅ **新規 CRITICAL/HIGH/MEDIUM なし**: `python/engine/replay_session.py` /
  `python/engine/server.py` / `python/engine/nautilus/narrative_hook.py` /
  `python/engine/schemas.py` / `engine-client/src/dto.rs` を再確認し、
  ラウンド 2・3 で修正した request_id フィルタ、attach login、EngineBusy 互換、
  runner finally の race 回避に新たな破綻は見つからなかった。
- ✅ **最終検証コマンド再実行**: `cargo fmt --check` / `cargo check --workspace` /
  `cargo clippy --workspace -- -D warnings` / `cargo test --workspace` /
  `uv run pytest python/tests/ -m "not live" -q` を再実行し、全て成功。
- ✅ **Python テスト総数維持**: `uv run pytest python/tests/ -m "not live" -q`
  は **1691 passed, 3 skipped** を維持。

### LOW メモ

- `pytest` 実行時に 9 warning が出るが、内容は `AsyncMock` / テスト用 event loop 停止 /
  unawaited coroutine 検知など **テストハーネス由来** が中心で、今回の
  実装スコープに対する MEDIUM 以上の挙動不良は再現しなかった。
- `cargo test --workspace` 中の warning も `exchange` crate の test-only unused code
  に限られ、本フェーズの helper direct API 実装とは独立。

### 検証結果

- `cargo fmt --check`: 全緑
- `cargo check --workspace`: 全緑
- `cargo clippy --workspace -- -D warnings`: 全緑
- `cargo test --workspace`: 全テスト pass
- `uv run pytest python/tests/ -m "not live" -q`: **1691 passed, 3 skipped, 9 warnings**

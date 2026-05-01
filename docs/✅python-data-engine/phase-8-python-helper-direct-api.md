# Phase 8 — Python 単独モード化 / Rust HTTP API 廃止計画（改訂版）

作成日: 2026-05-01
改訂日: 2026-05-01（[phase-8-review.md](./phase-8-review.md) / [phase-8-review2.md](./phase-8-review2.md) を反映）
対象: `src/replay_api.rs` / `src/api/order_api.rs` / `src/api/agent_api.rs` / `src/api/mod.rs` 廃止と Python helper class 新設
方針: **HTTP API を経由せず Python helper class を直接呼び出すユースケースを正規ルートに昇格させ、Rust 側 HTTP API を完全廃止する**

---

## 0. ゴール

1. **Python 単独モード**を一級市民化する。`flowsurface`（Iced GUI）が起動していなくても backtest / replay を Python だけで完結できる
2. Rust 側 HTTP control API（ポート 9876）を release build で**完全廃止**する。4 ファイル合計 約 6,758 行を削除
3. E2E テストの主流を `bash + curl` から `pytest + Python helper` に移し替える
4. GUI が必要な操作（sidebar toggle 等）は最小処置で済ませる（移植ではなく削除を基本方針とする）

**非ゴール**:

- WebSocket IPC（ポート 19876, schema_major / schema_minor）の廃止 — GUI ↔ engine 間通信は維持する
- `NautilusRunner` 内部実装の改変 — 既存 `start_backtest_replay_streaming` の signature と動作はそのまま再利用する
- Iced GUI のレイアウト・ペイン生成ロジックへの介入 — pane 生成は今と同じく `ReplayDataLoaded` 受信時に GUI 側で完結させる

### 0.1 着手前に確定済みの設計判断（Q4 解決）

`open-questions.md` の Q4 相当（GUI と helper の同時起動）について、レビューの指摘を受けて Phase 8.1 着手前に次を確定する：

- **helper は in-process オンリーで動作**し、WS IPC（:19876）を**一切立てない**
- **GUI と helper の同時運用は明示的に非サポート**（GUI 起動中は GUI 内部で engine を spawn / attach し、helper は GUI を起動しないモード専用）
- helper class からは `Command` 列挙体や IPC schema を一切 expose しない（ユーザーは `dict` で event を受け取る）

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

すべて IPC を介さず in-process で driving できる構造で実装済み。**helper class は薄いラッパーで足りる**。

### 1.3 GUI 経路は HTTP を経由していない（order_api 全廃の安全性）

[src/main.rs:2140-2199](../../src/main.rs#L2140-L2199) で GUI 発注は `Action::SubmitOrder` → `engine_client::dto::SubmitOrderRequest` を直接組んで `Command::SubmitOrder` を IPC 送信している。`order_api.rs::handle_submit_request` は経由しない。

つまり：

- `OrderGuardConfig` の rate limiter / qty/yen cap は **`/api/order/submit` HTTP path 専用の防壁**で、GUI 経路にはもともと適用されていない
- HTTP path を廃止しても GUI 発注ロジックは無傷
- `order_api.rs` 3,490 行は丸ごと安全に削除できる

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

廃止対象ファイル（合計 **約 6,758 行**）：

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
| [scripts/run-replay-debug.sh](../../scripts/run-replay-debug.sh) | build + GUI 起動 + HTTP 投入 | `python -m engine.replay_session run ...` で完結 |
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
                     │ 直接 import（IPC 不在・HTTP 不在）
                     ▼
                NautilusRunner（既存）
                     ▲
                     │ WS IPC（GUI 起動時のみ）
┌────────────────────┴────────────────────────────────────┐
│ flowsurface (Iced GUI)                                   │
│   - HTTP API モジュール (replay_api / order_api /         │
│     agent_api / api/mod.rs) は削除                        │
│   - 既存 WS IPC で engine と通信                          │
│   - Python が spawn する場合と attach する場合の両方を     │
│     維持                                                  │
└─────────────────────────────────────────────────────────┘
```

### 3.2 起動経路の対応表

| ユースケース | 旧 | 新 |
|------------|-----|-----|
| GUI で replay を見る | `cargo run -- --mode replay` + `curl POST /load` | `cargo run -- --mode replay` 起動 → GUI 内 `File > Replay を開始...` メニューでパラメータ入力（後述 §3.4） |
| backtest を回すだけ（GUI 不要） | （事実上不可能） | `python -m engine.replay_session run --strategy=... --instrument=... --start=... --end=...` |
| pytest から backtest を駆動 | （HTTP 経由で fragile） | `with ReplaySession() as s:` を直接 import |
| 発注（live モード） | GUI から手動、または `curl POST /api/order/submit` | GUI から手動（既存）。pytest からは `LiveSession` |

### 3.3 GUI と helper の関係（§0.1 で確定）

GUI が立っているとき helper を**ユーザーが同時に呼ぶことは想定しない**。GUI が engine を内部 spawn または attach し、内部 IPC で driving する経路は今と完全に同じ。helper class は「**GUI を起動しない**」起動経路を新設するもの。

これにより「pane-ready ack」のような GUI 整合性問題は構造的に消える：

- GUI なし: 待つペインがない → `ReplaySession.load()` は engine load 完了で即 return
- GUI あり: GUI 内で `ReplayDataLoaded` を直接受信して pane 生成 → 外部からの ack 待ち契約は不要

### 3.4 GUI における replay 起動 UX（A 案採用）

HTTP API を廃止すると、現状 [src/native_menu.rs:83-90](../../src/native_menu.rs#L83) の replay モード時メニュー項目「ストラテジーを開く...」だけでは instrument / 期間 / granularity / initial_cash を入力する経路が無い。`File > Replay を開始...` フォーム式メニューを新設してこの入力経路を担う。

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
- 既存 `Command::LoadReplayData` / `Command::StartEngine` IPC コマンドはそのまま再利用（HTTP API が叩いていた IPC と同じもの）
- フォームのデフォルト値・前回入力記憶などは [§7.2 Q3](#72-未決事項) で別途検討

---

## 4. helper class API 設計

### 4.1 `engine.replay_session.ReplaySession`

**設計方針**：

- **contextmanager 必須**（`with` 文で構造的に lifecycle を強制 → 並列利用 fail を構造解消）
- **callback ベース**（`on_event: Callable`）で `start_backtest_replay_streaming` をそのまま薄くラップ。queue / thread / generator は導入しない
- IPC schema や `Command` 列挙体を一切 expose しない（ユーザーは `dict` で event を受ける）

```python
# python/engine/replay_session.py（単一ファイル構成）

from typing import Callable, Literal
from pathlib import Path

class ReplaySession:
    """Python 単独で nautilus replay backtest を駆動する helper.

    GUI を経由せずに NautilusRunner を呼び出す。HTTP API（旧 :9876）の
    機能等価物を in-process API として提供する。

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
    ) -> None:
        """引数の検証のみ。重い初期化（NautilusRunner 構築）は __enter__ で行う。"""

    def __enter__(self) -> "ReplaySession":
        """NautilusRunner を構築。同一インスタンスが二度 __enter__ されたら例外。"""

    def __exit__(self, exc_type, exc, tb) -> None:
        """NautilusRunner を dispose。run() 中なら stop_event を set してから dispose。"""

    # ---- load 系（旧 POST /api/replay/load 相当） ----
    def load(
        self,
        instrument_id: str,
        start_date: str,
        end_date: str,
        granularity: Literal["Trade", "Minute", "Daily"] = "Daily",
    ) -> None:
        """データの存在確認のみ実施（旧 HTTP load の契約と同じ）。
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

        on_event は呼び出し thread 上で実行される（threading 不要）。
        中断は Ctrl-C / SIGINT。set_speed() / stop() を別 thread から呼ぶ
        ケースに限り threading.Event 経由でハンドリングする。
        """

    # ---- runtime control ----
    def set_speed(self, multiplier: int) -> None:
        """旧 POST /api/replay/control 相当。run() 中の生成スレッドから読まれる。"""

    def stop(self) -> None:
        """別 thread から呼ぶ非同期キャンセル。run() の on_event ループが
        次の tick で終端に到達する。"""

    # ---- snapshot ----
    @property
    def portfolio(self) -> dict | None:
        """旧 GET /api/replay/portfolio 相当。最後の ReplayBuyingPower イベントの dict。"""

    @property
    def status(self) -> Literal["idle", "loaded", "running", "stopped", "errored"]:
        """旧 GET /api/replay/status 相当。"""

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

### 4.2 `engine.replay_session.LiveSession`

**Phase 8.1 必須スコープ**：旧 `/api/sidebar/tachibana/request-login` 経路を helper で代替する。Order 系 E2E 7 本が依存しているため、`LiveSession.login()` を Phase 8.1 で先行実装する。

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
    ) -> None: ...

    def __enter__(self) -> "LiveSession": ...
    def __exit__(self, exc_type, exc, tb) -> None: ...

    # ---- Phase 8.1 必須 ----
    def login(
        self,
        *,
        user_id: str | None = None,    # 既定: $DEV_TACHIBANA_USER_ID
        password: str | None = None,   # 既定: $DEV_TACHIBANA_PASSWORD
    ) -> None:
        """旧 POST /api/sidebar/tachibana/request-login 相当。
        env 経由 cred で立花へログインし session を確立する。"""

    # ---- Phase 8.3 で追加 ----
    def submit_order(self, **kwargs) -> str: ...
    def modify_order(self, order_id: str, **kwargs) -> None: ...
    def cancel_order(self, order_id: str) -> None: ...
    def cancel_all(self) -> None: ...
    @property
    def orders(self) -> list[dict]: ...
```

### 4.3 CLI: `python -m engine.replay_session run ...`

```bash
uv run python -m engine.replay_session run \
    --strategy docs/example/buy_and_hold.py \
    --instrument 1301.TSE \
    --start 2025-01-06 \
    --end 2025-03-31 \
    --granularity Daily \
    --initial-cash 1000000

# event stream を JSONL で stdout に書き出す（| jq でフィルタ可能）
```

CLI 内部で `with ReplaySession() as s:` を使い、ユーザーは contextmanager を意識しなくてよい。

> 注: 既存 `python -m engine ...`（WS server 起動）と区別するため、helper は単一ファイル `python/engine/replay_session.py` に置き、`python -m engine.replay_session` で叩く。`python/engine/__main__.py`（WS server）は手を入れない。

---

## 5. 段階的移行プラン（5 → 4 phase に圧縮）

> レビューで指摘された「Phase 8.2 から 8.4 までの間に HTTP リグレッション盲点が生じる」問題を解消するため、HTTP 削除と pytest 移行を同一 Phase に統合する。

### Phase 8.0 — 設計確定（着手前の合意形成）

- [ ] §0.1 の前提条件（helper は in-process オンリー、GUI 同時起動非サポート）を README または CLAUDE.md に追記
- [ ] [open-questions.md](./open-questions.md) Q4 相当をクローズ済みに更新
- [ ] examples で書く buy_and_hold の callback 形 1 本を最終確認

**完了条件**: helper API 形（contextmanager + callback）が確定し、Phase 8.1 着手の障害がない。

### Phase 8.1 — helper class + CLI 新設 + GUI replay フォーム（破壊的変更なし）

#### Phase 8.1a: Python helper class + CLI

- [ ] `python/engine/replay_session.py` を**単一ファイル**で新規作成（`ReplaySession` + `LiveSession` の双方を含む）
- [ ] `ReplaySession` を `NautilusRunner.start_backtest_replay_streaming` の薄いラッパーとして実装（contextmanager + callback ベース）
- [ ] `LiveSession.login()` を**必須スコープ**として実装（Order E2E pytest 化の前提）
- [ ] `python/engine/replay_session.py` に `if __name__ == "__main__"` ガード + argparse で CLI を提供
- [ ] pytest で helper の golden path テストを追加：
  - [ ] `python/tests/test_replay_session.py`（load → run → portfolio）
  - [ ] `python/tests/test_replay_session_stop.py`（別 thread から stop()）
  - [ ] `python/tests/test_replay_session_speed.py`（run() 中の set_speed()）
  - [ ] `python/tests/test_replay_session_cli.py`（subprocess 経由 CLI）
  - [ ] `python/tests/test_live_session_login.py`（demo 立花への login smoke）
- [ ] `docs/example/buy_and_hold.py` を helper 経由で動かすコマンドを README に追記

#### Phase 8.1b: GUI replay 起動フォーム（A 案・§3.4 参照）

> Phase 8.3 で HTTP API を消した瞬間に「GUI で replay を見る」入力経路がゼロになるため、Phase 8.3 着手前にこのフォームが必須。Phase 8.1 のスコープに含める。

- [ ] [src/native_menu.rs](../../src/native_menu.rs) replay モード時のメニューに `File > Replay を開始...` を追加
- [ ] 既存「ストラテジーを開く...」独立メニュー項目を削除（フォーム内に統合）
- [ ] `Action::OpenReplayDialog` を `Message` フローに追加し、iced ダイアログを表示
- [ ] ダイアログ実装：instrument_id / start_date / end_date / granularity / strategy_file / initial_cash の入力フィールド + 入力検証
- [ ] OK 押下時に `Command::LoadReplayData` → `Command::StartEngine` を IPC 送信（既存 IPC コマンドを再利用）
- [ ] iced 単体テストではなく [src/main.rs:4239-4242](../../src/main.rs#L4239-L4242) と同様の string-assertion で「メニュー項目が存在する」「ダイアログが view() ツリーに含まれる」をリグレッションガードする

#### 共通

- [ ] **既存 HTTP API はそのまま残す**（Phase 8.3 まで）

**完了条件**:

- `uv run python -m engine.replay_session run ...` 1 コマンドで GUI なしの backtest が完走し、event stream が stdout に流れる
- pytest 全 PASS
- README に Ctrl-C 中断 / 別 thread からの stop パターンが記載されている
- `cargo run -- --mode replay` で起動した GUI から `File > Replay を開始...` のフォーム経由で backtest が完走し、HTTP API を一切叩かずに data が pane に流れる
- HTTP API 経由の駆動経路（旧 `curl POST /api/replay/load`）も並走で動作している（Phase 8.3 まで残す）

### Phase 8.2 — GUI 専用 endpoint の最小処置（移植ではなく削除中心）

> レビュー: Iced は `update()` 単体呼び出し用 test harness を持たず、本リポジトリの既存テスト（[src/main.rs:4239-4242](../../src/main.rs#L4239-L4242)）も string-assertion で逃げている実績がある。Phase 8.2 は本格的な unit test 化に挑まず、**削除を基本方針**とする。

- [ ] `/api/sidebar/toggle-venue` の呼び出し箇所を全削除（E2E 側）
- [ ] `/api/test/tachibana/cancel-helper` / `/api/test/tachibana/delete-session` を debug build 限定の test backdoor として残す（release build からは削除）
- [ ] tachibana 系の実 WebSocket 統合テストは Phase 8.1 の `LiveSession` で代替する想定で、GUI 内部状態のテストは string-assertion ベースで最小化

**完了条件**: Phase 8.3 着手時点で、`/api/sidebar/*` `/api/test/*` を叩く E2E スクリプトが残っていない。

### Phase 8.3 — HTTP API 削除 + bash → pytest helper 一括置換

> Phase 8.2 と 8.4 を統合。HTTP 削除と pytest 化を同時実施することで「HTTP 経路は誰もテストしない期間」を 0 にする。

- [ ] `tests/e2e/*.sh` を pytest 版に置換：
  - [ ] `s56_replay_pane_autogen.sh` → Iced string-assertion or 削除
  - [ ] `s57_replay_buying_power_smoke.sh` → `python/tests/e2e/test_replay_buying_power.py`
  - [ ] `s58_replay_load_smoke.sh` → `test_replay_load.py`
  - [ ] `s90_replay_user_flow.sh` → `test_replay_user_flow.py`
  - [ ] `s80_order_*` / `s81_*` / `s82_*` / `s83_*` → `python/tests/e2e/test_order_*.py`
  - [ ] `tachibana_demo_login.sh` / `tachibana_relogin_after_cancel.sh` → `test_tachibana_login.py`
  - [ ] **`s55_mode_startup_smoke.sh` / `smoke.sh` は維持**（HTTP 不使用、プロセス起動・観測が試験対象）
- [ ] `LiveSession.submit_order()` / `modify_order()` / `cancel_order()` / `cancel_all()` / `orders` を Phase 8.1 から繰り越し実装
- [ ] Rust 側 HTTP API モジュール削除：
  - [ ] [src/replay_api.rs](../../src/replay_api.rs) 削除（約 2,943 行）
  - [ ] [src/api/order_api.rs](../../src/api/order_api.rs) 削除（約 3,490 行）
  - [ ] [src/api/agent_api.rs](../../src/api/agent_api.rs) 削除（約 323 行）
  - [ ] [src/api/mod.rs](../../src/api/mod.rs) 削除（2 行）
- [ ] [src/main.rs](../../src/main.rs) から `replay_api::spawn` 呼び出し / `ControlApiCommand` enum / `replay_api_stream` Subscription / `REPLAY_API_STATE` 静的変数 / `OrderApiState` `AgentApiState` を全削除
- [ ] [scripts/run-replay-debug.sh](../../scripts/run-replay-debug.sh) / [scripts/replay_dev_load.sh](../../scripts/replay_dev_load.sh) を削除（または `python -m engine.replay_session run` ラッパーに書き換え）
- [ ] `.vscode/launch.json` の `replay - Rust: Debug (CodeLLDB)` 構成を削除（Python helper 用構成に置換）
- [ ] [CLAUDE.md](../../.claude/CLAUDE.md) の replay 関連セクションを書き直す
- [ ] [docs/wiki/replay.md](../wiki/replay.md) を helper ベースの記述に書き換え

**完了条件**:

- **release build で** ポート 9876 を listen するプロセスが存在しない（debug build は test backdoor 残存可）
- `cargo build --release` が成功する
- `cargo test --workspace` 全 PASS
- `uv run pytest python/tests/` 全 PASS

---

## 6. テスト戦略

### 6.1 Phase 8.1 で追加するテスト

| ファイル | 内容 |
|---------|------|
| `python/tests/test_replay_session.py` | helper の load → run → portfolio の golden path（callback ベース） |
| `python/tests/test_replay_session_cli.py` | `python -m engine.replay_session run ...` の subprocess 起動テスト |
| `python/tests/test_replay_session_stop.py` | run() の途中で別 thread から stop() を呼ぶ → on_event ループが終端に到達する |
| `python/tests/test_replay_session_speed.py` | run() 中に set_speed() で multiplier が反映される |
| `python/tests/test_live_session_login.py` | demo 立花への login smoke（DEV_TACHIBANA_* env 必須） |

### 6.2 Phase 8.3 の移行ガイドライン

- bash + curl の I/O 検証 → pytest の `assert helper.xxx == ...` に置換
- bash の sleep / polling → pytest の `tenacity` retry または event 駆動 wait
- HTTP status code 検証 → 例外 type 検証（`pytest.raises(FileNotFoundError)` 等）

### 6.3 リグレッション保護（簡素化版）

- `python/tests/test_no_http_listener.py` を追加し、Phase 8.3 完了後に `socket.bind(("127.0.0.1", 9876))` が成功する（=誰も listen していない）ことを **release build でのみ** assert する
- Rust 側専用リグレッションテスト（モジュール存在チェック）は**不要** — `cargo build` が通ること自体が削除のリグレッションガードになる

---

## 7. リスクと未決事項

### 7.1 リスク

| # | リスク | 影響 | 軽減策 |
|---|------|------|--------|
| R1 | E2E テスト移行漏れ | カバレッジ低下 | Phase 8.3 で削除と置換を同一 PR にまとめる。並走期間は意図的に作らない（並走は両者が違う実装を叩くだけで意味が薄いため） |
| R2 | Iced GUI 内部状態のテスト表現力不足 | sidebar/test 系の一部シナリオがテストできない | Phase 8.2 で「移植」を諦め「削除 + debug backdoor」にする方針で構造的に許容 |
| R3 | helper class の API 設計が早期に固まらない | 利用側の手戻り | Phase 8.0 で API を確定。Phase 8.1 の examples は callback 形 1 本（buy_and_hold）が動けば確定 |
| R4 | ~~NautilusRunner が in-process で 2 回呼ばれる場合のリソース二重確保~~ | ~~helper の並列利用で fail~~ | **解消**：`__enter__` / `__exit__` で同一インスタンスの二度 enter を例外化。contextmanager 必須化により構造解消 |
| R5 | ~~外部 user の bash スクリプト破壊~~ | ~~外部影響~~ | **不要**：本リポジトリは個人/社内向けで外部 API バージョニング無し。CLAUDE.md / README に「Phase 8.x で HTTP API 廃止」告知のみで十分 |

### 7.2 未決事項

| # | 質問 | 状態 |
|---|------|------|
| ~~Q1~~ | ~~`LiveSession`（旧 `/api/order/*`）は本当に作るか？~~ | **決定**: `LiveSession.login()` のみ Phase 8.1 必須。`submit/modify/cancel` は Phase 8.3 で必要に応じて追加。Order 系 E2E 7 本が `/api/sidebar/tachibana/request-login` 経由でログインしているため、これを置換する helper が必要 |
| Q2 | `/api/agent/narrative` は Python 単独モードで意味があるか？ | A: 残す（user code が narrate を呼べる） / B: 廃止 — Phase 8.3 着手前に決める |
| Q3 | `python -m engine.replay_session run ...` の event 出力フォーマットは JSONL でよいか？ | 既定 JSONL。`--format=table` で human-readable も用意するか別途検討 |
| Q3b | GUI replay フォーム（§3.4）のデフォルト値・前回入力の記憶方針 | 案: 前回入力を `saved-state.json` 内の `replay_form_last_input` に保存し次回起動時に復元（D9 「replay モードでは saved-state.json を load も save も行わない」と矛盾するため、別ファイル `replay-form-cache.json` を新設するか、env で defaults を提供するかを Phase 8.1b 着手前に決める）|
| ~~Q4~~ | ~~GUI と helper を同時起動するユースケース~~ | **決定**（§0.1）: 非サポート。helper は in-process オンリーで WS IPC を立てない |
| Q5 | Phase 8.3 で削除する 6,758 行の中に「単独で価値のあるユーティリティコード」があれば抽出するか | replay_api 内の HTTP raw parser は再利用価値ゼロ。order_api 内の OrderGuard は HTTP 経路専用で GUI には不要のため、抽出候補なし。**candidate 無し** |

---

## 8. Definition of Done

Phase 8 シリーズ完了時点で：

1. **release build で** ポート 9876 を listen しているプロセスが存在しない（debug build は test backdoor 残存可）
2. `python -m engine.replay_session run ...` で GUI 起動なしに backtest が完走する
3. `tests/e2e/*.sh` は `s55_mode_startup_smoke.sh` と `smoke.sh` を残して全削除
4. Rust 側 HTTP API モジュール 4 ファイル（合計 約 6,758 行）が削除されている
5. memory に記録された **「Python 単独でも動くか？」判断軸が満たされている**
6. CLAUDE.md / README / docs/wiki の replay セクションが helper ベースに書き換わっている

---

## 9. 関連ドキュメント

- [phase-8-review.md](./phase-8-review.md) — 本計画への 1 件目レビュー（反映済み）
- [phase-8-review2.md](./phase-8-review2.md) — 本計画への 2 件目レビュー（反映済み）
- [spec.md](./spec.md) — Rust ↔ Python 境界仕様
- [archive/refactor-rust-python-boundary-2026-05-01.md](./archive/refactor-rust-python-boundary-2026-05-01.md) — depth/price 正規化の責務移動（別案件）
- [implementation-plan.md](./implementation-plan.md) — フェーズ 0〜7 の実装計画
- [src/replay_api.rs](../../src/replay_api.rs) — 廃止対象 (2,943 L)
- [src/api/order_api.rs](../../src/api/order_api.rs) — 廃止対象 (3,490 L)
- [src/api/agent_api.rs](../../src/api/agent_api.rs) — 廃止対象 (323 L)
- [src/api/mod.rs](../../src/api/mod.rs) — 廃止対象 (2 L)
- [python/engine/nautilus/engine_runner.py](../../python/engine/nautilus/engine_runner.py) — helper の被ラップ対象

---

## 改訂履歴

| 日付 | 改訂内容 |
|------|---------|
| 2026-05-01 | 初版作成 |
| 2026-05-01 | レビュー 2 件を反映：Phase 8.0 新設（Q4 解決）/ Phase 構成 5→4 圧縮 / `LiveSession.login()` を Phase 8.1 必須に格上げ / Phase 8.2 を「移植」から「削除中心」に簡素化 / 完了条件を release build 限定に緩める / contextmanager 必須化で R4 構造解消 / R5 deprecated phase 削除 / `src/api/mod.rs` を削除リストに追加 / dispatcher と handler の関係を §2.1 に注記 / generator → callback ベースに変更 / リグレッションガードを grep ベースに簡素化 / Q1 と Q4 を未決から決定済みに格上げ |
| 2026-05-01 | GUI replay 起動 UX を A 案（メニューフォーム）に確定：§3.2 起動経路対応表を明確化 / §3.4 を新設して `File > Replay を開始...` フォームの UX フローを記述 / Phase 8.1 を 8.1a（Python helper）と 8.1b（GUI フォーム）に分割 / Phase 8.1 完了条件に GUI フォーム経由の動作を追加 / Q3b（フォームのデフォルト値方針）を未決事項に追加 |

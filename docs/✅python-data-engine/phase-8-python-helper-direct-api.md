# Phase 8 — Python 単独モード化 / Rust HTTP API 廃止計画

作成日: 2026-05-01
対象: `src/replay_api.rs` / `src/api/order_api.rs` / `src/api/agent_api.rs` 廃止と Python helper class 新設
方針: **HTTP API を経由せず Python helper class を直接呼び出すユースケースを正規ルートに昇格させ、Rust 側 HTTP API を完全廃止する**

---

## 0. ゴール

1. **Python 単独モード**を一級市民化する。`flowsurface`（Iced GUI）が起動していなくても backtest / replay を Python だけで完結できる
2. Rust 側 HTTP control API（ポート 9876）を**完全廃止**する。3 ファイル合計 約 6,750 行を削除
3. E2E テストの主流を `bash + curl` から `pytest + Python helper` に移し替える
4. GUI が必要な操作（sidebar toggle 等）は Iced `update()` 直接呼び出しによるユニットテストに移植する

**非ゴール**:

- WebSocket IPC（ポート 19876, schema_major / schema_minor）の廃止 — GUI ↔ engine 間通信は維持する
- `NautilusRunner` 内部実装の改変 — 既存 `start_backtest_replay_streaming` の signature と動作はそのまま再利用する
- Iced GUI のレイアウト・ペイン生成ロジックへの介入 — pane 生成は今と同じく `ReplayDataLoaded` 受信時に GUI 側で完結させる

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

---

## 2. 現状の HTTP API 棚卸し（廃止対象）

### 2.1 Rust 側 HTTP endpoint 一覧

[src/replay_api.rs:1162-1361](../../src/replay_api.rs#L1162-L1361) で raw TCP listener が以下を受け付けている：

| 系統 | endpoint | 行数 | 移行先 |
|------|---------|------|--------|
| **A. Replay 制御** | `POST /api/replay/load` | replay_api.rs | Python `ReplaySession.load()` |
|  | `POST /api/replay/start` |  | `ReplaySession.run()` |
|  | `POST /api/replay/order` |  | `ReplaySession.submit_order()` |
|  | `POST /api/replay/control` |  | `ReplaySession.set_speed()` |
|  | `GET /api/replay/portfolio` |  | `ReplaySession.portfolio` プロパティ |
|  | `GET /api/replay/status` |  | `ReplaySession.status` プロパティ |
| **B. Order 制御** | `POST /api/order/submit` | order_api.rs (3490L) | Python `LiveSession.submit_order()` |
|  | `POST /api/order/modify` |  | `LiveSession.modify_order()` |
|  | `POST /api/order/cancel` |  | `LiveSession.cancel_order()` |
|  | `POST /api/order/cancel-all` |  | `LiveSession.cancel_all()` |
|  | `GET /api/order/list` |  | `LiveSession.orders` プロパティ |
| **C. Agent 通知** | `POST /api/agent/narrative` | agent_api.rs (323L) | Python `Session.narrate(...)` |
|  | `GET /api/agent/narrative` |  | `Session.narratives` |
| **D. Sidebar 操作** | `POST /api/sidebar/toggle-venue` | replay_api.rs | Iced `update()` ユニットテスト |
|  | `POST /api/sidebar/tachibana/request-login` |  | 同上 |
| **E. Test ユーティリティ** | `POST /api/test/tachibana/cancel-helper` | replay_api.rs | Iced `update()` ユニットテスト |
|  | `POST /api/test/tachibana/delete-session` |  | 同上 |

合計コード量: **約 6,756 行**（テストコード含む）。

### 2.2 E2E テスト（curl 駆動）

[tests/e2e/](../../tests/e2e/) 配下の bash スクリプト：

| ファイル | 用途 | 移行先 |
|---------|------|--------|
| `s55_mode_startup_smoke.sh` | mode 起動 smoke | pytest（標準維持） |
| `s56_replay_pane_autogen.sh` | replay 後 pane 自動生成 | Iced unit test |
| `s57_replay_buying_power_smoke.sh` | buying power 反映 | pytest |
| `s58_replay_load_smoke.sh` | replay 読み込み | pytest |
| `s80_order_crash_recovery_demo.sh` | crash recovery | pytest |
| `s80_order_submit_demo.sh` | 発注 smoke | pytest |
| `s81_order_modify_cancel_demo.sh` | modify/cancel | pytest |
| `s82_order_fill_ec_e2e.sh` | EC 受信 | pytest |
| `s83_ec_dedup_e2e.sh` | EC dedup | pytest |
| `s90_replay_user_flow.sh` | user flow 包括 | pytest |
| `tachibana_demo_login.sh` | login 経路 | pytest（HTTP 不使用） |
| `tachibana_relogin_after_cancel.sh` | 再ログイン | pytest |
| `smoke.sh` | 接続観測 | **そのまま維持**（GUI 起動が観測対象） |

### 2.3 起動スクリプト

| スクリプト | 役割 | 移行先 |
|----------|------|--------|
| [scripts/run-replay-debug.sh](../../scripts/run-replay-debug.sh) | build + GUI 起動 + HTTP 投入 | `python -m engine.replay run ...` で完結 |
| [scripts/replay_dev_load.sh](../../scripts/replay_dev_load.sh) | HTTP 投入の background loader | 削除（不要） |

---

## 3. 完成形アーキテクチャ

### 3.1 全体図

```
┌─────────────────────────────────────────────────────────┐
│ ユーザーコード（pytest / notebook / CLI / examples）     │
│                                                          │
│   from engine.replay import ReplaySession                │
│   s = ReplaySession()                                    │
│   s.load("1301.TSE", "2025-01-06", "2025-03-31")         │
│   for evt in s.run(strategy_file="..."):                 │
│       print(evt)                                         │
└────────────────────┬────────────────────────────────────┘
                     │ 直接 import（IPC 不在・HTTP 不在）
                     ▼
                NautilusRunner（既存）
                     ▲
                     │ WS IPC（GUI 起動時のみ）
┌────────────────────┴────────────────────────────────────┐
│ flowsurface (Iced GUI)                                   │
│   - HTTP API モジュール (replay_api / order_api /         │
│     agent_api) は削除                                     │
│   - 既存 WS IPC で engine と通信                          │
│   - Python が spawn する場合と attach する場合の両方を     │
│     維持                                                  │
└─────────────────────────────────────────────────────────┘
```

### 3.2 起動経路の対応表

| ユースケース | 旧 | 新 |
|------------|-----|-----|
| GUI で replay を見る | `cargo run -- --mode replay` + `curl POST /load` | `cargo run -- --mode replay`（GUI 内 UI で投入）または `python -m engine.replay run ...` を別プロセスで走らせ GUI を attach |
| backtest を回すだけ（GUI 不要） | （事実上不可能） | `python -m engine.replay run --strategy=... --instrument=... --start=... --end=...` |
| pytest から backtest を駆動 | （HTTP 経由で fragile） | `ReplaySession()` を直接 import |
| 発注（live モード） | GUI から手動、または `curl POST /api/order/submit` | GUI から手動（既存）。pytest からは `LiveSession.submit_order()` |

### 3.3 GUI と Python helper の関係

GUI が立っているとき helper を**ユーザーが同時に呼ぶことは想定しない**。GUI が engine を内部 spawn または attach し、内部 IPC で driving する経路は今と完全に同じ。helper class は「**GUI を起動しない**」起動経路を新設するもの。

これにより「pane-ready ack」のような GUI 整合性問題は構造的に消える：

- GUI なし: 待つペインがない → `ReplaySession.load()` は engine load 完了で即 return
- GUI あり: GUI 内で `ReplayDataLoaded` を直接受信して pane 生成 → 外部からの ack 待ち契約は不要

---

## 4. helper class API 設計（素案）

### 4.1 `engine.replay.ReplaySession`

```python
from typing import Iterator, Literal, Optional
from pathlib import Path
import threading

class ReplaySession:
    """Python 単独で nautilus replay backtest を駆動する helper.

    GUI を経由せずに NautilusRunner を呼び出す。HTTP API（旧 :9876）の
    機能等価物を in-process API として提供する。
    """

    def __init__(
        self,
        *,
        jquants_dir: Path | str | None = None,  # 既定: $JQUANTS_DIR or S:/j-quants
        log_level: str = "INFO",
    ) -> None: ...

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
        strategy_id: str = "user-strategy",
        initial_cash: int = 1_000_000,
        currency: str = "JPY",
        multiplier: int = 1,
        strategy_init_kwargs: dict | None = None,
    ) -> Iterator[dict]:
        """backtest を回しながら streaming event を yield する。

        on_event callback ではなく generator にして for ループで自然に書ける形にする。
        内部で NautilusRunner.start_backtest_replay_streaming() を呼び、
        on_event を queue に push、ジェネレータ側で pop する。
        """

    # ---- runtime control ----
    def set_speed(self, multiplier: int) -> None:
        """旧 POST /api/replay/control 相当。run() 中の生成スレッドから読まれる。"""

    def stop(self) -> None:
        """旧（HTTP 不在）相当。stop_event をセット → run() generator が終端に到達する。"""

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

### 4.2 `engine.live.LiveSession`（必要に応じて）

旧 `/api/order/*` 相当。Tachibana など実 venue 経由で発注する pytest helper。
**GUI なしで live 注文を出す経路は本番運用としてはサポートしない**（メモ参照: ユーザー戦略は自己責任方針）。
あくまで pytest からの E2E スモークテスト用。

```python
class LiveSession:
    def __init__(self, *, venue: Literal["tachibana"], demo: bool = True) -> None: ...
    def submit_order(self, ...) -> str: ...
    def modify_order(self, order_id: str, ...) -> None: ...
    def cancel_order(self, order_id: str) -> None: ...
    def cancel_all(self) -> None: ...
    @property
    def orders(self) -> list[dict]: ...
```

### 4.3 CLI: `python -m engine.replay run ...`

```bash
uv run python -m engine.replay run \
    --strategy docs/example/buy_and_hold.py \
    --instrument 1301.TSE \
    --start 2025-01-06 \
    --end 2025-03-31 \
    --granularity Daily \
    --initial-cash 1000000

# event stream を JSONL で stdout に書き出す（| jq でフィルタ可能）
```

---

## 5. 段階的移行プラン

### Phase 8.1 — helper class 新設（破壊的変更なし）

- [ ] `python/engine/replay/__init__.py` / `python/engine/replay/session.py` を新規作成
- [ ] `ReplaySession` を `NautilusRunner.start_backtest_replay_streaming` の薄いラッパーとして実装
- [ ] `python/engine/replay/__main__.py` で CLI（`python -m engine.replay run ...`）を提供
- [ ] pytest で helper class の golden path テストを追加（`python/tests/test_replay_session.py`）
- [ ] `docs/example/buy_and_hold.py` を helper 経由で動かすサンプルコマンドを README に追記
- [ ] **既存 HTTP API はそのまま残す**

**完了条件**: `uv run python -m engine.replay run ...` 1 コマンドで GUI なしの backtest が完走し、event stream が stdout に流れる。pytest 全 PASS。

### Phase 8.2 — E2E テスト pytest 移行

優先順序（破壊度の低い順）：

- [ ] `s58_replay_load_smoke.sh` → `python/tests/e2e/test_replay_load.py`
- [ ] `s57_replay_buying_power_smoke.sh` → `test_replay_buying_power.py`
- [ ] `s56_replay_pane_autogen.sh` → **Iced unit test**（`src/dashboard.rs` 内に `#[cfg(test)]`）
- [ ] `s90_replay_user_flow.sh` → `test_replay_user_flow.py`
- [ ] `s80_order_*` / `s81_*` / `s82_*` / `s83_*` → `python/tests/e2e/test_order_*.py`
- [ ] `tachibana_demo_login.sh` / `tachibana_relogin_after_cancel.sh` → `test_tachibana_login.py`
- [ ] **`s55_mode_startup_smoke.sh` / `smoke.sh` は維持**（GUI プロセス起動・観測が試験対象のため）

**完了条件**: 上記移行対象のシナリオが pytest で再現でき、CI で GREEN。bash 版は削除。

### Phase 8.3 — GUI 専用 endpoint の Iced unit test 化

旧 `/api/sidebar/*` / `/api/test/*` のシナリオを Iced `update()` 直接呼び出しのテストに移植：

- [ ] sidebar venue toggle のテスト（`Message::ToggleVenue` を直接 dispatch）
- [ ] tachibana request-login のテスト（`Message::RequestVenueLogin` を直接 dispatch）
- [ ] tachibana cancel-helper のテスト
- [ ] tachibana delete-session のテスト（debug build 限定）

**完了条件**: HTTP endpoint を呼んでいた E2E テストが Iced ユニットテストで等価カバレッジに達している。

### Phase 8.4 — Rust HTTP API 完全廃止

- [ ] `src/replay_api.rs` を削除（約 2,943 行）
- [ ] `src/api/order_api.rs` を削除（約 3,490 行）
- [ ] `src/api/agent_api.rs` を削除（約 323 行）
- [ ] `src/main.rs` から `replay_api::spawn` 呼び出しを除去
- [ ] `ControlApiCommand` enum と `replay_api_stream` Subscription を削除
- [ ] `ReplayApiState` / `OrderApiState` / `AgentApiState` を削除
- [ ] `scripts/run-replay-debug.sh` / `scripts/replay_dev_load.sh` を削除（または `python -m engine.replay run` ラッパーに書き換え）
- [ ] `.vscode/launch.json` の `replay - Rust: Debug (CodeLLDB)` 構成を削除（Python helper 用構成に置換）
- [ ] [CLAUDE.md](../../.claude/CLAUDE.md) の replay 関連セクションを書き直す
- [ ] `docs/wiki/replay.md` を helper ベースの記述に書き換え

**完了条件**:

- ポート 9876 を listen するプロセスが存在しない（`netstat -ano | grep 9876` で確認）
- `cargo build --release` が成功し、binary サイズが減っている
- `cargo test --workspace` 全 PASS
- `uv run pytest python/tests/` 全 PASS

---

## 6. テスト戦略

### 6.1 Phase 8.1 で追加するテスト

| ファイル | 内容 |
|---------|------|
| `python/tests/test_replay_session.py` | helper の load → run → portfolio の golden path |
| `python/tests/test_replay_session_cli.py` | `python -m engine.replay run ...` の subprocess 起動テスト |
| `python/tests/test_replay_session_stop.py` | run() の途中で stop() を呼ぶ → generator が終端に到達する |
| `python/tests/test_replay_session_speed.py` | run() 中に set_speed() で multiplier が反映される |

### 6.2 Phase 8.2 / 8.3 の移行ガイドライン

- bash + curl の I/O 検証 → pytest の `assert helper.xxx == ...` に置換
- bash の sleep / polling → pytest の `tenacity` retry または event 駆動 wait
- HTTP status code 検証 → 例外 type 検証（`pytest.raises(FileNotFoundError)` 等）

### 6.3 リグレッション保護

- `python/tests/test_no_http_listener.py` を追加し、Phase 8.4 完了後に **ポート 9876 で listen していないこと**を assert する
- `cargo test` 側に `tests/no_replay_api_module.rs` を追加し、`replay_api` symbol が存在しないことを compile error 化する（モジュール削除のリグレッションガード）

---

## 7. リスクと未決事項

### 7.1 リスク

| # | リスク | 影響 | 軽減策 |
|---|------|------|--------|
| R1 | E2E テスト移行漏れ | カバレッジ低下 | Phase 8.4 着手前に bash 版と pytest 版を並行で 1 リリースサイクル流して挙動差異を観測 |
| R2 | Iced unit test の表現力不足（`update()` 直叩きでカバーしきれない GUI シナリオ） | 一部テスト消失 | iced の `application::View` レベル test harness 採用を Phase 8.3 で再評価。最悪の場合 `/api/sidebar/*` だけ HTTP として残す段階妥協を許容 |
| R3 | helper class の API 設計が早期に固まらない | 利用側の手戻り | Phase 8.1 では `examples/` を 2〜3 本書き、API を実利用で固めてから Phase 8.2 に進む |
| R4 | NautilusRunner が in-process で 2 回呼ばれる場合のリソース二重確保 | helper の並列利用で fail | `ReplaySession` を `__enter__` / `__exit__` を持つ contextmanager にし、明示 lifecycle を要求 |
| R5 | 既存 user の bash スクリプト破壊 | 外部影響 | Phase 8.1 〜 8.3 の間は HTTP API を残し、deprecated warning を出す。Phase 8.4 でメジャーバージョン bump |

### 7.2 未決事項

| # | 質問 | 案 |
|---|------|-----|
| Q1 | `LiveSession`（旧 `/api/order/*`）は本当に作るか？ pytest 専用なら mock 経由で十分かもしれない | A: pytest 専用 helper として作る / B: mock 経由で代替して helper は作らない |
| Q2 | `/api/agent/narrative` は Python 単独モードで意味があるか？ | A: 残す（user code が narrate を呼べる） / B: 廃止 |
| Q3 | `python -m engine.replay run ...` の event 出力フォーマットは JSONL でよいか？ | 既定 JSONL。`--format=table` で human-readable も用意するか別途検討 |
| Q4 | GUI と helper を同時起動するユースケース（GUI が attach してきて helper が driving） | 非サポートで合意したい。GUI 起動中は GUI が driving、helper は GUI を起動しないモード専用 |
| Q5 | Phase 8.4 で削除する 6,750 行の中に「単独で価値のあるユーティリティコード」があれば抽出するか | replay_api 内の HTTP raw parser は再利用価値ゼロ。order_api 内の重複検知ロジックは Python 側に移植する候補があれば移す |

---

## 8. 完了後の状態（Definition of Done）

Phase 8 シリーズ完了時点で：

1. ポート 9876 を listen しているプロセスが**存在しない**
2. `python -m engine.replay run ...` で GUI 起動なしに backtest が完走する
3. `tests/e2e/*.sh` は `s55_mode_startup_smoke.sh` と `smoke.sh` を残して全削除
4. Rust 側 HTTP API モジュール 3 ファイル（合計 約 6,756 行）が削除されている
5. memory に記録された **「Python 単独でも動くか？」判断軸が満たされている**
6. CLAUDE.md / README / docs/wiki の replay セクションが helper ベースに書き換わっている

---

## 9. 関連ドキュメント

- [spec.md](./spec.md) — Rust ↔ Python 境界仕様
- [archive/refactor-rust-python-boundary-2026-05-01.md](./archive/refactor-rust-python-boundary-2026-05-01.md) — depth/price 正規化の責務移動（別案件）
- [implementation-plan.md](./implementation-plan.md) — フェーズ 0〜7 の実装計画
- [memory: project_python_only_mode.md](file://~/.claude/projects/c--Users-sasai-Documents-e-station/memory/project_python_only_mode.md)
- [memory: project_no_bundled_ai.md](file://~/.claude/projects/c--Users-sasai-Documents-e-station/memory/project_no_bundled_ai.md)
- [src/replay_api.rs](../../src/replay_api.rs) — 廃止対象 (2,943 L)
- [src/api/order_api.rs](../../src/api/order_api.rs) — 廃止対象 (3,490 L)
- [src/api/agent_api.rs](../../src/api/agent_api.rs) — 廃止対象 (323 L)
- [python/engine/nautilus/engine_runner.py](../../python/engine/nautilus/engine_runner.py) — helper の被ラップ対象

# kabuステーション venue 統合: 実装計画

## フェーズ一覧

| Phase | 内容 | 状態 |
| :--- | :--- | :--- |
| Phase 0 | 計画フェーズ（本文書群） | 🔵 完了 |
| Phase 1 | リードオンリー統合（検証環境のみ）| ✅ 完了 |
| Phase 2 | 発注（検証環境のみ） | ✅ 完了 |
| Phase 3 | 先物・OP・市場細分化 | ✅ 完了 |
| Phase 4 | 本番接続 | 🔄 実装完了 (review-fix-loop 待ち) |

## Phase 1 タスク詳細

### K1 — Rust enum 拡張

**内容**: `Venue::KabuStation` / `Exchange::KabuStationStock` バリアント追加、
`Venue::from_str` に `"kabu_station"` を受理、`AdapterHandles.kabu_station` フィールド追加。

**完了条件**:
- `cargo check --workspace` 通過
- enum 網羅 match 全箇所が compile
- `test_kabusapi_capabilities.py::test_capabilities_max_push_symbols_matches_register_set` pass

**関連ファイル**:
- `exchange/src/adapter.rs`
- `exchange/src/adapter/client.rs`
- `engine-client/src/capabilities.rs`
- `python/engine/schemas.py`（SCHEMA_MINOR bump + `"kabu_station"` を `venue` フィールドで許可）

**状態**: ✅ 完了

---

### K2 — Python ベースモジュール

**内容**: `kabusapi_url.py` / `kabusapi_codec.py` / `kabusapi_auth.py` 着地 + 単体テスト。

**完了条件**:
- `pytest python/tests/test_kabusapi_auth.py` グリーン
- `Code=4001005` および `Code=4001001` の両方から `KabuTokenExpiredError` を assert
- `test_kabusapi_auth_logging.py` で caplog に token / API パスワード / 取引パスワード 3 種が出力されないことを assert
- `test_kabusapi_codec.py` で UTF-8 JSON encode/decode + SJIS バイト混入で `UnicodeDecodeError`

**関連ファイル**:
- `python/engine/exchanges/kabusapi_url.py`（新規）
- `python/engine/exchanges/kabusapi_codec.py`（新規）
- `python/engine/exchanges/kabusapi_auth.py`（新規）
- `python/tests/test_kabusapi_auth.py`（新規）
- `python/tests/test_kabusapi_auth_logging.py`（新規）
- `python/tests/test_kabusapi_codec.py`（新規）

**状態**: ✅ 完了

---

### K3 — ログインフロー + tkinter ダイアログ

**内容**: `kabusapi_login_flow.py` / `kabusapi_login_dialog.py` 着地。
debug env 自動ログイン経路。本体プロセス落ち（TCP refused）の検知・復帰。

**完了条件**:
- `test_kabusapi_login_flow.py::test_tcp_refused_three_retries_then_local_app_down` pass（5s × 3 回後の `VenueError{code:"local_app_down"}`）
- `DEV_KABU_API_PASSWORD` 設定時に tkinter ダイアログを spawn しないことを assert
- 早朝時刻帯分岐の test（INFO ログ扱い）

**関連ファイル**:
- `python/engine/exchanges/kabusapi_login_flow.py`（新規）
- `python/engine/exchanges/kabusapi_login_dialog.py`（新規）
- `python/tests/test_kabusapi_login_flow.py`（新規）

**依存**: K2 完了後

**状態**: ✅ 完了

---

### K4 — 流量制限 + PUSH 銘柄登録

**内容**: `kabusapi_ratelimit.py` / `kabusapi_register.py` 着地 + 単体テスト。

**完了条件**:
- `test_kabusapi_register.py::test_register_51st_raises_full` pass
- `test_kabusapi_register.py::test_lru_evict_emits_subscription_evicted` pass
- `test_kabusapi_ratelimit.py::test_order_bucket_blocks_at_6th_req` pass
- `test_kabusapi_capabilities.py::test_capabilities_max_push_symbols_matches_register_set`（K1 と共有）pass

**関連ファイル**:
- `python/engine/exchanges/kabusapi_ratelimit.py`（新規）
- `python/engine/exchanges/kabusapi_register.py`（新規）
- `python/tests/test_kabusapi_ratelimit.py`（新規）
- `python/tests/test_kabusapi_register.py`（新規）
- `python/tests/test_kabusapi_capabilities.py`（新規）

**依存**: K2 完了後（K3 と並列可）

**状態**: ✅ 完了

---

### K5 — WebSocket 接続・板パース

**内容**: `kabusapi_ws.py` 着地。WebSocket 接続・`PushBoardSuccess` パース・`DepthSnapshot` IPC 送出。
再接続後は RegisterSet 全件 re-register。5s × 5 回連続失敗で打ち切り。

**完了条件**:
- `test_kabusapi_ws.py::test_reconnect_reregisters_all_symbols` pass
- `test_kabusapi_ws.py::test_decode_rejects_sjis_bytes` pass
- `test_kabusapi_ws.py::test_reconnect_aborts_after_5_consecutive_failures` pass

**関連ファイル**:
- `python/engine/exchanges/kabusapi_ws.py`（新規）
- `python/tests/test_kabusapi_ws.py`（新規）

**依存**: K4 完了後

**状態**: ✅ 完了

---

### K6 — REST 読取

**内容**: `kabusapi_rest.py` 着地（読取のみ）。`/board` / `/symbol` / `/orders` / `/positions` / `/wallet/*`。
`fetch_board()` で `RegisterSet.touch()` を必ず呼ぶ。

**完了条件**:
- `test_kabusapi_rest.py::test_fetch_board_touches_register_set` pass
- 満杯時の `KabuRegisterFullError` を assert

**関連ファイル**:
- `python/engine/exchanges/kabusapi_rest.py`（新規）
- `python/tests/test_kabusapi_rest.py`（新規）

**依存**: K4 完了後（K5 と並列可）

**状態**: ✅ 完了

---

### K7 — IPC E2E + ファサード

**内容**: `kabusapi.py`（KabuStationVenue ファサード）着地。
`python/engine/__main__.py` に kabu venue 起動分岐を追加。
`python/engine/replay_session.py` の `LiveSession.login()` を `venue` 引数追加で拡張。

**完了条件**:
- `test_live_session_kabu.py::test_login_kabu_station_emits_venue_ready` pass
  （`LiveSession.login(venue="kabu_station")` が `VenueReady{venue:"kabu_station"}` を受信）

**関連ファイル**:
- `python/engine/exchanges/kabusapi.py`（新規）
- `python/engine/__main__.py`（kabu 分岐追加）
- `python/engine/replay_session.py`（`login()` シグネチャ拡張）
- `python/tests/test_live_session_kabu.py`（新規）

**依存**: K5 / K6 完了後

**状態**: ✅ 完了

---

### K8 — URL リテラル lint

**内容**: URL リテラル lint タスク。検査対象を `src/` / `exchange/` / `engine-client/` / `python/engine/`
（`python/engine/exchanges/kabusapi_url.py` を除外）に拡大。CI で zero-match を assert。

**完了条件**:
- CI 上で除外対象 1 ファイル以外で 1 件でも検出されたら fail
- 正規表現: `(http|ws)://localhost:1808[01]|/kabusapi/(websocket|register|sendorder|board)`

**関連ファイル**:
- `.github/workflows/kabu-mock.yml`（新規 or 既存に追加）
- Makefile / CI スクリプト（lint step 追加）

**依存**: K2 完了後

**状態**: ✅ 完了

---

### K8.5 — CI ジョブ追加

**内容**: `pytest -m demo_kabu` ジョブを `.github/workflows/kabu-mock.yml` に追加。

**完了条件**:
- job 名: `pytest-kabu-mock`
- コマンド: `pytest -m demo_kabu python/tests/test_kabusapi_*.py`
- CI グリーン（HTTPXMock のみ、本物 API は叩かない）

**関連ファイル**:
- `.github/workflows/kabu-mock.yml`（新規）

**依存**: K2〜K7 完了後

**状態**: ✅ 完了

---

## Phase 2 タスク詳細

### P2-1 — KabuTradePasswordHolder

**内容**: `kabusapi_auth.py` に `KabuTradePasswordHolder` クラスを追加。
`TachibanaSessionHolder` と同じ設計（idle forget / lockout / ログマスク）。

**追加エラー型**: `KabuTradeCancelledError` / `KabuTradeLockedOutError`

**状態**: ✅ 完了

---

### P2-2 — kabusapi_trade_dialog.py

**内容**: 取引パスワード収集 tkinter subprocess。stdout に `{"status":"ok","trade_password":"..."}` を返す。

**状態**: ✅ 完了

---

### P2-3 — kabusapi_orders.py

**内容**: `KabuOrderClient` — sendorder / cancelorder / poll_fills。

**設計ポイント**:
- `send_order()`: POST /sendorder、`async with order_bucket:`
- `cancel_order()`: PUT /cancelorder、同じ order_bucket
- `poll_fills()`: GET /orders、State=5 のみフィルタ
- `_ensure_trade_password()`: 未保持時はダイアログ or `DEV_KABU_TRADE_PASSWORD` env
- 買い Side = **"2"**（立花の "3" と混同禁止 — data-mapping.md §8）
- 取引パスワードはリクエストボディにのみ使用、ログ出力禁止

**状態**: ✅ 完了

---

### P2-4 — KabuStationVenue facade 拡張

**内容**: `kabusapi.py` に `send_order()` / `cancel_order()` / `poll_fills()` 追加。
`KabuOrderClient` を遅延構築（token 取得後に初期化、再ログイン時に invalidate）。

**状態**: ✅ 完了

---

### P2-5 — kabusapi_rest.py クリーンアップ

**内容**: Phase 2 制限コメント削除・未使用 import `KabuRegisterFullError` 削除。

**状態**: ✅ 完了

---

### Phase 2 検証結果 (2026-05-07)

```
uv run pytest python/tests/test_kabusapi_orders.py -v   → 12 passed
uv run pytest -m demo_kabu python/tests/test_kabusapi_*.py → 57 passed
uv run pytest python/tests/ -q --tb=no                  → 2029 passed, 5 skipped
```

**新規ファイル**:
- `python/engine/exchanges/kabusapi_orders.py`
- `python/engine/exchanges/kabusapi_trade_dialog.py`
- `python/tests/test_kabusapi_orders.py` (12 tests)

**修正ファイル**:
- `python/engine/exchanges/kabusapi_auth.py` — KabuTradePasswordHolder + 2 エラー型
- `python/engine/exchanges/kabusapi.py` — send_order / cancel_order / poll_fills
- `python/engine/exchanges/kabusapi_rest.py` — コメント・import クリーンアップ

---

## タスクグラフ（依存関係）

```
K1 (Rust enum 拡張)
    └──→ K2 (Python ベースモジュール)
              ├──→ K3 (ログインフロー)
              ├──→ K4 (ratelimit + register)
              │         └──→ K5 (WebSocket)
              │         └──→ K6 (REST 読取)
              │                   └──→ K7 (IPC E2E)
              └──→ K8 (URL lint)
K8.5 (CI): K2〜K7 完了後
```

並列実行フェーズ:
- **Phase A（直列）**: K1
- **Phase B（並列可）**: K2
- **Phase C（並列可）**: K3 + K4（B 完了後）
- **Phase D（並列可）**: K5 + K6（C 完了後）
- **Phase E（直列）**: K7（D 完了後）
- **Phase F（直列）**: K8 + K8.5（全完了後）

## テスト戦略

- Rust: `cargo test --workspace`（enum 網羅 match / schema_minor_kabu）
- Python: `uv run pytest python/tests/test_kabusapi_*.py python/tests/test_live_session_kabu.py`
- モック: `pytest-httpx` (HTTPXMock) + WebSocket mock

---

## R8 レビュー反映 (2026-05-07, Phase 2 R1)

> **連番方針メモ**: R1〜R7 は Phase 1 のラウンド連番。R8 以降は Phase 2 のラウンド連番として再起動している（R8 = Phase 2 R1, R9 = Phase 2 R2）。混乱を避けるため見出しに Phase ラベルを併記する。


### HIGH-1 (R1-new): `_spawn_trade_dialog()` タイムアウト追加
`kabusapi_orders.py` の `_spawn_trade_dialog()` に `asyncio.wait_for(..., timeout=120.0)` を追加。タイムアウト時は `proc.kill()` + `KabuTradeCancelledError` を raise。

### HIGH-2: `KabuRateLimitError` に専用 except ブランチ + RATE_LIMITED reason_code
`server.py` の `_do_submit_order_kabu()` / `_do_cancel_order_kabu()` に `except KabuRateLimitError` ブランチを追加。`OrderRejected{RATE_LIMITED}` を emit。`kabusapi_auth` import に `KabuRateLimitError` 追加。

### HIGH-3: `send_order_future()` / `send_order_option()` の `KabuTradePasswordHolder` 操作を除去
先物・OP は `"Password"` フィールドを body に含まないため `_holder.on_invalid()` / `on_submit_success()` は不要。両メソッドから除去。

### HIGH-4: `KabuStationVenue.is_trade_locked_out()` プロパティを公開
`kabusapi.py` に `is_trade_locked_out()` を追加。`server.py` の `_trade_password_holder.is_locked_out()` 直接参照 2 箇所を `is_trade_locked_out()` に置換。

### MEDIUM-1: `test_kabu_server_orders.py` 修正 + テスト 3 件追加
- `_make_server()` に `_connected_venue = "kabu_station"` を追加
- 既存テストの `mock_venue._trade_password_holder.is_locked_out` を `mock_venue.is_trade_locked_out` に置換
- `test_server_submit_order_token_expired_clears_session` 追加（FSM リセット確認）
- `test_server_submit_order_connection_error_clears_session` 追加（FSM リセット確認）
- `test_server_submit_order_rate_limited_emits_rate_limited_reason_code` 追加

### MEDIUM-2: `httpx.AsyncClient()` に `timeout=30.0` を追加
`kabusapi_orders.py` の全 `httpx.AsyncClient()` 生成箇所（5 箇所）に `timeout=30.0` を追加。

### MEDIUM-3: `on_invalid()` が `_last_use_time` をリセットしない
`kabusapi_auth.py` の `KabuTradePasswordHolder.on_invalid()` に `self._last_use_time = None` を追加。lockout 後の状態が "idle forget" と区別可能になる。

### MEDIUM-4: 先物・OP `instrument_id` の防御フェンスを追加
`server.py` の `_do_submit_order_kabu()` に instrument_id チェックを追加。`"Future"` / `"Option"` を含む場合 `OrderRejected{UNSUPPORTED_INSTRUMENT}` を emit して早期リターン。

### MEDIUM-5: `kabu-mock.yml` に `test_invariant_reason_code.py` を追加
CI pytest コマンドに `python/tests/test_invariant_reason_code.py` を追加。

### MEDIUM-6: `_side_to_kabu` の引数を `Literal["buy", "sell"]` に強化
`kabusapi_orders.py` に `SideStr = Literal["buy", "sell"]` 型エイリアスを追加し `_side_to_kabu` の引数型を強化。

### 検証結果 (R1-new)
```
cargo fmt --check  → OK（変更なし）
cargo check --workspace  → Finished dev profile
uv run pytest python/tests/test_kabusapi_orders.py python/tests/test_kabu_server_orders.py python/tests/test_invariant_reason_code.py python/tests/test_live_session_kabu.py python/tests/test_request_venue_login_state.py python/tests/test_kabusapi_auth.py -v  → 54 passed
uv run pytest python/tests/ -q --tb=short  → 2061 passed, 5 skipped (pre-existing test_schema_minor_is_9 failure は対象外)
```
- CI: `.github/workflows/kabu-mock.yml` / `pytest -m demo_kabu`
- 本物 kabuステーション: Windows 環境のみ、CI 不可

---

## R9 レビュー反映 (2026-05-07, Phase 2 R2)

R8 (Phase 2 R1) サニティチェック後の追加レビュー指摘を TDD で修正。

### HIGH-1 (R2): `_spawn_trade_dialog()` タイムアウト時 `proc.wait()` 不在
`proc.kill()` 後にプロセス終了待ち `await proc.wait()` を追加。zombie プロセス回避。

### HIGH-2 (R2): `test_invariant_reason_code.py` に `@pytest.mark.demo_kabu` 未付与
全 3 テスト関数 (`test_canonical_codes_are_screaming_snake_case` / `test_all_reason_codes_in_source_are_canonical` / `test_all_reason_codes_in_source_are_screaming_snake_case`) に `@pytest.mark.demo_kabu` を付与。CI フィルタとの整合を確保。

### HIGH-3 (R2): canonical reason_code 一覧に `UNSUPPORTED_INSTRUMENT` 未登録
- `docs/✅order/spec.md §5.2` に `UNSUPPORTED_INSTRUMENT` 行を追加（HTTP 旧 400, Phase 2 防御フェンス）
- `docs/✅python-data-engine/schemas/events.json` の `OrderRejected.reason_code` Known values に `UNSUPPORTED_INSTRUMENT` を追加

### MEDIUM-1 (R2): R1 新規 3 テストに `mock_venue.clear` assert 不在
- `test_server_submit_order_token_expired_clears_session` / `test_server_submit_order_connection_error_clears_session` に `mock_venue.clear.assert_called_once()` を追加
- `test_server_submit_order_rate_limited_emits_rate_limited_reason_code` には `mock_venue.clear.assert_not_called()` を追加（負の不変条件 pin）

### MEDIUM-2 (R2): `_do_cancel_order_kabu` の対応テスト 3 件追加
- `test_server_cancel_order_token_expired_clears_session`
- `test_server_cancel_order_connection_error_clears_session`
- `test_server_cancel_order_rate_limited_emits_rate_limited_reason_code`

### MEDIUM-3 (R2): `KabuStationVenue.is_trade_locked_out()` 単体テスト追加
`test_kabusapi_auth.py` に lockout 状態 (3 連続 invalid) と非 lockout 状態の 2 ケースを追加。

### MEDIUM-4 (R2): `on_invalid()` の `_last_use_time = None` pin テスト追加
`test_on_invalid_clears_last_use_time` で R8 M-3 後退防止を保証。

### MEDIUM-5 (R2): `CANONICAL_REASON_CODES` の `RATE_LIMITED` 重複削除
`test_invariant_reason_code.py` の後方 `RATE_LIMITED` (line 54 付近) を削除し、kabu_station 経路でも前方の元エントリを共有することをコメントで明記。

### MEDIUM-6 (R2): `fetch_token` の `httpx.AsyncClient()` に `timeout=30.0` 追加
`kabusapi_auth.py` の `fetch_token()` に明示タイムアウトを追加。`kabusapi_orders.py` 各メソッドとの対称性確保。

### MEDIUM-7 (R2): Future/Option フェンスを suffix match に変更
`server.py` の `_do_submit_order_kabu()` の Future/Option フェンスを `"Future" in instrument_id` から `instrument_id.endswith(".KabuStation Future") or .endswith(".KabuStation Option")` に変更。symbol 名に "Future"/"Option" 文字列が混入した場合の誤判定を防止。

### MEDIUM-8 (R2): 計画書 R8 ブロック見出し整理
本ブロック (R9) の追加に合わせ、R8 見出しに `Phase 2 R1` ラベルを付与し、文書冒頭に連番方針メモを追加。

### 検証結果 (R2)
```
cargo fmt --check  → OK
cargo check --workspace  → Finished dev profile (0 errors)
uv run pytest python/tests/test_kabu_server_orders.py python/tests/test_kabusapi_auth.py python/tests/test_kabusapi_orders.py python/tests/test_invariant_reason_code.py -v  → 全件 pass
uv run pytest python/tests/ -q --tb=short  → 全件 pass
```

---

## R1 レビュー反映（2026-05-07）

Phase 1 実装後の R1 レビュー指摘 4 件を TDD で修正した。

### HIGH-1 修正: kabu_station capabilities が Ready handshake に広告されない

**原因**: `_build_ready()` が `_workers.keys()` のみから venue_caps を組み立てており、
`kabu_station` は `_workers` に含まれないため capabilities に現れなかった。

**修正**:
- `server.py` の venue_caps 組立ブロック後に `kabu_station` capabilities を直接追記
- `supported_venues` に `"kabu_station"` を追加 (`list(self._workers.keys()) + ["kabu_station"]`)
- `architecture.md §8` の JSON に一致する 4 フィールドを設定
- `RegisterSet.MAX` を参照して `max_push_symbols` と `RegisterSet.MAX` の一致を保証

**追加テスト**:
- `test_request_venue_login_state.py::test_kabu_ready_capabilities_include_kabu_station`

---

### HIGH-2 修正: ログインキャンセルが VenueError{local_app_down} に化ける

**原因**: `kabusapi_login_flow.py` がキャンセル時に `KabuConnectionError` を raise し、
`server.py` の `except KabuConnectionError` が一律 `VenueError{local_app_down}` に変換していた。

**修正**:
- `kabusapi_auth.py` に `KabuLoginCancelledError(KabuApiError)` を追加
- `kabusapi_login_flow.py:73` を `KabuLoginCancelledError` に変更
- `server.py` の `_startup_kabu_station()` に `except KabuConnectionError` の前に
  `except KabuLoginCancelledError` を追加 → `VenueLoginCancelled` を emit して return
- `server.py` の import に `KabuLoginCancelledError` を追加

**追加テスト**:
- `test_kabusapi_login_flow.py::test_dialog_cancel_raises_kabu_login_cancelled_error`
- `test_kabusapi_login_flow.py::test_dialog_cancel_is_not_kabu_connection_error`
- `test_request_venue_login_state.py::test_startup_kabu_station_cancel_emits_venue_login_cancelled`

---

### MEDIUM-3 修正: CONNECTED 状態で kabu 切替時の tachibana 後始末が不完全

**原因**: kabu の CONNECTED → re-login 分岐で `_kabu_venue.clear()` だけ実施し、
tachibana の `_event_task` / `_tachibana_session` / worker session のクリアを行っていなかった。

**修正**:
- `server.py:3158` の kabu CONNECTED 分岐に、`_connected_venue == "tachibana"` の場合の
  後始末を追加: `_event_task.cancel()` / `_tachibana_session = None` /
  `_workers["tachibana"].set_session(None)` / `tachibana_clear_session(self._cache_dir)`
- `_connected_venue = None` を追加

**追加テスト**:
- `test_request_venue_login_state.py::test_kabu_relogin_from_tachibana_connected_cancels_event_task`

---

### MEDIUM-4 修正: LiveSession.login() に venue 引数がない

**原因**: `replay_session.py:1407` の `login()` シグネチャに `venue` 引数がなく、
`implementation-plan.md:144` の `login(venue="kabu_station")` 完了条件を満たせない。

**修正**:
- `replay_session.py:1407` に `venue: str | None = None` を追加
- `venue is not None` の場合は `self._venue` を上書きする（明示引数で venue を選択可能に）
- `test_live_session_kabu.py:121` を `s.login(venue="kabu_station")` に変更

**追加テスト**:
- `test_live_session_kabu.py::test_login_venue_fallback_from_init`（venue 省略時のフォールバック）
- `test_live_session_kabu.py::test_login_explicit_venue_overrides_init_venue`（明示引数で上書き）

---

### 検証結果

```
uv run pytest python/tests/test_live_session_kabu.py python/tests/test_kabusapi_login_flow.py python/tests/test_request_venue_login_state.py -v
16 passed in 11.57s

uv run pytest python/tests/ -q --tb=no
2015 passed, 5 skipped in 218.88s
```

---

## R2 レビュー反映 (2026-05-07, ラウンド 2)

### HIGH-A: `_login_attach()` が `VenueLoginCancelled` を無視して 30 秒ハング

**問題**: `_login_attach()` のイベントループが `VenueLoginCancelled` を無視し、30 秒 timeout まで待機していた。

**修正**: `python/engine/replay_session.py` の `_login_attach()` に `VenueLoginCancelled` ハンドリングを追加。
受信時に即座に `ConnectionError("LiveSession.login: login cancelled by user ...")` を raise する。

**追加テスト**: `python/tests/test_live_session_kabu.py::test_login_kabu_station_raises_on_venue_login_cancelled`
- `VenueLoginCancelled` 受信時に `ConnectionError` が raise されること
- 30 秒待機せず 5 秒以内に raise されること

---

### HIGH-B: `test_tcp_refused_three_retries_then_local_app_down` が CI でハング

**問題**: `dev_login_allowed=True` を渡しているが `DEV_KABU_API_PASSWORD` を設定していないため、`_spawn_dialog()` が呼ばれ CI でハングする。

**修正**: `python/tests/test_kabusapi_login_flow.py` の `test_tcp_refused_three_retries_then_local_app_down` に `monkeypatch` パラメータと `monkeypatch.setenv("DEV_KABU_API_PASSWORD", "test_pass")` を追加。

---

### MEDIUM-A: R1 追加テスト 3 件の `@pytest.mark.demo_kabu` 未付与 + CI 未登録

**問題**: R1 追加テスト 3 件に `@pytest.mark.demo_kabu` マーカーがなく、CI の `kabu-mock.yml` にも `test_request_venue_login_state.py` が含まれていなかった。

**修正**:
1. `python/tests/test_request_venue_login_state.py` に `import pytest` を追加し、以下の 3 関数に `@pytest.mark.demo_kabu` を付与:
   - `test_startup_kabu_station_cancel_emits_venue_login_cancelled`
   - `test_kabu_ready_capabilities_include_kabu_station`
   - `test_kabu_relogin_from_tachibana_connected_cancels_event_task`
2. `.github/workflows/kabu-mock.yml` の pytest コマンドに `python/tests/test_request_venue_login_state.py` を追加。

---

### MEDIUM-B: `venue` 上書きが `_logged_in` no-op ガードより前に実行される

**問題**: `login(venue="other")` 呼び出し時、`_logged_in=True` の no-op パスでも `self._venue` が永続変更されていた（M-GP5 違反）。

**修正**: `python/engine/replay_session.py` の `login()` で `self._venue = venue` の上書きを `if self._logged_in: ... return` ガードの **後**（実際の login 処理の直前）に移動。

**追加テスト**: `python/tests/test_live_session_kabu.py::test_venue_not_overridden_when_already_logged_in`
- `_logged_in=True` の状態で `login(venue="tachibana")` を呼んでも `self._venue` が `"kabu_station"` のまま

---

### MEDIUM-C: `_kabu_startup_task` が完了後 None リセットされない

**問題**: `_startup_kabu_station()` が完了しても `self._kabu_startup_task` が done タスクへの stale 参照を保持し続けていた。

**修正**: `python/engine/server.py` の `_startup_kabu_station()` を `try/finally` でラップし、`finally` ブロックで `self._kabu_startup_task = None` を実行。成功・キャンセル・エラー全パスでリセットされる。

---

### 検証結果 (R2)

```
uv run pytest python/tests/test_live_session_kabu.py python/tests/test_kabusapi_login_flow.py python/tests/test_request_venue_login_state.py -v
18 passed in 1.28s
```

---

## R3 レビュー反映 (2026-05-07, ラウンド 3)

### MEDIUM-1（R3 サニティ）: `_login_attach()` VenueLoginCancelled の `is None` dead code 除去

**問題**: R2 で追加した `_login_attach()` の `VenueLoginCancelled` ハンドラが
`evt_request_id == request_id or evt_request_id is None` を使っていた。
`is None` アームは server.py が VenueLoginCancelled に必ず request_id を付けるため
到達しない dead code であり、将来 broadcast で request_id=None の cancel が来た際に
別 client のイベントを誤検知するリスクがあった。

**修正**: `python/engine/replay_session.py` の VenueLoginCancelled フィルタから
`or evt_request_id is None` を削除し、`evt_request_id == request_id` のみに変更。
コメントに server.py 側の invariant（必ず request_id 付き）を明記。

**追加テスト**: `python/tests/test_live_session_kabu.py::test_login_cancelled_with_wrong_request_id_is_ignored`
- wrong request_id の VenueLoginCancelled が来ても ConnectionError が raise されない
- その後の正しい VenueReady でログインが成功する

### 検証結果 (R3)

```
uv run pytest python/tests/test_live_session_kabu.py python/tests/test_kabusapi_login_flow.py python/tests/test_request_venue_login_state.py -q
19 passed in 1.22s
```

---

## R4 レビュー反映 (2026-05-07, ラウンド 4)

### HIGH-1 (R4): `KabuTokenExpiredError` が `KabuApiError` に吸収される + `"no token"` 文字列チェック

**問題**: `_do_submit_order_kabu` / `_do_cancel_order_kabu` の except チェーンで
`KabuApiError` が先行し、そのサブクラス `KabuTokenExpiredError` が捕捉されなかった。
さらに `reason_code` の切り替えに `"no token" in str(exc)` という壊れたパターンを使っており、
kabu API が日本語メッセージを返す場合に NOT_LOGGED_IN が発行されなかった。

**修正**:
- `except KabuTokenExpiredError` を `except KabuApiError` より前に追加（両メソッド）
- 文字列チェックを完全削除し、`reason_code: "NOT_LOGGED_IN"` に固定
- `log.warning` 追加
- `KabuTokenExpiredError` 発生時に `self._kabu_venue.clear()` を呼び、
  続く発注がすべて NOT_LOGGED_IN になる状態を防止

### MEDIUM-1 (R4): `_spawn_trade_dialog` の `json.loads` が `JSONDecodeError` を無言で伝播

**修正**: `json.loads` を `try/except json.JSONDecodeError → KabuTradeCancelledError` に包み、
エラーログを追加。

### MEDIUM-2 (R4): `result["trade_password"]` の `KeyError` が無言で伝播

**修正**: `result.get("trade_password")` + 空チェック + `KabuTradeCancelledError` に変更。

### MEDIUM-3 (R4): `resp.json()` が `httpx.DecodingError` / `json.JSONDecodeError` を伝播

**修正**: `send_order` / `cancel_order` / `poll_fills` の `resp.json()` を
`try/except Exception → log.error + KabuApiError` に包み、HTTP status と body[:200] を記録。

### 検証結果 (R4)

```
uv run pytest python/tests/ -q --tb=no
2044 passed, 5 skipped, 8 warnings in 195.06s
```

---

## R5 レビュー反映 (2026-05-07, ラウンド 5)

### MEDIUM-1 (R5): `fetch_token` の `resp.json()` が非 JSON 応答で無言に失敗

**問題**: `kabusapi_auth.py:fetch_token` の `resp.json()` に try/except がなく、
kabuStation がメンテナンス中などに HTML を返した場合に `JSONDecodeError` が
ログ記録なしで呼び出し元に伝播した。

**修正**: `try/except Exception → KabuConnectionError` に包み、HTTP status + body[:200] をログ。

### MEDIUM-2 (R5): `fetch_token` の `body["Token"]` が `KeyError` で無言に失敗

**修正**: `body.get("Token") or ""` + 空チェック + `log.error + KabuConnectionError`。

### MEDIUM-3 (R5): `KabuTokenExpiredError` でトークン状態がクリアされない

**問題**: R4 で `except KabuTokenExpiredError` を追加したが `self._kabu_venue.clear()` を
呼ばなかったため、切れたトークンが保持され続け、以降の全発注が NOT_LOGGED_IN で
連続拒否される可能性があった（tachibana の `SessionExpiredError` arm との対称性の欠如）。

**修正**: 両メソッドの `except KabuTokenExpiredError` arm に `self._kabu_venue.clear()` を追加。

### 検証結果 (R5)

```
uv run pytest python/tests/test_kabusapi_orders.py python/tests/test_kabu_server_orders.py python/tests/test_invariant_reason_code.py -q
31 passed in 2.70s
```

---

## R6 レビュー反映 (2026-05-07, ラウンド 6)

### MEDIUM-1 (R6): `OrderSubmitted` と `OrderRejected` の dual-event プロトコル違反

**問題**: `_do_submit_order_kabu` が `OrderSubmitted` を `send_order()` 呼び出しの前に emit していた。
`send_order()` が失敗した場合（例外 or `OrderID` なし）に `OrderRejected` も emit され、
同一 `client_order_id` に対して `OrderSubmitted → OrderRejected` という不整合なシーケンスが発生。
GUI の注文状態機械が undefined state に入る可能性があった。

**修正**: `OrderSubmitted` emit を `send_order()` 成功 + 有効な `OrderID` 取得後に移動。
失敗時は `OrderRejected` のみ emit される。

### MEDIUM-2 (R6): `KabuConnectionError` が `except KabuApiError` で吸収され venue がゾンビ状態に

**問題**: `send_order()` / `cancel_order()` 内で `httpx.ConnectError` が起きた場合、
`KabuConnectionError` (= `KabuApiError` のサブクラス) が server.py の `except KabuApiError` アームで
捕捉されていたが、`self._kabu_venue.clear()` が呼ばれていなかった。
その結果 token が保持され続け、次回の発注も同様に失敗する zombie 状態になった。

**修正**: `_do_submit_order_kabu` / `_do_cancel_order_kabu` に `except KabuConnectionError` アームを追加
（`except KabuTokenExpiredError` の後、`except KabuApiError` の前）。
`self._kabu_venue.clear()` を呼び出して token と order_client をリセット。

### 検証結果 (R6)

```
uv run pytest python/tests/test_kabu_server_orders.py python/tests/test_kabusapi_orders.py python/tests/test_invariant_reason_code.py -q
31 passed in 8.16s
```

---

## R7 レビュー反映 (2026-05-07, ラウンド 7)

### MEDIUM-1 (R7): `KabuTokenExpiredError` / `KabuConnectionError` 時に `_connected_venue` / `_live_state` がリセットされない

**問題**: R6 で `self._kabu_venue.clear()` を呼んでいたが、`self._connected_venue` と
`self._live_state` がリセットされなかった。GUI の `RequestVenueLoginState` ポーリングが
「接続中」を返し続け、zombie 状態になりうる。tachibana の `SessionExpiredError` 処理との
非対称性。

**修正**: `_clear_kabu_session()` ヘルパーを新設:
```python
def _clear_kabu_session(self) -> None:
    if self._kabu_venue is not None:
        self._kabu_venue.clear()
    self._connected_venue = None
    self._live_state = LiveState.DISCONNECTED
```
両メソッドの `KabuTokenExpiredError` / `KabuConnectionError` アームで
`self._kabu_venue.clear()` → `self._clear_kabu_session()` に変更。

### MEDIUM-2 (R7): `_do_cancel_order_kabu` に `venue_order_id` 空チェックがない

**問題**: `venue_order_id=""` が kabu API に渡ると、API エラーかランダムな発注取消が起きうる。
エラーは `except KabuApiError` で捕捉されるが、原因が空 venue_order_id とはログから分からない。

**修正**: メソッド冒頭に `if not venue_order_id: → OrderRejected{VALIDATION_ERROR}` ガードを追加。
接続チェック・lockout チェックよりも前に評価される。

### 検証結果 (R7, 収束確認)

```
uv run pytest python/tests/test_kabu_server_orders.py python/tests/test_kabusapi_orders.py python/tests/test_invariant_reason_code.py -q
31 passed
```

R7 サニティチェック (silent-failure-hunter): **MEDIUM+ ゼロ。収束。**

---

## Phase 3 タスク詳細

### P3-1 — Rust Exchange enum 市場細分化 + 先物・OP バリアント追加

**内容**: `Exchange` enum に以下を追加。`MarketKind` に `Future` / `Option` バリアントを追加。
- `KabuStationTse` — 東証 (exchange=1)
- `KabuStationNse` — 名証 (exchange=3)
- `KabuStationFse` — 福証 (exchange=5)
- `KabuStationSse` — 札証 (exchange=6)
- `KabuStationFuture` — 先物 (exchange=2/23/24)
- `KabuStationOption` — OP (exchange=2/23/24)

SCHEMA_MINOR: 18 → 19（Exchange enum 拡張）

**完了条件**:
- `cargo check --workspace` 通過
- `cargo test --workspace` 通過
- 網羅 match 全箇所が compile（adapter.rs / tickers_table.rs）

**関連ファイル**:
- `exchange/src/adapter.rs`
- `src/screen/dashboard/tickers_table.rs`
- `engine-client/src/lib.rs`（SCHEMA_MINOR bump）
- `engine-client/tests/schema_v2_4_nautilus.rs`

**状態**: ✅ 完了

---

### P3-2 — 先物・OP 発注 (kabusapi_orders.py 拡張)

**内容**: `KabuOrderClient` に `send_order_future()` / `send_order_option()` を追加。

**API**: `POST /sendorder/future` / `POST /sendorder/option`

**パラメータ** (OpenAPI `RequestSendOrderDerivFuture` / `RequestSendOrderDerivOption` より):
- Symbol (str), Exchange (int), TradeType (int), TimeInForce (int)
- Side (str: "1"=売/"2"=買), Qty (int), FrontOrderType (int)
- Price (float), ExpireDay (int)
- Optional: ClosePositionOrder, ClosePositions, ReverseLimitOrder

**設計決定**: 
- 取引パスワード (`Password`) は先物・OP の sendorder/future・sendorder/option には不要（OpenAPI スキーマに含まれない）
- cancelorder は既存の `cancel_order()` を流用（Password フィールドは引き続き必要）

**完了条件**:
- `test_kabusapi_futures.py::test_send_order_future_posts_to_correct_url` pass
- `test_kabusapi_futures.py::test_send_order_option_posts_to_correct_url` pass
- `test_kabusapi_futures.py::test_send_order_future_buy_side_is_2` pass

**関連ファイル**:
- `python/engine/exchanges/kabusapi_orders.py`（拡張）
- `python/tests/test_kabusapi_futures.py`（新規）

**状態**: ✅ 完了

---

### P3-3 — 余力照会・銘柄名照会 (kabusapi_rest.py 拡張)

**内容**: `KabuRestClient` に以下を追加。
- `fetch_wallet_future()` — GET /wallet/future
- `fetch_wallet_option()` — GET /wallet/option
- `fetch_symbolname_future(future_code, deriv_month)` — GET /symbolname/future
- `fetch_symbolname_option(option_code, deriv_month, put_or_call, strike_price)` — GET /symbolname/option

**完了条件**:
- `test_kabusapi_futures.py::test_fetch_wallet_future_calls_correct_url` pass
- `test_kabusapi_futures.py::test_fetch_wallet_option_calls_correct_url` pass
- `test_kabusapi_futures.py::test_fetch_symbolname_future_calls_correct_url` pass
- `test_kabusapi_futures.py::test_fetch_symbolname_option_calls_correct_url` pass

**関連ファイル**:
- `python/engine/exchanges/kabusapi_rest.py`（拡張）
- `python/tests/test_kabusapi_futures.py`（新規）

**状態**: ✅ 完了

---

### Phase 3 検証結果

```
uv run pytest python/tests/test_kabusapi_futures.py -v → 15 passed
uv run pytest python/tests/test_kabusapi_orders.py python/tests/test_kabusapi_futures.py python/tests/test_live_session_kabu.py -q → 39 passed
cargo check --workspace       → Finished (0 errors)
cargo clippy --workspace -- -D warnings → Finished (0 warnings)
cargo fmt --check             → OK (no formatting issues)
cargo test --workspace        → 全テスト pass
```

### Phase 3 R1 レビュー反映（2026-05-07）

4エージェント並列レビュー（rust-reviewer / silent-failure-hunter / ws-compatibility-auditor / general-purpose）実施。CRITICAL x2 / HIGH x5 を修正。

| 指摘 | 重大度 | 対応 |
|------|--------|------|
| `FrontOrderType` デフォルト `1` → OpenAPI 不正値（先物/OP 有効値: 18/20/28/30/120） | CRITICAL | `120` (成行マーケットオーダー) に修正 |
| `kabusapi_ws.py` `websockets.connect()` に `compression=None` 欠落（RSV1 バグ再発） | CRITICAL | `compression=None` 追加 |
| `kabusapi_rest.py` 4メソッドの `resp.json()` に `try/except` なし | HIGH | `try/except → KabuApiError` 追加 |
| `send_order_future/option` に `on_submit_success()` 欠如（idle タイマー未更新） | HIGH | `on_submit_success()` 追加 |
| `send_order_future/option` に `KabuTradePasswordInvalidError` ハンドリング欠如 | HIGH | `try/except` + `holder.on_invalid()` 追加 |
| `tickers_table.rs` フィルターボタンに Future/Option なし | HIGH | `future_market_btn` / `option_market_btn` 追加 |
| `from_venue_and_market(KabuStation, Stock)` 設計意図が未明示 | HIGH | doc コメント追記（意図的後方互換設計） |
| URL lint 正規表現が Phase 3 エンドポイントを対象外 | MEDIUM | `wallet/symbolname/cancelorder/orders/positions` 追加 |
| `test_send_order_option_sell_side_is_1` テスト欠如 | MEDIUM | テスト追加 |
| `Password` 非混入ピンテスト欠如 | MEDIUM | `test_*_no_password_in_body` x2 追加 |
| テスト名 `front_order_type_1` が不正値を明示 | MEDIUM | `_120` にリネーム、アサートも修正 |

---

## Phase 3 R2 レビュー反映 (2026-05-07, ラウンド 2)

| 指摘 ID | 重大度 | 内容 | 対応 |
|---------|--------|------|------|
| C-1 | HIGH | `kabusapi_rest.py` 既存 6 メソッドの裸の `resp.json()` + 全 `AsyncClient` に `timeout` 未設定 | 6 メソッドを `try/except → KabuApiError` でラップ、全 10 箇所に `timeout=30.0` 追加。テスト 6 件追加 |
| H-3 | HIGH | ログ文字列中のバックスラッシュ (`wallet\future` 等) が `\f` (U+000C) フォームフィード扱いになるサイレントログ破壊 | ログ文字列を `/` に統一済み（Phase 3 R1 実装時にすでに `wallet/future` 形式で記述されていた） |
| H-2 | HIGH | `_build_ready()` と `_startup_kabu_station()` が別々に `resolve_kabu_env()` を呼ぶ — env が変わると不整合 | `DataEngineServer.__init__()` に `self._kabu_env = resolve_kabu_env()` を追加してキャッシュ。両メソッドを `self._kabu_env` に変更。テスト 1 件追加 |
| H-1 | HIGH | `KabuStationVenue` に `send_order_future()` / `send_order_option()` facade なし。`server.py` の UNSUPPORTED_INSTRUMENT フェンスで先物・OP が全拒否されたまま | `kabusapi.py` にファサード 2 メソッド追加。`server.py` の `_do_submit_order_kabu()` を Phase 3 dispatch に書き換え（Future/Option は `send_order_future/option` へ振り分け）。テスト 3 件追加 |
| M-1 | MEDIUM | `implementation-plan.md` Phase 3 R1 の `on_submit_success()` 追加記述が Phase 2 R8 HIGH-3 の決定（除去済み）と矛盾 | `kabusapi_orders.py` の `send_order_future/option` にコメント追加（Password 不要のため holder 操作なし）。計画書 R1 表の記述は「Phase 2 R8 HIGH-3 により除去済みのため不適用」として設計根拠を明記 |
| M-2 | MEDIUM | `adapter.rs` の `Display` impl で `KabuStationFuture`/`KabuStationOption` が `_` ワイルドカード経由 | 両バリアントを明示的な match arm として追加 |
| M-3 | MEDIUM | `test_kabusapi_futures.py::test_send_order_future_limit_order_includes_price` docstring に「FrontOrderType は 2」と誤記 | 「FrontOrderType は 20（OpenAPI 有効値: 指値）」に修正 |
| M-4 | MEDIUM | `_WALLET_FUTURE_RESP` モックに OpenAPI スキーマとの対応コメントなし | コメントを追加して `WalletFuture` スキーマとの対応を明示 |
| M-5 | MEDIUM | P4-3 完了条件のテスト名が実在しない名前 | 実在するテスト名 `test_kabu_ready_capabilities_include_kabu_station` / `test_kabu_ready_capabilities_is_production_true_in_prod_env` に更新 |
| M-6 | MEDIUM | P4-2 の `_resolve_kabu_env()` 表記（アンダースコア付き非公開形式）が実際のパブリック関数名と相違 | `resolve_kabu_env()` に修正 |

### 主な設計判断

- **on_submit_success() 方針確定**: 先物・OP の `send_order_future/option` は取引パスワードを送らないため `KabuTradePasswordHolder` の `on_submit_success()` / `on_invalid()` は不要。Phase 2 R8 HIGH-3 決定を正式に計画書に記録。
- **server.py 配線追加**: `_do_submit_order_kabu()` が Phase 2 では UNSUPPORTED_INSTRUMENT で拒否していた先物・OP を、Phase 3 として本来の dispatch ロジックに切り替え完了。

### 検証結果

```
cargo fmt --check  → OK
cargo check --workspace  → Finished dev profile (0 errors)
cargo clippy --workspace -- -D warnings  → Finished (0 warnings)
cargo test -p flowsurface-exchange  → 14 passed（exchange_display_fromstr_roundtrip 含む）
uv run pytest python/tests/test_kabusapi_rest.py python/tests/test_kabusapi_orders.py python/tests/test_kabusapi_futures.py python/tests/test_kabu_server_orders.py python/tests/test_request_venue_login_state.py python/tests/test_kabusapi_auth.py -v  → 80 passed
uv run pytest python/tests/ -q --tb=short  → 2120 passed, 5 skipped
```

## Phase 3 R3 レビュー反映 (2026-05-07, ラウンド 3)

### サニティチェック結果

R2 修正（fetch_board register 順序変更 + `_make_server()` `_kabu_env` 追加）後の silent-failure-hunter 単独チェック。

| 重大度 | 件数 | 内容 |
|--------|------|------|
| CRITICAL | 0 | |
| HIGH | 0 | |
| MEDIUM | 0 | |
| LOW | 1 | `test_fetch_board_touches_existing_before_http` が HTTP 呼び出しとの前後関係を時系列的に独立証明していない（`call_log` はタッチ発生のみ記録。機能的影響なし） |

### 収束判定: MEDIUM+ ゼロ。収束。

### 検証結果

```
cargo fmt --check  → OK
cargo check --workspace  → Finished dev profile (0 errors)
cargo test --workspace  → 全件 pass（0 failures）
uv run pytest python/tests/ -q  → 2126 passed, 5 skipped
```

新規追加テスト累計（Phase 3 review-fix-loop R1-R3）: +65 件
- `test_kabusapi_rest.py`: +9件（非 JSON / register 順序）
- `test_kabu_server_orders.py`: +3件（Future/Option dispatch）
- `test_request_venue_login_state.py`: +1件（_kabu_env キャッシュ確認）

---

## R10 レビュー反映 (2026-05-07, Phase 2 R3)

Phase 2 実装全体の追加レビュー（4エージェント並列）で CRITICAL x4 / HIGH x6 / MEDIUM x10 を検出・修正した。

| 指摘 | 重大度 | 対応 |
|------|--------|------|
| `send_order()` body に `OrderType` フィールド（OpenAPI 不存在）。`FrontOrderType`/`Price`/`ExpireDay` が欠落 | CRITICAL | `OrderType` 削除、`FrontOrderType=10`/`Price=0`/`ExpireDay=0` 追加（C-1） |
| `cancel_order()` が `"OrderID"`（大文字D）を送信（OpenAPI は `"OrderId"` 小文字d）+ `Password` フィールドは OpenAPI に不存在 | CRITICAL | `"OrderId"` に修正、`Password` 削除、`_ensure_trade_password()` 呼出も削除（C-2） |
| 発注成功後 `_venue_to_client[order_id]` を更新しないため約定/取消イベントが Rust に届かない | CRITICAL | `self._venue_to_client[order_id] = order.client_order_id` 追加（C-3） |
| `_ensure_trade_password()` に `asyncio.Lock` なし — 並列発注でダイアログが多重起動しキャンセルで全発注ブロック | CRITICAL | `self._dialog_lock = asyncio.Lock()` + double-check パターン（C-4） |
| `poll_fills()` のポーラータスクが `server.py` に未配線（約定イベントが永遠に届かない） | HIGH | `_kabu_fill_poller_task` 属性追加、`_startup_kabu_station()` で起動、`_clear_kabu_session()` でキャンセル（H-1） |
| `KabuConnectionError` の `reason_code` が `"NOT_LOGGED_IN"` — 接続エラーに意味的に不適切 | HIGH | `"CONNECTION_ERROR"` に変更（H-2） |
| `is_locked_out()` のロックアウト解除後 `_invalid_count` がリセットされない | HIGH | `self._invalid_count = 0` 追加（H-3） |
| `KabuTradePasswordHolder` に `__repr__` なく、デバッグ時にパスワードが漏洩するリスク | HIGH | `__repr__()` 追加（パスワードマスク）（H-5） |
| `_emit_result()` が `except Exception` 内で例外を raise しても後続コードが続く | HIGH | `_emit_result()` を追加 `try/except` で保護（H-6） |
| `dev_kabu_trade_password_allowed` が `__main__.py` → `DataEngineServer` に未配線 | HIGH | `_env_dev_kabu_trade_password_allowed()` 関数追加 + `DataEngineServer()` に配線（H-4） |
| `OrderSubmitted` emit が API 呼び出し**後**（tachibana と非対称） | MEDIUM | API 呼び出し**前**に移動（M-1） |
| `_clear_kabu_session()` で `self._kabu_venue = None` 漏れ | MEDIUM | `None` 代入追加（M-2） |
| バリデーション失敗時の emit が `"Error"` 文字列（OrderRejected でない） | MEDIUM | `OrderRejected{VALIDATION_ERROR}` に変更（M-3） |
| `poll_fills()` にフィルタ前後デバッグログなし（調査困難） | MEDIUM | ログ追加（M-4） |
| `_make_server()` に `_kabu_fill_poller_task = None` / `_venue_to_client = {}` 未初期化 | MEDIUM | テストヘルパー補完（M-5） |
| `test_cancelorder_sends_password_in_body` — OpenAPI 修正後も古い仕様をテスト | MEDIUM | `test_cancelorder_includes_correct_fields` にリネーム + アサート更新（M-6） |
| stderr 切り捨て 200 バイトで日本語エラーが途切れる | MEDIUM | 500 バイトに拡大（M-7） |
| `clear()` docstring が lockout 保持の意図を説明していない | MEDIUM | docstring 更新（H-6 兼 M-8） |
| `test_server_submit_order_connection_error_clears_session` に `OrderRejected{CONNECTION_ERROR}` アサートなし | MEDIUM | アサート追加（M-9） |
| `_emit_result()` の例外保護漏れ | MEDIUM | `kabusapi_trade_dialog.py` の `except Exception` 内 `_emit_result()` を try/except で囲む（M-10） |

### R11 追加修正 (R2 指摘)

| 指摘 | 重大度 | 対応 |
|------|--------|------|
| `OrderSubmitted` emit が API 呼び出し後（tachibana は API 呼び出し前に発火） | MEDIUM | API 呼び出し直前に移動し `test_server_submit_order_missing_order_id_emits_rejected` を nautilus 流シーケンスに更新 |

### 検証結果 (R10 + R11)
```
uv run pytest python/tests/test_kabusapi_orders.py python/tests/test_kabu_server_orders.py \
  python/tests/test_live_session_kabu.py python/tests/test_request_venue_login_state.py \
  python/tests/test_invariant_reason_code.py python/tests/test_kabusapi_futures.py -q  → 67 passed
cargo check --workspace  → Finished dev profile (0 errors)
cargo clippy --workspace -- -D warnings  → Finished (0 warnings)
cargo fmt --check  → OK
```

---

## R12 レビュー反映 (2026-05-07, Phase 2 R3 サニティ + R4 収束)

R10/R11 後の追加 review-fix-loop で 3 ラウンド回し、サニティ漏れを潰した。

### R3 サニティ修正
- **R3-H1**: `python/tests/test_invariant_reason_code.py` の `_FILES_TO_CHECK` に `kabusapi_orders.py` を追加（将来 reason_code が追加された際の lint 抜け防止）
- **R3-M1**: `.github/workflows/python-tests.yml` のメイン CI フィルタに `not demo_kabu` を追加（kabu-mock CI との二重実行回避）
- **R3-M2**: `python/engine/server.py` の `_do_submit_order_kabu` の `except KabuTokenExpiredError` arm に `NOT_LOGGED_IN ≠ CONNECTION_ERROR` の意図コメントを追加

### R4 収束（LOW 1 件のみ）
- **R4-L1**: `_do_cancel_order_kabu` 側の `except KabuTokenExpiredError` arm にも R3-M2 と対称なコメントを追加（submit/cancel の対称性確保）

### 検証結果 (R12)
```
uv run pytest python/tests/test_invariant_reason_code.py python/tests/test_kabu_server_orders.py -q  → 22 passed
cargo fmt --check  → OK
```

R4 サニティチェック (silent-failure-hunter): **HIGH/MEDIUM 0 件。収束。**

---

## R13 レビュー反映 (2026-05-07, Phase 2 review-fix-loop R2)

### HIGH-1 (R13): `KABU_IS_PRODUCTION` が初回 Ready で更新されない

**問題**: `spawn_venue_ready_bridge_on` が `conn.subscribe_events()` でブロードキャストを購読する前に、最初の `Ready` イベントが `perform_handshake` 内で送信済みのため、初回接続・再接続時に `is_production` が常に `false`（検証表示）のままになりうる。`broadcast` はリプレイを持たないため購読前のイベントは取得不可。

**修正**: `spawn_venue_ready_bridge_on`（`src/main.rs`）で `conn.subscribe_events()` を呼ぶ前に、`conn.capabilities()` ハンドシェイクスナップショットから `parse_kabu_is_production` を呼び `KABU_IS_PRODUCTION` をシードするコードを追加。以降はイベントループが `Ready` イベントでの更新を担当。

**追加テスト**: `kabu_production_banner_tests::bridge_seeds_is_production_from_handshake_capabilities`

### MEDIUM-1 (R13): `parse_kabu_is_production` のパースエラーが無音で握り潰される

**問題**: `.ok().flatten().unwrap_or(false)` が型不一致などの `Err` を `log::warn` なしでサイレントに `false` へ変換しており、Python/Rust スキーマ乖離が発生した場合にデバッグ手がかりが得られなかった。

**修正**: `parse_kabu_is_production` を `match` 式に書き換え、`Err(e)` アームで `log::warn!` を発行してから `false` を返すよう変更。fail-safe 挙動は維持。

**追加テスト**: `kabu_production_banner_tests::parse_fails_gracefully_on_type_mismatch_for_schema_drift`

### 検証結果 (R13)

```
cargo fmt --check   → OK
cargo check --workspace  → Finished dev profile (0 errors)
cargo clippy --workspace -- -D warnings  → Finished (0 warnings)
cargo test --workspace  → 全件 pass（新規テスト +2 件含む）
```

---

## Phase 4 タスク詳細（提案・着手前）

**ゴール**: 本番接続 (`localhost:18080`) を多層ガード付きで解禁し、最小 1 単元の実弾発注スモークテストが可能な状態にする。runbook（事故対応・取消手順・本体ダウン時オペレーション）を整備する。実弾発注は AI 側では実行せず、runbook の手動手順としてユーザーが実施する（合意済 2026-05-07）。

**スコープ外（明示）**:
- 24h 連続稼働の自動検証（ユーザーの手動運用に委ねる）
- 自動再ログイン（早朝強制ログアウトはバナー誘導のまま）
- 本番口座の自動残高チェック・自動損切り

### Phase 4 タスク表

| Task | 内容 | 完了条件（テストファイル + 代表 assert） |
| :--- | :--- | :--- |
| P4-1 | `kabusapi_url.py` に `is_production_url(url)` / `guard_prod_url(url)` を追加。`KABU_ALLOW_PROD=1` 未設定で `localhost:18080` または `/kabusapi/...` の prod 経路を返す/呼ぶと `ValueError("KABU_ALLOW_PROD")` を raise。`base_url("prod")` / `endpoint(..., env="prod")` / `ws_url("prod")` 全経路で多層ガード。env="verify" は env なしで通る。 | `test_kabu_prod_url_guard.py::test_prod_blocked_without_env` / `test_prod_allowed_with_env_1` / `test_verify_always_passes` / `test_env_0_blocks` / `test_env_true_string_blocks`（tachibana の `test_prod_url_guard.py` と同形） |
| P4-2 | `KabuStationVenue` / `_startup_kabu_station` / login flow に `env: KabuEnv` 引数を伝播。`server.py` 起点で **二重 env**（`KABU_ALLOW_PROD=1` **かつ** `KABU_ENV=prod`）を要求する `resolve_kabu_env()` ヘルパー追加（`kabusapi_url.py` のパブリック関数名と一致）。片方だけでは verify にフォールバックし WARN ログ。release ビルドでも `DEV_KABU_API_PASSWORD` による prod 自動ログインを禁止（dev_login_allowed の二段ガード）。 | `test_kabu_env_resolver.py::test_resolve_defaults_to_verify` / `test_both_envs_required_for_prod` / `test_only_allow_prod_falls_back_to_verify` / `test_prod_disables_dev_login` |
| P4-3 | `VenueReady.capabilities["kabu_station"]` に `is_production: bool` を追加。SCHEMA_MINOR bump。`server.py` の `_build_ready()` で `self._kabu_env == "prod"`（H-2 キャッシュ経由）のとき `True`。Rust 側 `engine_client::capabilities` に `is_production` フィールド追加（既存 4 フィールド + 1）。 | `cargo test --workspace` 通過。`test_request_venue_login_state.py::test_kabu_ready_capabilities_include_kabu_station`（is_production=False を内包）/ `test_kabu_ready_capabilities_is_production_true_in_prod_env`（prod）。SCHEMA_MINOR bump assert。 |
| P4-4 | iced UI フッター kabu バッジに本番表示。`is_production=true` のとき赤背景 + "🔴 本番" ラベル、verify は既存の薄色 + "検証" ラベル。文字列は spec.md / architecture.md にも追記。 | `cargo test -p src --lib footer_badge`（既存テストの色/ラベル assert 拡張）。スクリーンショット比較は不要、文言と styling 関数のユニットテスト。 |
| P4-5 | `_do_submit_order_kabu` / `_do_cancel_order_kabu` の URL 組立で `guard_prod_url()` を呼ぶ pin。誤って verify セッション中に prod URL が漏れた場合の最終フェンス。`KabuStationVenue.send_order` / `cancel_order` / `poll_fills` も同様に pin。 | `test_kabu_prod_url_guard.py::test_send_order_invokes_guard_prod_url`（mock で `guard_prod_url` の呼出を assert）。 |
| P4-6 | `docs/✅kabusapi/runbook.md` 新規作成。章構成: §1 緊急時の連絡先・口座、§2 全注文一括取消手順（kabuステーション本体 + REST `PUT /cancelorder`）、§3 kabuステーション本体ダウン時のオペレーション、§4 早朝強制ログアウト時の挙動・再ログイン手順、§5 実弾スモークテスト手順（最小 1 単元 buy → 即 sell の手順チェックリスト）、§6 取引パスワード忘却・lockout 復旧手順、§7 本番↔検証切替の env 設定方法、§8 ログ収集 / インシデントレポート雛形。 | `docs/✅kabusapi/runbook.md` ファイルが存在し、§1〜§8 が見出しとして揃っている（lint チェック程度。内容のレビューは review-fix-loop で行う）。 |
| P4-7 | `open-questions.md` Q-P2-5（取引パスワード誤りエラーコード）を解消。kabu OpenAPI / ptal/howto を再確認し、確定 code を `kabusapi_auth.py` の `KabuTradePasswordInvalidError` 判定に反映。確定不能な場合は「Phase 4 でも未確定 + 検出戦略」を計画書に明記。 | `test_kabusapi_orders.py::test_invalid_trade_password_recognized_for_code_*` の code を確定値に更新（または `Phase 4 未確定` の根拠コメントを残す）。 |
| P4-8 | URL lint 正規表現を本番 URL も拾うよう拡張: `(http\|ws)://localhost:1808[01]` のうち `:18080` を含むリテラルが `kabusapi_url.py` 以外で現れたら fail。CI に追加。 | `.github/workflows/kabu-mock.yml` に lint step 追加 + zero-match assert。 |
| P4-9 | `pytest -m demo_kabu` ジョブに Phase 4 新規テスト群を追加。`test_kabu_prod_url_guard.py` / `test_kabu_env_resolver.py` を CI コマンドに含める。 | CI グリーン。 |

### 依存関係

```
P4-1 ──→ P4-2 ──→ P4-3 ──→ P4-4
            └──→ P4-5
P4-6（並列可、ドキュメント単独）
P4-7（並列可、Q-P2-5 調査依存）
P4-8 ──→ P4-9（最後）
```

並列実行フェーズ:
- Phase A（直列）: P4-1
- Phase B（並列可）: P4-2 + P4-6 + P4-7
- Phase C（並列可）: P4-3 + P4-5（B 完了後）
- Phase D（直列）: P4-4（C 完了後）
- Phase E（直列）: P4-8 + P4-9（全完了後）

### Acceptance criteria（Phase 4 全体）

1. `KABU_ALLOW_PROD` 未設定では prod URL 生成・接続が必ず ValueError を raise
2. `is_production` フラグが Ready handshake 経由で UI に伝わり、本番接続中は赤バナー表示
3. `runbook.md` §1〜§8 が揃っており、最小 1 単元実弾スモークテスト手順がチェックリスト化
4. `cargo test --workspace` / `uv run pytest python/tests/ -q` 全件グリーン
5. `pytest -m demo_kabu` CI ジョブに新規テスト含めてグリーン
6. SCHEMA_MINOR bump 反映（19 → 20 想定）
7. 既存 Phase 1〜3 テストを 1 件も壊していない
8. review-fix-loop で MEDIUM+ 指摘ゼロまで収束

### Phase 4 進捗 (2026-05-07)

- ✅ P4-1: `is_production_url` / `guard_prod_url` 追加 + `base_url` / `endpoint` / `ws_url` で自動 pin。`test_kabu_prod_url_guard.py` 21 件 GREEN。
- ✅ P4-2: `resolve_kabu_env()` 追加（`KABU_ALLOW_PROD=1` + `KABU_ENV=prod` 二重判定）。`_startup_kabu_station` に env 伝播 + prod では `dev_login_allowed` / `dev_trade_password_allowed` を強制 False。`test_kabu_env_resolver.py` 9 件 + server 2 件 GREEN。
- ✅ P4-5: 発注パスでの guard pin（`base_url` 経由で自動）。`test_kabu_prod_url_pin.py` 6 件 GREEN（send_order / cancel_order / send_order_future / send_order_option / poll_fills / verify pass）。
- ✅ P4-3: `is_production` capabilities フラグ + SCHEMA_MINOR 19→20 bump。Python `test_request_venue_login_state.py` 3 件 + Rust `capabilities::tests::test_kabu_station_is_production_can_be_read` GREEN。
- ✅ P4-4: iced UI フッター kabu バッジに本番赤バナー（"🔴 本番"）。`KABU_IS_PRODUCTION` AtomicBool + `parse_kabu_is_production` + `kabu_chip_prod_style`。Bridge が `Ready` 受信時に capabilities から抽出。`kabu_production_banner_tests` 8 件 GREEN。
- ✅ P4-7: Q-P2-5 部分解決。`4002013` は実は MarginTradeType param error と判明（取引パスワード誤りの専用 code は kabu 公式 spec に存在しない）。code-based 判定を message-based 判定に切替。`open-questions.md` Q-P2-5 を「部分解決」に更新。
- ✅ P4-8: URL lint を `python/engine/` から `src/` / `engine-client/` / `exchange/` / `data/` まで拡張。`__pycache__` / `target` 除外。`localhost:18080` も検出範囲に含まれる（既存 `1808[01]` パターン）。
- ✅ P4-9: 新規テスト 3 ファイルを `pytest -m demo_kabu` ジョブに追加（`test_kabu_prod_url_guard.py` / `test_kabu_env_resolver.py` / `test_kabu_prod_url_pin.py`）。
- ✅ P4-6: 骨子完成（`docs/✅kabusapi/runbook.md` §1〜§8）。Phase 4 実装後に詳細を肉付け予定（review-fix-loop 前）。

### Phase 4 検証結果 (2026-05-07)

```
uv run pytest python/tests/ -q --tb=no                          → 2110 passed, 5 skipped
uv run pytest -m demo_kabu (kabu-mock.yml と同コマンド)          → 142 passed, 4 deselected
cargo test --workspace                                          → 全件 pass
cargo check --workspace                                         → 0 errors
```

Phase 4 主要実装完了。残るは review-fix-loop（オプション）と runbook 肉付け。

### Phase 4 着手中の知見

- **2026-05-07 P4-5 観察ミスの訂正**: 一時 `test_server_submit_order_missing_order_id_emits_rejected` を「pre-existing 失敗」と記録したが**誤り**。現在のテスト (290 行) は `assert "OrderSubmitted" in events` を要求しており、実装 (`server.py:1878`) も nautilus 流 2 段イベントとして OrderSubmitted → OrderRejected を順に emit する。R6 はこの 2 段イベント仕様そのものを定義しており、適用済み。stash/unstash 途中の一時的な失敗ログを文字化け越しに誤読したのが原因。検証: `uv run pytest python/tests/test_kabu_server_orders.py::test_server_submit_order_missing_order_id_emits_rejected -v` → PASSED。
- **設計判断 (P4-1)**: `base_url()` 内で `guard_prod_url()` を必ず呼ぶようにしたため、`endpoint()` / `ws_url()` を経由する全ての URL 組立が自動 pin される。orders.py 側に追加 pin コードは不要で、`KabuOrderClient(env="prod")` で発注メソッドを呼んだ時点で URL 組立段階で ValueError が raise される。最小変更で多層化を達成。
- **設計判断 (P4-2)**: env 解決を `kabusapi_url.py`（下層）に置いたため、server.py 以外（テスト、SDK、将来のスクリプト）からも同じヘルパーで env 判定できる。release ガード（prod で dev_login_allowed=False 強制）は server.py 側の責任に分離。

### スコープ外 / 後続 Phase 候補

- 24h 連続稼働の自動 E2E（環境構築コスト大、ユーザー手動運用）
- 自動再ログイン・自動取消（誤発注リスク回避、現状ユーザー誘導）

---

## Phase 3 R2 レビュー反映 (2026-05-07, review-fix-loop R2)

### R2-H1: `fetch_board()` の `register()` / `touch()` 順序修正

**問題**: `fetch_board()` が新規銘柄に対して HTTP 呼び出し**前**に `register()` を呼んでいたため、HTTP 失敗時（timeout / ConnectError）に `RegisterSet` がサーバー実態と乖離していた（サーバーは未登録なのにローカルは登録済みと記録）。

**修正** (`python/engine/exchanges/kabusapi_rest.py`):
- 新規銘柄: HTTP 成功後に `register()` を呼ぶよう順序を変更。HTTP 失敗時は `RegisterSet` に追加しない。
- 既存銘柄: サーバー側は既に PUSH 登録済みなので `touch()` は HTTP 前に呼んで問題なし（従来通り）。

**追加テスト** (`python/tests/test_kabusapi_rest.py`):
- `test_fetch_board_does_not_register_on_http_failure` — ConnectError 時に `register()` が呼ばれないことを assert
- `test_fetch_board_registers_after_http_success` — HTTP 成功後に `register()` が呼ばれることを assert
- `test_fetch_board_touches_existing_before_http` — 既存銘柄の `touch()` が HTTP 前に呼ばれ `register()` が呼ばれないことを assert

**影響**: `test_fetch_board_raises_when_full` を更新（満杯チェックが HTTP 後になるため HTTP モックを追加）。

### R2-M1: `_make_server()` に `_kabu_env` 属性追加

**問題**: `_make_server()` ヘルパーに `_kabu_env` が未設定だった。`_startup_kabu_station()` を呼ぶテストを追加した場合に `AttributeError` が発生するリスク。

**修正** (`python/tests/test_kabu_server_orders.py`):
- `_make_server()` に `srv._kabu_env = "verify"` を追加（1 行追加）。

### R2-L1: P4-3 タスク表の表記修正

`_build_ready()` の `is_production` 判定を `resolve_kabu_env() == "prod"` から `self._kabu_env == "prod"`（H-2 キャッシュ経由）に訂正。

### 検証結果 (R2)

```
uv run pytest python/tests/test_kabusapi_rest.py python/tests/test_kabu_server_orders.py -v  → 30 passed
uv run pytest python/tests/ -q --tb=short  → 2126 passed, 5 skipped
```
- 本番口座残高アラート・自動損切り（戦略責任の領域、AGENTS.md の「ユーザー戦略は自己責任」方針に沿う）

---

## R14 レビュー反映 (2026-05-07, Phase 4 R1)

3 エージェント並列（rust-reviewer / silent-failure-hunter / general-purpose）で CRITICAL 0 / HIGH 3 / MEDIUM 8 / LOW 3 を検出・修正。

### HIGH-1: `KABU_IS_PRODUCTION` が VenueError/VenueLoginStarted/VenueLoginCancelled で false リセットされない
`src/main.rs` の 3 イベントアームを OR パターンに統合し `if venue == KABU_STATION_VENUE_NAME` ブロックで `KABU_IS_PRODUCTION.store(false, Release)` を追加。回帰テスト 3 件追加。

### HIGH-2: `RecvError::Lagged` 時の `KABU_IS_PRODUCTION` リセット欠如
Lagged アームに `false` store + warn ログ追加。回帰テスト 1 件追加。

### HIGH-3: `KABU_IS_PRODUCTION` を書き換えるテスト 2 件が並列競合 (flaky)
`cache_load_store_round_trips` と `bridge_seeds_is_production_from_handshake_capabilities` を `atomic_store_load_and_seeding` 1 関数に統合。

### MED-A: URL lint に `/kabusapi/token` 追加
### MED-B: `guard_prod_url` mock 呼出 assert テスト追加 (`test_send_order_invokes_guard_prod_url`)
### MED-C: `is_production_url()` が `127.0.0.1:18080` を見逃す → `or "127.0.0.1:18080" in url` 追加
### MED-D: `test_schema_minor_is_20_after_p4_3` に `@pytest.mark.demo_kabu` 付与
### MED-E: `architecture.md §8` capabilities JSON に `"is_production": false` 追記
### MED-F: `runbook.md §6` の "暫定 4002013" を P4-7 解決済み内容に更新
### MED-G: `_startup_kabu_station` docstring を Phase 4 実態に合わせて更新
### MED-H: `kabu_chip_prod_style()` に `#[must_use]` 追加

### 検証結果 (R14)
```
cargo fmt --check  → OK
cargo check --workspace  → 0 errors
cargo clippy --workspace -- -D warnings  → 0 warnings
cargo test --workspace  → 全件 pass
uv run pytest python/tests/ -q --tb=no  → 2126 passed, 5 skipped
uv run pytest -m demo_kabu ...  → 97 passed, 3 deselected
```

---

## R15 レビュー反映 (2026-05-07, Phase 4 R2)

R14 修正後のサニティチェックで MEDIUM 2 / LOW 1 を追加修正。

### MEDIUM R2-A: `spawn_venue_ready_bridge_on` の `KABU_IS_PRODUCTION` シードが `VENUE_READY_CACHE` 未初期化時にスキップされる
シード処理を `VENUE_READY_CACHE` の `match` ガードより前に移動。本番パスでは問題なかったが、テストコードから直接呼ばれた場合の silent no-seed を防止。

### MEDIUM R2-B: URL lint 正規表現が `127.0.0.1:18080` リテラルを検出しない
`kabu-mock.yml` の lint PATTERN を `(localhost|127\.0\.0\.1):1808[01]` に拡張（MED-C の `is_production_url()` 変更との整合）。

### LOW R2-C: `test_send_order_invokes_guard_prod_url` に `@pytest.mark.demo_kabu` の二重付与
ファイルレベル `pytestmark` で全テストに適用済みのため個別デコレータを削除。

### 検証結果 (R15)
```
cargo fmt --check  → OK
cargo check --workspace  → 0 errors
cargo test --workspace  → 全件 pass
uv run pytest python/tests/ -q --tb=no  → 2126 passed, 5 skipped
```

R2 サニティチェック (silent-failure-hunter): **MEDIUM+ 0 件。収束。**

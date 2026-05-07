# kabuステーション venue 統合: 実装計画

## フェーズ一覧

| Phase | 内容 | 状態 |
| :--- | :--- | :--- |
| Phase 0 | 計画フェーズ（本文書群） | 🔵 完了 |
| Phase 1 | リードオンリー統合（検証環境のみ）| ✅ 完了 |
| Phase 2 | 発注（検証環境のみ） | — |
| Phase 3 | 先物・OP・市場細分化 | — |
| Phase 4 | 本番接続 | — |

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
- CI: `.github/workflows/kabu-mock.yml` / `pytest -m demo_kabu`
- 本物 kabuステーション: Windows 環境のみ、CI 不可

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

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

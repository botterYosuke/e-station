# kabuステーション venue 統合: 不変条件テスト一覧

各 K-task の不変条件 ID とテストファイルの対応表（Phase 0 雛形）。
各 K-task 詳細 assert は Phase 1 実装内で追記する。

## K-task ↔ 不変条件 ID 対応表

| K-task | 不変条件 ID | 不変条件の説明 | テストファイル | 代表 assert |
| :--- | :--- | :--- | :--- | :--- |
| K1 | INV-K1-ENUM | `Venue::KabuStation` / `Exchange::KabuStationStock` が enum 網羅 match 全箇所でコンパイルする | `cargo test -p engine-client schema_minor_kabu` | compile success |
| K1 / K4 | INV-K1-CAP | `max_push_symbols=50` と `RegisterSet.MAX=50` の一致 | `test_kabusapi_capabilities.py::test_capabilities_max_push_symbols_matches_register_set` | `assert capabilities["max_push_symbols"] == RegisterSet.MAX == 50` |
| K2 | INV-K2-TOKEN-ERR | `Code=4001005` から `KabuTokenExpiredError` が発火する | `test_kabusapi_auth.py` | `pytest.raises(KabuTokenExpiredError)` |
| K2 | INV-K2-TOKEN-ERR-2 | `Code=4001001` からも `KabuTokenExpiredError` が発火する | `test_kabusapi_auth.py` | `pytest.raises(KabuTokenExpiredError)` |
| K2 | INV-K2-NO-LOG-SECRET | caplog に token / API パスワード / 取引パスワードが出力されない | `test_kabusapi_auth_logging.py` | `assert TOKEN not in caplog.text` など |
| K2 | INV-K2-SJIS-REJECT | SJIS バイト列で `UnicodeDecodeError` が発火する | `test_kabusapi_codec.py::test_decode_rejects_sjis_bytes` | `pytest.raises(UnicodeDecodeError)` |
| K2 | INV-K2-SIDE-BUY-2 | kabu 買い区分は `"2"` であり `"3"` ではない | `test_kabusapi_codec.py::test_side_mapping_kabu_buy_is_2_not_3` | `assert buy_side == "2"` |
| K3 | INV-K3-TCP-RETRY | TCP 拒否時 5s × 3 回後に `VenueError{code:"local_app_down"}` | `test_kabusapi_login_flow.py::test_tcp_refused_three_retries_then_local_app_down` | `assert error.code == "local_app_down"` |
| K3 | INV-K3-NO-TKINTER | `DEV_KABU_API_PASSWORD` 設定時に tkinter を spawn しない | `test_kabusapi_login_flow.py` | `mock_spawn.assert_not_called()` |
| K3 | INV-K3-MORNING | 早朝時刻帯の `local_app_down` は INFO ログ扱い | `test_kabusapi_login_flow.py` | `assert "INFO" in caplog.records[...]` |
| K4 | INV-K4-REG-FULL | 51 件目の register で `KabuRegisterFullError` | `test_kabusapi_register.py::test_register_51st_raises_full` | `pytest.raises(KabuRegisterFullError)` |
| K4 | INV-K4-LRU-EVICT | LRU evict 時に `SubscriptionEvicted{symbol}` を IPC 送出 | `test_kabusapi_register.py::test_lru_evict_emits_subscription_evicted` | `assert evicted_symbol in ipc_events` |
| K4 | INV-K4-RATELIMIT | OrderBucket(5) が 6 件目でブロックする | `test_kabusapi_ratelimit.py::test_order_bucket_blocks_at_6th_req` | 6 件目が待機することを assert |
| K5 | INV-K5-RECONNECT-REREG | 再接続後に RegisterSet 全件を re-register | `test_kabusapi_ws.py::test_reconnect_reregisters_all_symbols` | `assert PUT /register calls == 全件` |
| K5 | INV-K5-ABORT | 5s × 5 回連続失敗で reconnect ループ打ち切り → `VenueError{code:"local_app_down"}` | `test_kabusapi_ws.py::test_reconnect_aborts_after_5_consecutive_failures` | `assert error.code == "local_app_down"` |
| K6 | INV-K6-TOUCH | `fetch_board()` が `RegisterSet.touch()` を呼ぶ | `test_kabusapi_rest.py::test_fetch_board_touches_register_set` | `register_set.touch.assert_called()` |
| K6 | INV-K6-REST-FULL | `fetch_board()` で新規 + 満杯時に `KabuRegisterFullError` | `test_kabusapi_rest.py` | `pytest.raises(KabuRegisterFullError)` |
| K7 | INV-K7-E2E | `LiveSession.login(venue="kabu_station")` が `VenueReady{venue:"kabu_station"}` を発火 | `test_live_session_kabu.py::test_login_kabu_station_emits_venue_ready` | `assert event.venue == "kabu_station"` |
| K8 | INV-K8-URL-LINT | `kabusapi_url.py` 以外に URL リテラルが存在しない | CI lint step | zero-match assertion |
| K8.5 | INV-K85-CI | `pytest -m demo_kabu` が CI でグリーン | `.github/workflows/kabu-mock.yml` | CI pass |

## 早朝強制ログアウト時刻帯の分岐定義 {#early-morning-logout}

kabuステーション本体は早朝に強制ログアウトする仕様。
ptal/howto.html の記載が確認できるまでは暫定として以下を適用:

- **暫定時刻帯**: JST 4:00〜9:00
- **ログレベル**: 当該時刻帯に `local_app_down` が発生した場合は `ERROR` でなく `INFO` で記録
- **Q-K3 確定後**: 正式な時刻帯に更新し、この節と `invariant-tests.md` の INV-K3-MORNING を更新

## SCHEMA_MINOR bump 不変条件

`python/engine/schemas.py` の `SCHEMA_MINOR` 変更時は以下を合わせて更新する:

- `engine-client/src/lib.rs` の `SCHEMA_MINOR` 定数
- `test_schema_compat.py` の `VenueError.code` 既存値の venue 横断列挙 assert
- `token_expired` / `local_app_down` の名前空間衝突確認 assert

Rust test: `cargo test -p engine-client schema_minor_kabu`
Python test: `uv run pytest python/tests/test_schema_compat.py`

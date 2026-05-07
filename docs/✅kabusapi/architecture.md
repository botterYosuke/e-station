# kabuステーション venue 統合: アーキテクチャ

## 1. 配置原則

立花 venue（[docs/✅tachibana/architecture.md §1](../../✅tachibana/architecture.md#1-配置原則)）と同じ方針を踏襲する。

| 責務 | 所在 | 備考 |
| :--- | :--- | :--- |
| API パスワード保持 | **Python メモリのみ** | tkinter ダイアログ入力 or `DEV_KABU_API_PASSWORD` env |
| トークン取得 (`POST /token`) | **Python** (`kabusapi_auth.py`) | Rust 関与ゼロ |
| トークン保持 | **Python メモリのみ** | ファイル永続化しない（本体終了で失効）|
| `X-API-KEY` ヘッダ付与 | **Python** | 全リクエストに付与 |
| 板パース (`PushBoardSuccess` JSON) | **Python** (`kabusapi_ws.py`) | 既存 `DepthSnapshot` IPC へ詰め替え |
| PUSH 銘柄登録 50 上限管理 | **Python** (`kabusapi_register.py`) | LRU で枠管理、evict 時は IPC `SubscriptionEvicted{symbol}` 送出 |
| 流量制限 token-bucket | **Python** (`kabusapi_ratelimit.py`) | 発注 5/s, 余力 10/s, 情報 10/s |
| エラー判定（HTTP + body `Code`） | **Python** (`kabusapi_auth.check_response`) | `KabuApiError` 派生例外を投げる |
| ログイン画面（tkinter） | **Python** (`kabusapi_login_dialog.py`) | subprocess 隔離、立花と同じ流儀 |
| 取引パスワード（取消／発注）UI | **Python tkinter subprocess** | Phase 2 以降。都度入力、メモリのみ保持 |
| ログイン発火判定 | **Python** (`kabusapi_login_flow.py`) | startup_login で本体疎通 → トークン取得 |
| バナー文言 | **Python** | Rust UI は `VenueError.message` をそのまま描画 |
| UI フレーム / チャート描画 | **Rust** (既存 iced 流用) | venue 固有コードゼロ |

**Rust 側に新設されるもの（最小限）**:
- `exchange/src/adapter.rs` — `Venue::KabuStation` / `Exchange::KabuStationStock` バリアント追加
- `exchange/src/adapter/client.rs` — `AdapterHandles.kabu_station: Option<Arc<dyn VenueBackend>>` フィールド追加
- `engine-client/src/capabilities.rs` — `venue_capabilities["kabu_station"]` キー追加
- IPC schema: SCHEMA_MINOR bump（`"kabu_station"` を `venue` フィールドで受理）

**Rust 側に書かないもの（禁則）**:
- `data/src/config/kabu.rs` — クレデンシャル永続化は Python 側
- `exchange/src/adapter/kabu.rs` — API 呼出は Python
- `src/screen/login_kabu.rs` — tkinter で開く
- URL リテラル (`localhost:18080` / `localhost:18081` / `/kabusapi/websocket` 等)

## 2. Python 自律ログイン方式

### 2.1 起動シーケンス

```
Rust (flowsurface) 起動
    ↓ stdin payload: { port, token, config_dir, cache_dir, dev_kabu_login_allowed: bool }
Python エンジン起動
    ↓ startup_login():
        1. DEV_KABU_API_PASSWORD がある場合 → tkinter ダイアログをスキップ
        2. ない場合 → tkinter subprocess を spawn して API パスワードを収集
        3. POST /token でトークン取得（失敗時 5s × 3 回 retry → VenueError{code:"local_app_down"}）
        4. トークン取得成功 → Python メモリに保持
        5. IPC: VenueReady{venue:"kabu_station"} 送信
Rust: VenueReady 受信 → チャート表示可
```

### 2.2 クレデンシャル管理

- **API パスワード**: Python メモリのみ（`DEV_KABU_API_PASSWORD` env or tkinter 入力）
- **トークン**: Python メモリのみ（起動毎に `POST /token` で取得、ファイルに書かない）
- **取引パスワード**: Phase 2 以降（都度 tkinter subprocess で収集、即削除）

ファイルキャッシュは**作らない**（立花の `tachibana_session.json` 相当は存在しない）。

### 2.3 トークン失効時の挙動

`4001001` / `4001005` 検出 → `/token` 再取得を **1 回 retry**（メモリ保持の API パスワードのみ使用）。
retry 失敗 → `VenueError{code:"token_expired"}` を発火し tkinter 再ログインへ誘導。
**自動再ログインは禁止**（ユーザー入力を伴う再ログインは自動化しない）。

### 2.4 本体プロセス落ち時の挙動

`ConnectionRefusedError` → 5s backoff × 3 回 retry → `VenueError{code:"local_app_down"}`。

早朝強制ログアウト時刻帯: リトライ後の `local_app_down` を ERROR でなく INFO 扱いとする
（invariant-tests.md §early-morning-logout に定義）。

## 3. IPC ライフサイクル

既存 DTO をそのまま流用し、`venue` 文字列として `"kabu_station"` を受理する。

| IPC メッセージ | 方向 | 発火タイミング |
| :--- | :--- | :--- |
| `RequestVenueLogin{venue:"kabu_station"}` | Rust → Python | GUI から「ログイン」ボタン押下 |
| `VenueLoginStarted{venue:"kabu_station"}` | Python → Rust | startup_login 開始時 |
| `VenueLoginCancelled{venue:"kabu_station"}` | Python → Rust | ユーザーがダイアログをキャンセル |
| `VenueReady{venue:"kabu_station"}` | Python → Rust | トークン取得成功 |
| `VenueError{venue:"kabu_station", code, message}` | Python → Rust | エラー検出時 |

`VenueError.code` 予約値:
- `"token_expired"` — retry 1 回失敗、tkinter 再ログインへ誘導
- `"local_app_down"` — 本体プロセス落ち / WebSocket 再接続打ち切り

## 4. PUSH WebSocket

```
kabusapi_ws.connect(env="verify", token=..., on_message=handler)
    ↓ websockets.connect("ws://localhost:18081/kabusapi/websocket",
                         ping_interval=20, ping_timeout=10)
    ↓ 受信ループ:
        msg = json.loads(frame)  # PushBoardSuccess
        DepthSnapshot IPC を生成して送出
    ↓ 切断検知:
        RegisterSet 全件を PUT /register で再登録
        指数バックオフ再接続（5s × 5 回）
        5 回連続失敗 → VenueError{code:"local_app_down"}
```

**再接続後は常に RegisterSet 全件 re-register**（サーバ側保持に依存しない、Q-K1 の検証結果に依らないデフォルト挙動）。

## 5. 銘柄登録 (RegisterSet)

```
RegisterSet(max_symbols=50)
    register(symbol, exchange) → 空き有り: PUT /register + 追加
                                 満杯: KabuRegisterFullError（暗黙 evict しない）
    unregister(symbol, exchange) → PUT /unregister + 削除
    unregister_all() → PUT /unregister/all + クリア
    touch(symbol) → LRU 位置更新（GET /board が呼ぶ）
    evict_lru() → 最古の銘柄を unregister、IPC SubscriptionEvicted{symbol} 送出
```

`GET /board` は内部的に自動 PUSH 登録を発火する（SKILL R6）。
よって `kabusapi_rest.fetch_board()` 内で必ず `RegisterSet.touch()` を呼ぶ。
新規 + 満杯時は `KabuRegisterFullError` を投げユーザーに登録解除を促す（暗黙 evict しない）。

## 6. エラーハンドリング

```python
class KabuApiError(Exception): ...
class KabuTokenExpiredError(KabuApiError): ...   # Code=4001001 / 4001005
class KabuRateLimitError(KabuApiError): ...      # Code=4002006
class KabuRegisterFullError(KabuApiError): ...   # Code=4002001 / 51銘柄目
class KabuConnectionError(KabuApiError): ...     # ConnectionRefusedError
```

`kabusapi_auth.check_response(payload, http_status)` が HTTP status + body `Code` の 2 段判定を行う。

## 7. テスト戦略

kabuステーション本体は Windows 限定で CI 不可 → `pytest-httpx` (HTTPXMock) + WebSocket mock のみ。

```python
@pytest.mark.demo_kabu
def test_xxx():
    ...
```

CI ジョブ: `.github/workflows/kabu-mock.yml` / job: `pytest-kabu-mock` / コマンド: `pytest -m demo_kabu python/tests/test_kabusapi_*.py`

本番 18080 を踏むテストは**禁止**（R1）。

## 8. Rust 側 capabilities キー

`Ready.capabilities.venue_capabilities["kabu_station"]`:

```json
{
  "requires_local_app": true,
  "max_push_symbols": 50,
  "supports_amend": false,
  "requires_trade_password_for_cancel": true,
  "is_production": false
}
```

`is_production` はデフォルト `false`（検証環境）。`KABU_ALLOW_PROD=1` + `KABU_ENV=prod` の二重設定時のみ `true` になる。
Rust 側 `KABU_IS_PRODUCTION` AtomicBool に反映され、UI の本番バナー描画に使われる（P4-4）。

数値 `50` の一次ソースは comparison.md §7 PUSH 配信。
`max_push_symbols` と `RegisterSet.MAX` は常に一致していなければならない
（`test_kabusapi_capabilities.py::test_capabilities_max_push_symbols_matches_register_set` で保証）。

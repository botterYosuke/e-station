---
name: kabuステーションＡＰＩ
description: 三菱UFJ eスマート証券（旧 auカブコム）kabuステーションAPI（v1.5）を使ったコーディング規約と運用クイックスタート。ローカル REST サーバ（localhost:18080 本番 / 18081 検証）への接続、トークン発行、X-API-KEY ヘッダ運用、PUSH 配信（WebSocket）、銘柄登録（50 銘柄上限）、流量制限、注文・余力・板情報の不変条件、kabuステーション本体プロセスの起動依存・OS 制約・ポート衝突の落とし穴を定義する。「kabu」「カブコム」「kabusapi」「kabuステーション」「auカブコム」「eスマート」と言われたら起動する。
---

# kabuステーションＡＰＩ スキル

> **状態（2026-05-07）**: kabu venue は **新規 Phase 0 計画フェーズ**。立花 venue（[tachibana SKILL.md](../tachibana/SKILL.md)）と同じ Python autonomous アーキテクチャに揃える。
>
> - **API 仕様（REST エンドポイント・PUSH WebSocket・流量制限・symbol/exchange コード体系）は一次資料として信頼してよい**
> - **Rust 側 venue adapter は新設しない**（Python 集約方針、立花と同じ）。すべての I/O は `python/engine/exchanges/kabusapi*.py` に閉じる
> - **Rust 側に追加されるのは下記のみ**:
>   - `engine-client/src/dto.rs` — `RequestVenueLogin` / `VenueReady` / `VenueError` / `VenueLoginStarted` / `VenueLoginCancelled` の **`Venue::KabuStation` 拡張**（既存 enum にバリアント追加するだけ）
>   - `exchange/src/adapter.rs` — `Venue::KabuStation` / `Exchange::KabuStation*` 列挙子
> - **ログイン UI は Python tkinter ヘルパー subprocess で開く**（tachibana と同じ）。Rust 側にログイン画面コードを書かない
> - **env 名は venue prefix 付き**: `DEV_KABU_API_PASSWORD` / `DEV_KABU_PROD`（既定 `false` = 検証 18081 を叩く、本番接続には `KABU_ALLOW_PROD=1` 併用）。読むのは Python 側 `kabusapi_login_flow` のみ
> - **kabuステーション本体（Windows GUI アプリ）の起動が前提**。本体プロセスが落ちている / ログアウト状態だと REST も WebSocket も応答しない（R1 参照）

flowsurface kabu venue 統合は **Python 側 `python/engine/exchanges/kabusapi*.py` にロジックを集約**する。Rust 側はチャート描画・IPC ライフサイクルに責務を絞る。本スキルは Claude が API 仕様に正しく沿って Python / IPC コードを書くためのルール集である。

> **以降、本ファイル本文中で「実装」と書かれた Python ヘルパーへの言及は、特記がない限り**「将来実装予定（Phase 1〜3 で Python 側に新設）」**と読み替えること**。

## 参照リソース

- **公式マニュアル（必読の一次資料）**
  - OpenAPI 仕様書: [reference/kabu_STATION_API.yaml](reference/kabu_STATION_API.yaml) — REST 全エンドポイント・request/response スキーマ・errno 一覧の単一ソース
  - レンダリング済み HTML: [reference/index.html](reference/index.html)（ReDoc 版、ブラウザで開くと検索可）
  - ポータル一式: [ptal/](ptal/) 配下
    - [ptal/index.html](ptal/index.html) — 概要・サービス案内
    - [ptal/guide.html](ptal/guide.html) — 利用ガイド（事前申込・有効化手順）
    - [ptal/howto.html](ptal/howto.html) — 接続方法・トークン取得フロー
    - [ptal/push.html](ptal/push.html) — PUSH 配信（WebSocket）仕様
    - [ptal/error.html](ptal/error.html) — エラーコード一覧
    - [ptal/faq.html](ptal/faq.html) — よくある質問（kabuステーション本体ログイン要件など）
    - [ptal/time-zone-setting.html](ptal/time-zone-setting.html) — Windows タイムゾーン要件
    - [ptal/add-in.html](ptal/add-in.html) — Excel アドイン
- **言語別サンプル（1 ファイル = 1 エンドポイント）**: [sample/Python/](sample/Python/)
  - 認証: [`kabusapi_token.py`](sample/Python/kabusapi_token.py)
  - 板/時価: [`kabusapi_board.py`](sample/Python/kabusapi_board.py) / [`kabusapi_symbol.py`](sample/Python/kabusapi_symbol.py)
  - 発注（現物）: [`kabusapi_sendorder_cash_buy.py`](sample/Python/kabusapi_sendorder_cash_buy.py) / [`kabusapi_sendorder_cash_sell.py`](sample/Python/kabusapi_sendorder_cash_sell.py)
  - 発注（信用）: [`kabusapi_sendorder_margin_new.py`](sample/Python/kabusapi_sendorder_margin_new.py) / [`kabusapi_sendorder_margin_daytrade.py`](sample/Python/kabusapi_sendorder_margin_daytrade.py) / [`kabusapi_sendorder_margin_pay_ClosePositionOrder.py`](sample/Python/kabusapi_sendorder_margin_pay_ClosePositionOrder.py) / [`kabusapi_sendorder_margin_pay_ClosePositions.py`](sample/Python/kabusapi_sendorder_margin_pay_ClosePositions.py)
  - 発注（先物/OP）: [`kabusapi_sendorder_future_new.py`](sample/Python/kabusapi_sendorder_future_new.py) / [`kabusapi_sendorder_option_new.py`](sample/Python/kabusapi_sendorder_option_new.py) ほか
  - 訂正/取消: [`kabusapi_cancelorder.py`](sample/Python/kabusapi_cancelorder.py)
  - 余力: [`kabusapi_cash.py`](sample/Python/kabusapi_cash.py)（現物） / [`kabusapi_margin.py`](sample/Python/kabusapi_margin.py)（信用） / [`kabusapi_wallet_future.py`](sample/Python/kabusapi_wallet_future.py) / [`kabusapi_wallet_option.py`](sample/Python/kabusapi_wallet_option.py)
  - 注文/残高照会: [`kabusapi_orders.py`](sample/Python/kabusapi_orders.py) / [`kabusapi_positions.py`](sample/Python/kabusapi_positions.py)
  - 銘柄登録（PUSH 対象）: [`kabusapi_register.py`](sample/Python/kabusapi_register.py) / [`kabusapi_unregister.py`](sample/Python/kabusapi_unregister.py) / [`kabusapi_unregisterall.py`](sample/Python/kabusapi_unregisterall.py)
  - PUSH 受信: [`kabusapi_websocket.py`](sample/Python/kabusapi_websocket.py)
  - 銘柄コード採番: [`kabusapi_symbolname_future.py`](sample/Python/kabusapi_symbolname_future.py) / [`kabusapi_symbolname_option.py`](sample/Python/kabusapi_symbolname_option.py) / [`kabusapi_symbolname_minioptionweekly.py`](sample/Python/kabusapi_symbolname_minioptionweekly.py)
  - その他: ranking / exchange / regulations / primaryexchange / apisoftlimit / marginpremium
  - サンプル一覧と起動順は [sample/Python/00.Readme.txt](sample/Python/00.Readme.txt)（Shift-JIS）参照
- 他言語サンプル: [sample/C#](sample/C%23/) / [sample/JavaScript](sample/JavaScript/) / [sample/ExcelMacro](sample/ExcelMacro/)（参考、Python 集約方針なので原則使わない）
- Excel アドイン: [ExcelAddin/](ExcelAddin/)（参考、コードからは触らない）

**原則**: OpenAPI 仕様書（`kabu_STATION_API.yaml`）が最優先。Python サンプルはマニュアル記載のパラメータを動作コードで示す参考実装。矛盾があれば OpenAPI に従う。新規エンドポイントを追加する際は YAML を grep してスキーマを確定させる。

---

## kabu venue 利用の前提条件

kabuステーション API を使うには以下が**すべて**必要:

1. **三菱UFJ eスマート証券（旧 auカブコム）の口座** — 株式・信用・先物 OP の取引区分は別途必要
2. **kabuステーション API オプションの利用申込** — 月額無料だが事前申込が必須（[ptal/guide.html](ptal/guide.html)）
3. **kabuステーション本体（Windows デスクトップアプリ）のインストールと起動** — REST/WebSocket サーバはこの本体プロセスが localhost:18080 / 18081 に立てる。本体が起動していないと TCP 接続自体が拒否される
4. **kabuステーション本体側で API 機能を有効化＆ AP IPassword を設定** — 設定 → API → 「APIを利用する」チェック＋「API パスワード」を任意で設定。これが `/token` リクエスト body の `APIPassword` になる
5. **OS は Windows 限定** — kabuステーション本体が Windows GUI アプリのため、Linux/Mac からは動かない（Wine 非対応）。CI で本物 API を叩く運用は不可。`pytest -m demo_kabu` は `httpx-mock` のみで構成する
6. **検証環境（18081）は別バイナリではなく同じ本体プロセスがポートで切り替える**。本番接続には別途 `KABU_ALLOW_PROD=1` env を要求し、Python URL builder が解禁する

---

## いつこのスキルを発動するか

- kabuステーション API に対する新規エンドポイント・新しいリクエスト型を追加するとき
- kabu Python モジュール（`kabusapi.py` / `kabusapi_auth.py` / `kabusapi_url.py` / `kabusapi_ws.py` / `kabusapi_orders.py` 等）のリクエスト/レスポンス型を追加・修正するとき
- WebSocket PUSH 配信パース（`kabusapi_ws.py`）を触るとき
- 注文入力・訂正・取消のパラメータを扱うとき
- API エラーコード（[ptal/error.html](ptal/error.html)）のハンドリングを設計するとき
- ユーザーが「kabu」「カブコム」「kabusapi」「kabuステーション」「auカブコム」「eスマート」に触れたとき
- flowsurface をローカルで起動して kabu セッションを必要とする検証を行うとき（下記「運用クイックスタート」を参照）

---

## 運用クイックスタート（ローカル起動で kabu セッションを作る）

E2E 検証で flowsurface を起動し、kabuステーション API を使いたい場合の手順。**コードを書く前にまずこの節を読むこと。**

### S1. kabuステーション本体を**先に**起動する

REST/WebSocket サーバは kabuステーション本体プロセスが立てる。flowsurface 起動より前に:

1. kabuステーション本体（Windows）を起動
2. ID/パスワード＋第二パスワードでログイン
3. 設定 → API → 「APIを利用する」が ✅ になっていることを確認
4. （初回のみ）「APIパスワード」を設定 — これを `.env` に書く

本体ログアウト時は API も即座に 401 を返すようになる。検証中に本体が早朝強制ログアウトされる仕様（[ptal/howto.html](ptal/howto.html)）に注意。**深夜回し E2E で「朝ログイン落ち」を踏むのは仕様**。

### S2. ビルドは **debug** を使う（release では自動ログイン不可）

`DEV_KABU_API_PASSWORD` / `DEV_KABU_PROD` による自動ログインは **Python 側 `python/engine/exchanges/kabusapi_login_flow.py`** が読む（**将来実装予定**）。Rust 側に env 取込みコードは追加しない（経路が Python に閉じる）。release では Python パスでガードし常にユーザー入力（tkinter ダイアログ）を要求する。

| ビルド | 自動ログイン | 本番/検証切替 | 用途 |
| :--- | :--- | :--- | :--- |
| `target/debug/flowsurface.exe` | ✅（Python が env 読込） | ✅（`DEV_KABU_PROD`） | E2E・検証・開発 |
| `target/release/flowsurface.exe` | ❌ | ❌ | 本番配布のみ |

### S3. `.env` は flowsurface 本体が**読まない**。シェル側で export する

立花と同じ。`set -a; source .env; set +a` でロードしてから `flowsurface.exe` を起動する。

`.env` の想定キー（debug 専用、Python 側 `kabusapi_login_flow.py` のみが読む）:

```
DEV_KABU_API_PASSWORD=...    # kabuステーション本体で設定した API パスワード
DEV_KABU_PROD=false          # 既定 false（検証 18081）。true で本番 18080
                             # ただし本番接続には KABU_ALLOW_PROD=1 を別途必要（R1）
```

**本番接続は `KABU_ALLOW_PROD=1` も同時設定したときのみ Python URL builder が解禁する**。`DEV_KABU_PROD=true` 単体では検証ポートに落とす（誤爆ガード）。

### S4. トークンキャッシュは**短命**

`/token` で発行した API トークンは「kabuステーション本体を終了/ログアウト」「別トークンが新規発行された時」に失効する（[ptal/howto.html](ptal/howto.html)）。**flowsurface 起動の都度 `/token` を叩いて取り直す**運用とし、ファイルキャッシュは作らない（立花とは違い JST 当日キャッシュは無効）。

### S5. ポート衝突（19876）と本体ポート（18080/18081）

flowsurface engine WS server は `:19876` を bind する（立花 SKILL S5 と同じ）。kabuステーション本体は `:18080`（本番）または `:18081`（検証）を bind する。**両方が同時に上がっていることが必須**。

```bash
netstat -ano | findstr 18080      # kabuステーション本体が LISTENING しているか
netstat -ano | findstr 19876      # flowsurface engine が LISTENING しているか
```

本体が落ちると `ConnectionRefusedError` が出る。これを「API パスワード間違い」と誤診断しない。

### S6. 起動時ログで拾うべきサイン

| ログ | 意味 | 対処 |
| :--- | :--- | :--- |
| `kabu /token: 200 OK, token=***` | 正常（トークン発行成功） | そのまま利用可 |
| `kabu /token: ConnectionRefusedError` | 本体が起動していない or ポート違い | S1 / S5 を確認 |
| `kabu /token: 401 4001003 invalid APIPassword` | API パスワード不一致 | 本体側設定を確認 |
| `kabu /token: 401 4001001 not logged in` | 本体がログアウト状態 | 本体に再ログイン |
| `kabu /sendorder: 4002006 流量制限` | 5 req/sec 超過（R5） | レート制限の実装を確認 |

---

## 絶対に守るべきルール

### R1. 本番環境では実弾が飛ぶ

- **本番 URL** `http://localhost:18080/kabusapi/` に接続すると、`/sendorder` 系は**実際に市場へ注文が出る**。約定は取り消せない
- **開発・テストは検証 URL** `http://localhost:18081/kabusapi/` を使う（kabuステーション本体側で「検証モード」起動が必要）
- **URL リテラルの所在は 1 箇所限定**: `BASE_URL_PROD` / `BASE_URL_VERIFY` を持てるのは **`python/engine/exchanges/kabusapi_url.py` の冒頭定義 1 箇所のみ**。Rust 側には URL リテラルを書かず、Python から venue 設定経由で受け取る
- Python 側のテストでは `BASE_URL_VERIFY` または `httpx_mock` で完結させる。本番ポートを叩くテストは禁止
- `localhost` 固定なので外部ネットワークには絶対に出ない。proxy 経由の発注も発生しない（kabuステーション本体が auカブコム のサーバへ取次ぐ）

### R2. REST は普通の JSON over HTTP（立花と違って独自形式ではない）

- マニュアル根拠: [`kabu_STATION_API.yaml`](reference/kabu_STATION_API.yaml) の `paths:` セクション
- すべての REST エンドポイントは `Content-Type: application/json` で **JSON body**（POST/PUT）または **クエリ文字列**（GET）。Python `httpx` / Rust `reqwest` の標準的な使い方で OK
- メソッドは仕様書通りに使い分ける:
  - `POST /token` — トークン発行
  - `POST /sendorder` / `POST /sendoco` — 新規発注
  - `PUT /cancelorder` — 取消
  - `PUT /register` / `PUT /unregister` — PUSH 銘柄登録/解除
  - `PUT /unregister/all` — 全銘柄登録解除
  - `GET /board/{symbol}@{exchange}` — 板情報（パスパラメータ）
  - `GET /symbol/{symbol}@{exchange}` — 銘柄情報
  - `GET /orders` / `GET /positions` — 一覧（クエリパラメータ）
  - `GET /wallet/cash` / `/wallet/margin` / `/wallet/future` / `/wallet/option` — 余力
  - `GET /ranking` / `/exchange/{symbol}` / `/regulations/{symbol}` / `/primaryexchange/{symbol}` / `/apisoftlimit` / `/margin/marginpremium/{symbol}`
- レスポンスも UTF-8 JSON（**Shift-JIS ではない** — 立花との最大の違い）

### R3. 認証は X-API-KEY ヘッダ（Bearer ではない）

1. 起動時に `POST /token` `{"APIPassword": "..."}` でトークン取得
2. 以降の全リクエストに `X-API-KEY: <取得したトークン>` ヘッダを付与
3. トークンが失効したら `4001001` / `4001005` 系エラーが返る → 再取得
4. **トークンはセッション秘密**。ログ出力時はマスクすること（`***` 表示、後半 4 文字のみ表示は許容）
5. 認証フローの実装は **Python 側 `python/engine/exchanges/kabusapi_auth.py`** で行う（**将来実装予定**）。失効時は自動再発行ポリシー（最大 1 回 retry、それでも失敗なら `KabuTokenExpiredError` を上層に投げる）

### R4. Symbol は `SymbolCode@Exchange` 複合キー

- 板情報など一部エンドポイントは URL パスに `5401@1` 形式（[`kabusapi_board.py`](sample/Python/kabusapi_board.py)）
- リクエスト body 内では `Symbol: "9433"` と `Exchange: 1` を別フィールドで持つ（[`kabusapi_sendorder_cash_buy.py`](sample/Python/kabusapi_sendorder_cash_buy.py)）
- `Exchange` の数値コード:
  - `1` = 東証 / `3` = 名証 / `5` = 福証 / `6` = 札証
  - `2` / `23` / `24` / `25` 等は先物・OP 系（OpenAPI の `Exchange` enum を参照）
- Python ヘルパー `kabusapi_symbol.compose_key(symbol, exchange) -> "9433@1"` を 1 箇所に集約し、文字列連結を散らさない

### R5. 流量制限を必ず尊重する

[OpenAPI tags](reference/kabu_STATION_API.yaml) 記載のとおり:

| カテゴリ | 制限 | 対象 |
| :--- | :--- | :--- |
| 発注系 | **5 req/sec** | `/sendorder` / `/sendoco` / `/cancelorder` |
| 余力系 | **10 req/sec** | `/wallet/*` |
| 情報系 | **10 req/sec** | `/board` / `/symbol` / `/orders` / `/positions` ほか |

**Python 側で token-bucket を実装し、超過する前に `await asyncio.sleep()` で待機する**（実装場所: `python/engine/exchanges/kabusapi_ratelimit.py`、**将来実装予定**）。サーバから `4002006` が返ってからのリトライは「最後の砦」とし、定常運転では事前抑制が原則。

### R6. PUSH 配信銘柄リストは 50 銘柄上限

- REST/PUSH を**合算**して 50 銘柄まで（OpenAPI `register` tag 説明参照）
- 51 銘柄目を `PUT /register` すると `4002001` 系エラー
- 銘柄入替えは `PUT /unregister` で枠を空けてから `PUT /register`、または `PUT /unregister/all` で全クリア
- **flowsurface 側で「現在登録中の銘柄セット」を Python に持たせ、超過する前に LRU で枠を作る**（実装場所: `kabusapi_register.py`、**将来実装予定**）
- 板情報 GET（`/board`）も内部的に自動登録を発火する点に注意（OpenAPI `info` tag 説明）。意図しない自動登録で枠を食いつぶすので、明示登録と GET の両方を集計する

### R7. エラーコードは `Code` フィールド + HTTP status の 2 段階

```
HTTP 4xx/5xx        → API レベル（接続/認証/流量）
レスポンス body Code → 業務処理エラー（パラメータ不正・残高不足など）
```

- エラー一覧は [ptal/error.html](ptal/error.html) を参照
- 代表例: `4001001` 未認証 / `4001003` API パスワード不一致 / `4001005` トークン期限切れ / `4002001` 銘柄登録上限超過 / `4002006` 流量制限超過 / `4002008` 銘柄未登録
- 既存ヘルパー `kabusapi_auth.check_response(payload, http_status)` は HTTP status と body `Code` を両方読む。`Code == 0` または非存在で正常、それ以外は `KabuApiError { code, message }` を投げる
- `4001005` は自動 retry（R3）、`4002006` は backoff retry（R5）。それ以外は上層へ伝播

### R8. WebSocket PUSH は単一エンドポイント・受信専用

- URL: `ws://localhost:18080/kabusapi/websocket`（本番） / `ws://localhost:18081/kabusapi/websocket`（検証）
- 認証ヘッダ不要（kabuステーション本体ログイン状態が前提）
- サンプル: [`kabusapi_websocket.py`](sample/Python/kabusapi_websocket.py)
- 配信内容: `PUT /register` で登録した銘柄の時価更新を JSON で push（板気配・歩み値・各値）
- **メッセージ送信は不要**（クライアントから何も送らない）。サーバから一方向に flow し、`on_message` で JSON parse する
- 受信ボディは UTF-8 JSON（立花の `^A^B^C` 区切りバイナリとは違う）
- **再接続はクライアント側責務**。切断検知後 exponential backoff で再接続する
- **keepalive ping は無効化する**: kabuStation は RFC 6455 準拠の PONG を返さない（空 PONG を返す）。`ping_interval=None` を必ず指定し、`asyncio.wait_for(ws.recv(), timeout=3600)` で無メッセージハングを検出して再接続する（Issue #40）
- 実装は **Python 側 `python/engine/exchanges/kabusapi_ws.py`**（**将来実装予定**）に集約。Rust 側で kabu WebSocket を直接張らない

### R9. 価格は `float`、数量は `int`

- リクエスト body `Price` は `float`（指値、例 `2762.5`）。半端な小数はサーバ側で呼値単位に丸められる（はず）が、クライアント側で呼値マスタを持って事前丸めするのが安全
- `Qty` は `int`（単元株数の倍数、例 `100`）
- `Symbol` は文字列（4-9 桁、優先株・先物 OP は 9 桁、銘柄コード命名規則は [`kabusapi_symbolname_*.py`](sample/Python/) シリーズ参照）
- `OrderID` はサーバ採番文字列（例 `"20200709A02N04712032"`、20 文字）。クライアントで生成しない

### R10. シークレットは**絶対に**ハードコードしない

- API パスワード・取得トークン・口座番号はすべて機密
- 運用時は `DEV_KABU_API_PASSWORD` env からロード、メモリのみ保持。ファイル永続化しない（トークンは短命、R3 / S4）
- `.env` は `.gitignore` に入れる
- `log::info!` / `print()` にトークン・パスワードを生で流さない（`***` または末尾 4 文字のみ）
- **サンプルファイル中の `'ed94b0d34f9441c3931621e55230e402'` 等のトークン値は公式サンプルのダミー**。本物のトークンを書いてコミットしない

---

## エンドポイント分類（OpenAPI tags ベース）

OpenAPI `tags:` セクションに対応。Claude が新しい機能を追加する際は、この表から該当 tag を選び、`reference/kabu_STATION_API.yaml` の `paths:` セクションを開いてリクエスト/レスポンススキーマを確定させる。

### auth — 認証
| Method | Path | 機能 |
| :--- | :--- | :--- |
| POST | `/token` | トークン発行 |

### order — 発注（5 req/sec）
| Method | Path | 機能 |
| :--- | :--- | :--- |
| POST | `/sendorder` | 株式・信用 注文（新規/返済） |
| POST | `/sendoco` | OCO 注文 |
| POST | `/sendorder/future` | 先物 注文 |
| POST | `/sendorder/option` | OP 注文 |
| PUT | `/cancelorder` | 取消（OrderID 指定） |

### wallet — 取引余力（10 req/sec）
| Method | Path | 機能 |
| :--- | :--- | :--- |
| GET | `/wallet/cash` | 現物余力 |
| GET | `/wallet/margin` | 信用余力 |
| GET | `/wallet/future` | 先物余力 |
| GET | `/wallet/option` | OP 余力 |

### info — 銘柄情報・照会（10 req/sec）
| Method | Path | 機能 |
| :--- | :--- | :--- |
| GET | `/board/{symbol}` | 板情報（symbol = `5401@1` 形式） |
| GET | `/symbol/{symbol}` | 銘柄情報 |
| GET | `/orders` | 注文約定照会 |
| GET | `/positions` | 残高照会 |
| GET | `/symbolname/future/{deriv_month}` | 先物銘柄コード採番 |
| GET | `/symbolname/option/{deriv_month}` | OP 銘柄コード採番 |
| GET | `/symbolname/minioption/weekly/{deriv_week_no}` | ミニ OP 週次 |
| GET | `/ranking` | 詳細ランキング |
| GET | `/exchange/{symbol}` | 為替情報 |
| GET | `/regulations/{symbol}` | 規制情報 |
| GET | `/primaryexchange/{symbol}` | 優先市場 |
| GET | `/apisoftlimit` | ソフトリミット |
| GET | `/margin/marginpremium/{symbol}` | プレミアム料 |

### register — PUSH 銘柄登録（50 銘柄上限、R6）
| Method | Path | 機能 |
| :--- | :--- | :--- |
| PUT | `/register` | 銘柄登録（PUSH 配信開始） |
| PUT | `/unregister` | 銘柄登録解除 |
| PUT | `/unregister/all` | 全銘柄登録解除 |

### push — 配信
| Protocol | URL | 機能 |
| :--- | :--- | :--- |
| WebSocket | `ws://localhost:18080/kabusapi/websocket` | 登録銘柄の時価 PUSH（R8） |

---

## 注文（/sendorder）パラメータの定石

OpenAPI 該当: `RequestSendOrderDerivFuture` / `RequestSendOrder` 等。Python サンプル [`kabusapi_sendorder_cash_buy.py`](sample/Python/kabusapi_sendorder_cash_buy.py) / [`kabusapi_sendorder_margin_new.py`](sample/Python/kabusapi_sendorder_margin_new.py) が現物・信用の引数組合せを示す。頻出フィールド:

| 項目 | 意味 | 代表値 |
| :--- | :--- | :--- |
| `Symbol` | 銘柄コード | 4 桁株式 / 9 桁先物 OP（例 `"9433"`） |
| `Exchange` | 市場コード | `1`=東証 / `3`=名証 / `5`=福証 / `6`=札証（先物 OP は別系統） |
| `SecurityType` | 商品種別 | `1`=株式（現物・信用とも） |
| `Side` | 売買区分 | `"1"`=売 / `"2"`=買（**文字列**であることに注意） |
| `CashMargin` | 現金/信用区分 | `1`=現物 / `2`=信用新規 / `3`=信用返済 |
| `MarginTradeType` | 信用取引区分 | `1`=制度 / `2`=一般長期 / `3`=一般デイトレ（信用時のみ） |
| `DelivType` | 受渡区分 | `1`=自動振替 / `2`=お預り金 / `3`=auじぶん銀行（現物時） / `0`=信用時 |
| `FundType` | 資金区分 | `'AA'`=信用代用 / `'11'`=信用取引 ほか |
| `AccountType` | 口座種別 | `2`=一般 / `4`=特定 / `12`=法人 |
| `Qty` | 数量 | 整数（単元株の倍数） |
| `FrontOrderType` | 執行条件 | `10`=成行 / `20`=指値 / `30`=逆指値 ほか（OpenAPI enum 全件あり） |
| `Price` | 注文価格 | float（成行時は `0`） |
| `ExpireDay` | 注文期限 | `0`=当日 / `YYYYMMDD` |
| `ReverseLimitOrder` | 逆指値詳細 | `FrontOrderType=30` 時に必須。`TriggerSec` / `TriggerPrice` / `UnderOver` / `AfterHitOrderType` / `AfterHitPrice` を含むオブジェクト |

**出力**: `Result`（0=正常）/ `OrderId`（成功時、訂正・取消の入力に使う）/ エラー時は `Code` / `Message`

**取消**: `PUT /cancelorder` body `{"OrderID": "20200709A02N04712032", "Password": "..."}` — Password は本体取引パスワード（API パスワードとは別）

**訂正**: kabuステーションAPI には**訂正専用エンドポイントが無い**。「取消 → 新規発注」のシーケンスで実現する。立花とは違うので注意。

---

## PUSH（WebSocket）配信のパース規約

### メッセージ形式

UTF-8 JSON、1 メッセージ = 1 銘柄スナップショット（OpenAPI `PushBoardSuccess` 参照）。

```json
{
  "Symbol": "5401",
  "SymbolName": "新日鐵住金",
  "Exchange": 1,
  "ExchangeName": "東証１部",
  "CurrentPrice": 2479.0,
  "CurrentPriceTime": "2020-07-22T15:00:00+09:00",
  "BidPrice": 2479.0, "BidQty": 100,
  "AskPrice": 2480.0, "AskQty": 200,
  "Sell1": {"Price": 2480.0, "Qty": 200, "Time": "...", "Sign": "0101"},
  ...
  "Buy1": {...},
  "VWAP": 2470.5, "TradingVolume": 12345600
}
```

### 受信ループの不変条件

- メッセージは `\n` 区切りではなく **WebSocket frame 単位で 1 JSON**。`json.loads(message)` で 1 発デシリアライズ
- 受信 throughput は登録銘柄数と相場速度に比例。50 銘柄全部に活発な板更新があるとピーク数百 msg/sec を覚悟
- **keepalive ping は `ping_interval=None` で無効化する**。kabuStation は RFC 6455 準拠の PONG を返さない（PING payload と不一致の空 PONG を返す）ため、`ping_interval` を設定すると 30 秒ごとに timeout 切断ループが発生する（Issue #40）。`asyncio.wait_for(ws.recv(), timeout=3600)` で無メッセージハングを検出して再接続する。立花のような手動 pong も不要
- 切断後の再接続: `PUT /register` した銘柄リストはサーバ側で保持されないので、**再接続後に再登録が必要**かどうか OpenAPI を再確認（[ptal/push.html](ptal/push.html) の「再接続時の挙動」セクション参照、実装時に検証する）

---

## Python 実装ヘルパー（**将来実装予定（Phase 1〜3 で新設）**、`python/engine/exchanges/kabusapi*.py`）

kabu venue の I/O は **Python 側に集約**される。新しいエンドポイントを追加する際は下記の Python ヘルパーを踏襲する。Rust 側に同等ヘルパーを実装してはいけない。

- `kabusapi_url.base_url(env: Literal["prod", "verify"]) -> str` — `BASE_URL_PROD` / `BASE_URL_VERIFY` の唯一の所在地（R1）
- `kabusapi_url.endpoint(path: str, *, env) -> str` — `{base}/kabusapi/{path}` 組立て
- `kabusapi_url.symbol_key(symbol: str, exchange: int) -> str` — `"5401@1"` 形式（R4）
- `kabusapi_auth.fetch_token(api_password: str, *, env) -> str` — `POST /token` ラッパー、メモリ保持のみ（R3 / R10）
- `kabusapi_auth.check_response(payload, http_status) -> None` — `Code`/`Message` 二段判定（R7）。失敗時は `KabuApiError` 派生例外を投げる
- `kabusapi_ratelimit.OrderBucket() / WalletBucket() / InfoBucket()` — token-bucket、`async with bucket:` で取得（R5）
- `kabusapi_register.RegisterSet` — 50 銘柄上限を Python 側で追跡し、超過前に LRU で `unregister`（R6）
- `kabusapi_ws.connect(env, on_message)` — `ping_interval=None` + `asyncio.wait_for(ws.recv(), 3600s)` で接続し、再接続時に `RegisterSet` から再登録（R8、Issue #40）
- エラー型: `KabuApiError` / `KabuTokenExpiredError` / `KabuRateLimitError` / `KabuRegisterFullError` / `KabuConnectionError`（kabuステーション本体落ち）
- テストは `pytest-httpx` の `HTTPXMock` でモック（既存 [`python/tests/test_binance_rest.py`](../../../python/tests/test_binance_rest.py) パターン）。本番 18080 を絶対に踏まない（R1）

### Rust 側に新設されるもの

- `engine_client::dto::Venue::KabuStation` バリアント追加（`RequestVenueLogin` / `VenueReady` / `VenueError` / `VenueLoginStarted` / `VenueLoginCancelled` の payload に流す）
- `engine_client::capabilities` の `venue_capabilities["kabu"]` キー定義
- `exchange::adapter::Venue::KabuStation` / `Exchange::KabuStation*` 列挙子（東証/名証/福証/札証＋先物 OP 系）

---

## 立花 venue との設計差異まとめ

新規実装時に「立花でこうだから kabu でも」と推論すると外す箇所:

| 項目 | 立花 | kabuステーション |
| :--- | :--- | :--- |
| 接続先 | リモート（`kabuka.e-shiten.jp`） | **localhost ローカルサーバ**（本体プロセスが立てる） |
| OS | クロスプラットフォーム | **Windows 限定**（本体が Win GUI） |
| URL 形式 | `{virtual_url}?{JSON文字列}` 独自形式 | 普通の REST + JSON body |
| 認証 | ID/PW + 第二暗証番号、仮想 URL 5 種取得 | API パスワード → トークン 1 個、X-API-KEY ヘッダ |
| エンコーディング | Shift-JIS | UTF-8 |
| セッション寿命 | JST 当日（夜間閉局でリセット）、ファイルキャッシュ可 | 本体終了/ログアウトで失効、**キャッシュ不要** |
| 第二暗証番号 | 発注時 iced modal で都度取得 | 取消時 `Password` フィールド（取引パスワード） |
| PUSH 区切り | `^A^B^C` ASCII 制御文字 + Shift-JIS | WebSocket frame = 1 JSON、UTF-8 |
| WebSocket ping | 手動 pong 必須 | **`ping_interval=None` で無効化**（kabuStation が RFC 6455 非準拠の空 PONG を返すため）+ `asyncio.wait_for` でハング検出 |
| 訂正 API | あり（`CLMKabuCorrectOrder`） | **無し**（取消→再発注） |
| 流量制限 | 明示記載なし（実測で TPS 制限あり） | **明示**（発注 5/s、余力 10/s、情報 10/s） |
| 銘柄登録 | 不要（任意の銘柄を直接照会） | **50 銘柄上限**の登録制 |
| マスタ | 21MB ダウンロード | API で都度取得（`/symbol/{key}`） |

**この差異表は実装時の sanity check に使う**。立花テンプレートをコピペで持ち込む前に、該当行が違っていないか確認する。

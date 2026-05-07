# 立花 venue ↔ kabu venue 対比

立花 SKILL（[/.claude/skills/tachibana/SKILL.md](../../../.claude/skills/tachibana/SKILL.md)）と kabu SKILL（[/.claude/skills/kabusapi/SKILL.md](../../../.claude/skills/kabusapi/SKILL.md)）を突き合わせ、新規実装時に「立花でこうだから kabu でも」と推論して外す箇所を全部表に落とす。

## 1. プロトコル・接続レイヤー

| 項目 | 立花 e支店 (v4r8) | kabuステーション (v1.5) |
| :--- | :--- | :--- |
| 接続先 | リモート HTTPS (`kabuka.e-shiten.jp` / `demo-kabuka.e-shiten.jp`) | **localhost ローカルサーバ** (`localhost:18080` 本番 / `localhost:18081` 検証) |
| 動作 OS | クロスプラットフォーム | **Windows 限定**（本体が Win GUI アプリ） |
| 本体プロセス依存 | 無し（リモート API） | **kabuステーション本体起動が必須**。落ちると TCP 拒否 |
| URL 形式（REQUEST 系） | 独自 `{virtual_url}?{JSON 文字列}`（key=value ではない、R2） | 普通の REST + JSON body / クエリ |
| URL 形式（PUSH 系） | `{sUrlEvent}?key=value&...` 例外形式 | `ws://localhost:.../kabusapi/websocket`（クエリなし） |
| URL リテラル所在 | `tachibana_url.py` 冒頭 1 箇所（F-L1） | `kabusapi_url.py` 冒頭 1 箇所（同方針） |
| エンコーディング | **Shift-JIS**（リクエスト・レスポンスとも） | **UTF-8**[^utf8-sjis-regression] |
| URL エスケープ | 30 文字独自パーセントエンコード（R9）。`reqwest::query()` 使用禁止 | 標準 URL エスケープで OK |

[^utf8-sjis-regression]: Shift-JIS 取り違え regression test を `test_kabusapi_codec.py::test_decode_rejects_sjis_bytes` で押さえる。

## 2. 認証・セッション

| 項目 | 立花 | kabu |
| :--- | :--- | :--- |
| ログイン入力 | `sUserId` + `sPassword` + 電話認証（手動・前提） | API パスワード単独（kabuステーション本体ログインは別途、ユーザー手動） |
| 第二暗証番号 | 発注時 iced modal で都度取得（F-H5、Phase 1 では収集すらしない） | **取消時のみ** `Password` フィールドで送信（取引パスワード、API パスワードとは別） |
| 認証応答 | 仮想 URL 5 種（`sUrlRequest` / `sUrlMaster` / `sUrlPrice` / `sUrlEvent` / `sUrlEventWebSocket`） | API トークン 1 個（文字列） |
| 後続リクエスト | 仮想 URL に直接アクセス（ヘッダ無し） | 全リクエストに `X-API-KEY: <token>` ヘッダ |
| セッション寿命 | JST 当日（夜間閉局でリセット） | 本体終了 / ログアウト / 別トークン発行で失効 |
| ファイルキャッシュ | `tachibana_session.json` に JST 当日付で保存・翌日まで再利用 | **作らない**（短命のため起動毎に `/token` を取り直す） |
| 失効時の挙動 | `p_errno="2"` → `VenueError{code:"session_expired"}` → ユーザー再ログイン誘導（自動再ログイン禁止） | `4001001` / `4001005` 検出時は `/token` 再取得を **1 回 retry**（メモリ保持の API パスワードのみ使用、ユーザー入力なし）。retry 失敗で `VenueError{code:"token_expired"}` を発火し tkinter 再ログインへ誘導（**ユーザー入力を伴う再ログインは自動化しない**、plan §1.3 U16 参照、U31） |
| keyring 使用 | 不使用（ファイルキャッシュ + メモリのみ） | 不使用（メモリのみ、ファイルにも書かない） |

## 3. エラーハンドリング

| 項目 | 立花 | kabu |
| :--- | :--- | :--- |
| 判定段階 | **2 段階**: `p_errno`（API 共通）→ `sResultCode`（業務） | **2 段階**: HTTP status → body `Code`（SKILL R7） |
| 正常判定 | `p_errno ∈ {"0", ""}` かつ `sResultCode == "0"` | HTTP 2xx かつ (`Code == 0` or `Code` 不在) |
| セッション失効コード | `p_errno == "2"` | `4001001` / `4001005` |
| 流量超過コード | （明示記載なし、実測） | `4002006` |
| 未認証コード | `sKinsyouhouMidokuFlg == "1"`（未読通知あり） | `4001003`（API パスワード不一致） |
| エラー一覧資料 | `mfds_json_api_ref_text.html#sResultCode` (`ComT7`) | `ptal/error.html` |

## 4. リクエスト I/O 規約

| 項目 | 立花 | kabu |
| :--- | :--- | :--- |
| 通番 | `p_no`（単調増加 AtomicU64、必須） | 不要（HTTP リクエスト単位、サーバ採番） |
| 送信時刻 | `p_sd_date` JST `YYYY.MM.DD-hh:mm:ss.sss`（必須） | 不要（HTTP date ヘッダで十分） |
| レスポンス整形フラグ | `sJsonOfmt="5"`（マスタは `"4"`）必須 | 不要 |
| 空配列の表現 | `""`（空文字列）。`deserialize_tachibana_list` で `[]` 正規化 | 普通の `[]` |
| 数値の型ゆれ | 全て文字列（例 `sOrderSuryou: "100"`） | 整数/浮動小数を JSON 型通りに使う（`Qty: 100`, `Price: 2762.5`） |
| 売買区分の表現 | `sBaibaiKubun`: `"1"`売 / `"3"`買 | `Side`: `"1"`売 / `"2"`買（**文字列だが値が違う**）[^side-regression] |

[^side-regression]: 立花 `1売/3買` と kabu `1売/2買` の値衝突に対し、コード生成時の regression test を必ず置く。テスト関数: `test_kabusapi_codec.py::test_side_mapping_kabu_buy_is_2_not_3`。

## 5. 銘柄・市場コード

| 項目 | 立花 | kabu |
| :--- | :--- | :--- |
| 銘柄キー | `sIssueCode` 単独（4-5 桁） | `Symbol` + `Exchange` 複合（`"5401@1"` パス形式・body 別フィールド形式の 2 形態） |
| 東証コード | `sSizyouC = "00"` | `Exchange = 1` |
| 名証/福証/札証 | 取り扱い未確認（東証のみ実運用） | `3` / `5` / `6` |
| 銘柄マスタ | `CLMEventDownload` で 21MB 一括 DL（ストリーム、`sJsonOfmt="4"`） | `/symbol/{key}` で都度取得（事前 DL なし）[^no-master-module] |
| 呼値マスタ | マスタ DL 内 `2-12 呼値` テーブル | クライアント側で持たない（サーバ側丸めに依存） |

[^no-master-module]: `kabusapi_master.py` は作らない（[plan.md §1.2 Python 側（**新規モジュール群**）](./plan.md#12-python-側新規モジュール群) 参照）。

## 6. 注文 API

| 項目 | 立花 | kabu |
| :--- | :--- | :--- |
| 新規注文 | `CLMKabuNewOrder`（現物・信用・買売・成行/指値/逆指値を 1 エンドポイントで） | `POST /sendorder`（株式・信用） / `POST /sendorder/future` / `POST /sendorder/option` で**商品別** |
| OCO | 無し | `POST /sendoco` |
| 訂正 | **`CLMKabuCorrectOrder` あり**（`sOrderPrice` / `sCondition` / `sOrderSuryou` / `sOrderExpireDay` 限定） | **無し**（取消 → 再発注で代替） |
| 取消 | `CLMKabuCancelOrder`（個別） / `CLMKabuCancelOrderAll`（一括） | `PUT /cancelorder`（個別のみ。一括 API 無し） |
| 取消時の追加認証 | 第二暗証番号 `sSecondPassword` | 取引パスワード `Password`（API パスワードとは別物） |
| 注文番号 | `sOrderNumber`（サーバ採番文字列） | `OrderID`（サーバ採番、20 文字、例 `"20200709A02N04712032"`） |
| 信用区分 | `sGenkinShinyouKubun` 単一フィールド（`0`現物 / `2`制度新規 6m / etc.） | `CashMargin` + `MarginTradeType` の 2 フィールド |
| 受渡区分 | （マニュアル参照、現物時のみ意味） | `DelivType` 必須（`1`/`2`/`3`/`0`） |
| 譲渡益課税区分 | `sZyoutoekiKazeiC` ログイン応答を流用 | `AccountType`（`2`一般/`4`特定/`12`法人） |
| 流量制限（明示） | 記載なし（実測で TPS 制限あり） | 発注 **5 req/sec** 明示 |

## 7. PUSH 配信（時価ストリーム）

> **本節は PUSH 50 銘柄上限の数値の一次ソース**（U38）。README / plan / SKILL からの「50 銘柄」記述はすべて本節（特に下表「銘柄登録 API」行）にリンク集約する。

| 項目 | 立花 EVENT I/F | kabu PUSH |
| :--- | :--- | :--- |
| プロトコル | HTTP long-poll または WebSocket（`sUrlEventWebSocket`） | WebSocket のみ |
| 認証 | URL 自体がセッション秘密 | 不要（本体ログイン状態が前提） |
| 銘柄登録 API | 不要（URL クエリ `p_issue_code` で都度指定） | **`PUT /register` で事前登録必須**、**50 銘柄上限（一次ソース・U38）**、解除は `/unregister` / `/unregister/all` |
| メッセージ形式 | `^A`（項目区切り）`^B`（名値区切り）`^C`（複数値区切り）+ Shift-JIS | **WebSocket frame = 1 JSON（UTF-8）**。`json.loads` 一発 |
| キー形式 | `<型>_<行番号>_<情報コード>`（例 `p_1_DPP`） | OpenAPI `PushBoardSuccess` のフィールド名（`CurrentPrice`, `BidPrice`, `Sell1` 等） |
| メッセージ区切り | `\n` または `^A` 終端、受信バッファで分割必要 | WebSocket frame 単位（自動分割） |
| 配信種別指定 | `p_evt_cmd=ST,KP,EC,SS,US,FD` クエリ（複数同時購読） | 種別指定なし（登録銘柄全部の時価が来る） |
| ping/pong | **手動 pong 必須**（受信した ping に手動応答、`websockets` の自動 ping は無効化） | library 任せ（`ping_interval=20, ping_timeout=10`） |
| 再接続後の状態復元 | 仮想 URL に再接続するだけ | **再接続後は `PUT /register` を常に再実行する（サーバ側保持に依存しない）**。詳細は [plan.md §4 リスクと未確定事項 Q-K1](./plan.md#4-リスクと未確定事項) 参照 |
| 流量 | 銘柄数次第。FD 12 秒無通信で再接続 | 50 銘柄活況時にピーク数百 msg/sec |

## 8. 余力・残高

| 項目 | 立花 | kabu |
| :--- | :--- | :--- |
| 現物余力 | `CLMZanKaiKanougaku` | `GET /wallet/cash` |
| 信用余力 | `CLMZanShinkiKanoIjiritu` | `GET /wallet/margin` |
| 先物余力 | （現状未対応） | `GET /wallet/future` |
| OP 余力 | （現状未対応） | `GET /wallet/option` |
| 注文一覧 | `CLMOrderList` / `CLMOrderListDetail` | `GET /orders` |
| 残高 | `CLMGenbutuKabuList` / `CLMShinyouTategyokuList` | `GET /positions` |
| 流量制限（明示） | 記載なし | **10 req/sec**（発注の倍） |

## 9. ローカル運用・デバッグ

| 項目 | 立花 | kabu |
| :--- | :--- | :--- |
| debug 自動ログイン env | `DEV_TACHIBANA_USER_ID` / `DEV_TACHIBANA_PASSWORD` / `DEV_TACHIBANA_DEMO`（既定 `true`） | `DEV_KABU_API_PASSWORD` / `DEV_KABU_PROD`（既定 `false` = 検証 18081） |
| 本番ガード env | `TACHIBANA_ALLOW_PROD=1` 併用必須 | `KABU_ALLOW_PROD=1` 併用必須 |
| .env ロード | flowsurface 本体は `dotenv` 不使用、シェル側 export | 同左 |
| release ビルド | env を読まない（Python 側でガード） | 同左 |
| ファイルキャッシュ | `tachibana_session.json`（JST 当日） | **作らない** |
| ポート衝突確認 | engine が確保するポート（`engine-client` start-or-attach パターン） | engine が確保するポート（`engine-client` start-or-attach パターン）+ `:18080` or `:18081`（kabu 本体）の**両方** |
| CI でのデモテスト | `workflow_dispatch` 限定（閉局帯偽陰性回避） | **不可**（Win GUI 本体プロセス依存）。`HTTPXMock` のみ |

## 10. 用語・概念マッピング（コード命名の指針）

| 抽象概念 | 立花用語 | kabu 用語 | 推奨命名（共通レイヤー） |
| :--- | :--- | :--- | :--- |
| venue 識別子 | `Venue::Tachibana` | `Venue::KabuStation` | （Rust enum） |
| ログインリクエスト | `CLMAuthLoginRequest` | `POST /token` | `login_flow.startup_login()` |
| セッション秘密 | 仮想 URL 5 種 | API トークン 1 個 | `Session.credentials`（venue 別実体） |
| 板スナップショット | FD frame parse | `PushBoardSuccess` JSON | `DepthSnapshot`（IPC 既存型流用） |
| 約定通知 | EC frame | `/orders` polling or PUSH（？要検証） | `OrderUpdate` |
| 銘柄キー（IPC） | `sIssueCode` | `Symbol@Exchange` | `Ticker`（既存）+ Python 側で venue 固有変換 |
| IPC ライフサイクル DTO（5 種） | `RequestVenueLogin` / `VenueLoginStarted` / `VenueLoginCancelled` / `VenueReady` / `VenueError`（`venue="tachibana"`） | 同 5 種を `venue="kabu_station"` で受理 | DTO 型変更なし、`venue` 文字列のみで分岐 |
| capabilities shape | （tachibana 別途定義） | `requires_local_app=true` / `max_push_symbols=50` / `supports_amend=false` / `requires_trade_password_for_cancel=true` | `Ready.capabilities.venue_capabilities["kabu_station"]`（plan §1.1 参照） |

## 設計差異の sanity check 表（実装時に開く）

新規エンドポイント追加・パース実装時、立花テンプレを kabu に持ち込む前に下記を必ずチェック:

1. ✅ URL リテラルは `kabusapi_url.py` だけにあるか
2. ✅ レスポンスは UTF-8 として読んでいるか（Shift-JIS 関数を呼んでいないか）
3. ✅ `X-API-KEY` ヘッダを忘れていないか（立花の仮想 URL 流儀でヘッダ無しになっていないか）
4. ✅ 数値型は JSON のまま使っているか（文字列に変換していないか）
5. ✅ 流量制限 bucket を通しているか（`OrderBucket` / `WalletBucket` / `InfoBucket`）
6. ✅ PUSH 銘柄登録の枠管理を `RegisterSet` に通しているか（生 `PUT /register` してないか）
7. ✅ 訂正処理を「取消 → 再発注」シーケンスで書いているか（訂正 API を探そうとしていないか）
8. ✅ ファイルキャッシュを作っていないか（kabu はトークン短命）
9. ✅ Rust 側に kabu 固有 URL / パスワード / トークンを書いていないか
10. ✅ tkinter ログインダイアログのフィールド・ラベルを Rust 側に書いていないか

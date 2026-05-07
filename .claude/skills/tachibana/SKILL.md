---
name: tachibana-e-api
description: 立花証券 e支店 API（v4r7/v4r8、tachibana）でコードを書く・運用するときの必読スキル。「立花」「e支店」「ｅ支店」「tachibana」「kabuka.e-shiten.jp」「demo-kabuka」「sUrlRequest」「sUrlEvent」「CLMAuthLoginRequest」「CLMKabuNewOrder」「sCLMID」「sResultCode」「p_errno」「sJsonOfmt」に触れたら必ず起動する。CLMAuthLoginRequest によるログイン、仮想 URL（sUrlRequest/Master/Price/Event/EventWebSocket）の取り扱い、`{virtual_url}?{JSON文字列}` 独自形式、p_no/p_sd_date/sJsonOfmt の必須化、p_errno と sResultCode の二段判定、Shift-JIS、空配列が "" で返る件、第二暗証番号の必須化、CLMKabuNewOrder のパラメータ、EVENT/WebSocket の ^A^B^C 区切り、CLMEventDownload マスタの特殊フロー、flowsurface ローカル起動時の debug/release・.env・セッションキャッシュ・ポート衝突の落とし穴を扱う。
---

# 立花証券・ｅ支店・ＡＰＩ スキル

立花 venue 統合は **Python 側 `python/engine/exchanges/tachibana*.py` にロジックを集約**する。Rust 側はチャート描画と IPC ライフサイクルイベントに責務を絞り、立花プロトコル固有のヘルパー（URL ビルド・Shift-JIS デコード・p_no 採番・WS 接続）は Rust に書かない（[architecture.md §1](../../../docs/✅tachibana/architecture.md)）。本スキルは Claude が API 仕様に正しく沿って Python / IPC コードを書くためのルール集である。

> **最新の進行ステータス・タスク完了状況は [docs/✅tachibana/implementation-plan.md](../../../docs/✅tachibana/implementation-plan.md) を参照**。本ファイルには腐りやすいマイルストン情報を書かない（不変ルールのみを書く）。

## 参照リソース

- **公式マニュアル（必読の一次資料）**
  - HTML リファレンス: [manual_files/mfds_json_api_ref_text.html](manual_files/mfds_json_api_ref_text.html)
    - `ComT1..ComT7` の章立てで共通説明・認証機能・業務（REQUEST）・マスタ・時価・EVENT・結果コード表を網羅
    - 共通説明は `ComP1..ComP7`（専用 URL・インタフェース概要・ブラウザ利用・共通項目/認証・マスタ・EXCEL VBA）
    - sCLMID の章タイトルがそのまま HTML の `id` 属性になっている（例: `#CLMKabuNewOrder`）。Claude は該当 `id` セクションを開いて仕様確認する
  - 同梱 PDF / Excel（`manual_files/` 配下に実ファイルあり）:
    - [api_request_if_v4r7.pdf](manual_files/api_request_if_v4r7.pdf) — REQUEST I/F 利用方法・データ仕様
    - [api_request_if_master_v4r5.pdf](manual_files/api_request_if_master_v4r5.pdf) — マスタデータ利用方法
    - [api_web_access.xlsx](manual_files/api_web_access.xlsx) — ブラウザからの動作確認例
  - 外部参照のみ（`manual_files/` には同梱されていない）:
    - `api_overview_v4r7.pdf` — インタフェース概要（ComP2 からリンク）
    - `api_event_if_v4r7.pdf` / `api_event_if.xlsx` — EVENT I/F 利用方法・データ仕様（ComT6 からリンク、同内容の PDF/Excel 版）
    - これら外部資料を参照する場合はブラウザ側で e-shiten.jp の公開 URL を確認する。ローカルでは Python サンプルに抜粋コメントがあるのでそれを補助資料にする
- **バージョン表記**: 本番 URL は現行 **v4r8**（`e_api_v4r8`）、ドキュメント類は v4r7 ファイル名のまま流用されている。v4r7 と v4r8 で互換を保つ方針のため、パラメータ仕様は v4r7 ドキュメントを参照してよい
- **Python サンプル（1 サンプル = 1 サブディレクトリ）**: `samples/e_api_*_tel.py/`
  - 各ディレクトリに `LICENSE` / `README.md` / `e_api_*.py` が同梱（`e_api_login_tel.py/` には更に `e_api_login_response.txt` と `e_api_account_info.txt` の実例 JSON が入っている）
  - ログイン: `e_api_login_tel.py/e_api_login_tel.py`
  - 新規注文（現物）: `e_api_order_genbutsu_buy_tel.py` / `e_api_order_genbutsu_sell_tel.py`
  - 新規注文（信用）: `e_api_order_shinyou_buy_shinki_tel.py` / `e_api_order_shinyou_sell_shinki_tel.py`
  - 返済注文（信用）: `e_api_order_shinyou_{buy,sell}_hensai_tel.py` / `e_api_order_shinyou_{buy,sell}_hensai_kobetsu_tel.py`（後者は建玉個別指定）
  - 訂正/取消: `e_api_correct_order_tel.py` / `e_api_cancel_order_tel.py` / `e_api_cancel_order_all_tel.py`
  - 一覧取得: `e_api_get_orderlist_tel.py` / `e_api_get_orderlist_detail_tel.py` / `e_api_get_genbutu_kabu_list_tel.py` / `e_api_get_shinyou_tategyoku_list_tel.py`
  - 余力: `e_api_get_kanougaku_genbutsu_tel.py` / `e_api_get_kanougaku_shinyou_tel.py`
  - マスタ: `e_api_get_master_tel.py`（全量ダウンロード）/ `e_api_get_master_kobetsu_tel.py`（個別列取得）
  - ニュース: `e_api_get_news_header_tel.py` / `e_api_get_news_body_tel.py`（本文は Base64）
  - 時価履歴: `e_api_get_histrical_price_daily.py` / `e_api_get_price_from_file_tel.py`
  - プッシュ: `e_api_event_receive_tel.py`（EVENT HTTP long-polling）/ `e_api_websocket_receive_tel.py`（WebSocket 版）
  - 総合例（スタンドアロン、直下に配置）: `samples/e_api_sample_v4r8.py` / `samples/e_api_sample_v4r8.txt`
  - 参考（非 Python）: `samples/Excel_VBA_api_sample_tel.xlsm/`（VBA 版サンプル一式）/ `samples/e_api_test_compress_v4r2_js.py/`（レスポンス gzip 圧縮の動作確認）
- **計画文書**: [docs/✅tachibana/](../../../docs/✅tachibana/)（README / spec / architecture / data-mapping / implementation-plan / open-questions）

**原則**: 公式マニュアルが最優先。Python サンプルはマニュアル記載のパラメータを動作コードで示す参考実装。矛盾があればマニュアルに従う。

## サブリファレンス（必要時のみ読む）

- [references/sclmid.md](references/sclmid.md) — sCLMID 一覧（認証・業務・マスタ・時価・EVENT）。新しい機能追加時に該当行を引く
- [references/order_params.md](references/order_params.md) — `CLMKabuNewOrder` の入出力項目定石、訂正・取消との関係
- [references/event_protocol.md](references/event_protocol.md) — EVENT / WebSocket の `^A^B^C` 区切り規約、`p_evt_cmd` 種別、URL パラメータ
- [references/master_download.md](references/master_download.md) — `CLMEventDownload` ストリーム処理の特殊ルール、`sTargetCLMID` 一覧

---

## 立花 venue 利用の前提条件

立花証券 venue を使うには以下が**すべて**必要:

1. **電話認証済みの立花証券 e支店 口座** — e支店 API は電話認証（電話番号照合）完了後でないとプログラマチックログインが成立しない。電話認証未済のアカウントでは tkinter ログインダイアログが弾かれる。
2. **e支店 API サービスの申込み** — API 利用は口座開設とは別途申込みが必要な場合がある（立花証券営業窓口に確認すること）。
3. **デモ環境デフォルト** — デモ環境（`demo-kabuka.e-shiten.jp`）に接続する。本番接続には `TACHIBANA_ALLOW_PROD=1` env が必要（設定しても demo/prod 選択は tkinter ダイアログで都度行う）。
4. **CI demo ジョブは `workflow_dispatch` のみ** — `pytest -m demo_tachibana` を回す CI ジョブ（`.github/workflows/tachibana-demo.yml`）は手動トリガ限定。PR/push への組み込みは閉局帯ヒットによる偽陰性を防ぐため禁止（open-questions.md Q21）。

---

## いつこのスキルを発動するか

- 立花証券 API に対する新規エンドポイント・新しい `sCLMID` を追加するとき
- 立花 Python モジュール（`tachibana.py` / `tachibana_auth.py` / `tachibana_codec.py` / `tachibana_url.py` / `tachibana_ws.py` / `tachibana_master.py` 等）のリクエスト/レスポンス型を追加・修正するとき
- Python 側の EVENT / WebSocket 受信パース（`tachibana_codec.py` / `tachibana_ws.py`）を触るとき
- 注文入力・訂正・取消のパラメータを扱うとき
- `sResultCode` / `p_errno` のハンドリングを設計するとき
- ユーザーが「立花」「e支店」「ｅ支店」「tachibana」に触れたとき
- flowsurface をローカルで起動して立花セッションを必要とする検証を行うとき（下記「運用クイックスタート」を参照）

---

## 運用クイックスタート（ローカル起動で立花セッションを作る）

E2E 検証やエージェント体験検証で flowsurface を起動し、立花セッションを使いたい場合の手順。**コードを書く前にまずこの節を読むこと。** ここに書かれた含意を見落とすと「env 設定したのにログイン画面が空のまま」「`--ticker` が効かない」等で数十分単位の時間を失う。

### S1. ビルドは **debug** を使う（release では自動ログイン不可）

`DEV_TACHIBANA_USER_ID` / `DEV_TACHIBANA_PASSWORD` / `DEV_TACHIBANA_DEMO`（既定 `true`）による自動ログインは **Python 側 `python/engine/exchanges/tachibana_login_flow.py`** が読む。release ビルドでは `DEV_TACHIBANA_*` を読み込まず常にユーザー入力を要求する（release Python パスでガード）。Rust 側に env 取込みコードは追加しない（経路が Python に閉じる）。

| ビルド | 自動ログイン | デモトグル自動化 | 用途 |
| :--- | :--- | :--- | :--- |
| `target/debug/flowsurface.exe` | ✅（Python 側が env を読む） | ✅（`DEV_TACHIBANA_DEMO`） | E2E・検証・開発 |
| `target/release/flowsurface.exe` | ❌（Python 側も env を**完全無視**） | ❌ | 本番配布のみ |

**禁止**: release で起動してログイン画面が空なのを「env 未設定」と誤診断すること。release は env を読まない。

### S2. `.env` は flowsurface 本体が**読まない**。シェル側で export する

flowsurface は `dotenv` 系のクレートを使っていない。起動前に自前でロードする:

```bash
# bash / git-bash
set -a; source .env; set +a
./target/debug/flowsurface.exe
```

```powershell
# PowerShell
Get-Content .env | ForEach-Object {
  if ($_ -match '^([A-Z_]+)=(.*)$') { Set-Item -Path "Env:$($Matches[1])" -Value $Matches[2] }
}
& .\target\debug\flowsurface.exe
```

`.env` の想定キー（いずれも debug 専用、**Python 側 `tachibana_login_flow.py` のみが読む**）:

```
DEV_TACHIBANA_USER_ID=...          # 立花ユーザーID
DEV_TACHIBANA_PASSWORD=...         # ログインパスワード
DEV_TACHIBANA_DEMO=true            # demo 環境フラグ（**未設定時は demo 既定**で本番に飛ばない）
```

**第二暗証番号は env に置かない**（F-H5）。ログイン時（tkinter ダイアログ）では収集せず、発注時に iced modal で取得しメモリのみ保持する（`Command::SetSecondPassword` 経由で Python に送信、idle forget タイマーで自動消去）。`.env` に `DEV_TACHIBANA_SECOND_PASSWORD` を書いても Python は読まない。

**`DEV_TACHIBANA_DEMO` 既定値は `true`**。未設定でも demo URL のみを叩く（spec.md §3.1 / architecture.md §7.7、F-Default-Demo）。**本番接続は別途 `TACHIBANA_ALLOW_PROD=1` を併用したときに限り Python URL builder が解禁する**（Q7）。`DEV_IS_DEMO` / `TACHIBANA_USER_ID` / `TACHIBANA_PASSWORD` といった旧名は採用しない。

### S3. 2 回目以降の起動は **セッションファイルキャッシュ** が利用される

初回ログイン成功後、セッション（仮想 URL 一式）は Python が `cache_dir/tachibana/tachibana_session.json` にファイル保存する（[`tachibana_file_store.py`](../../../python/engine/exchanges/tachibana_file_store.py)）。keyring は使用しない。**次回起動時は `tachibana_session.json` が JST 当日付のものであれば自動復元を試みる**。ログでは下記のような順序:

```
INFO -- Attempting to restore tachibana session from file cache
INFO -- Session file loaded, validating...
INFO -- Tachibana session validated successfully, restoring
```

意味:
- **初回だけ**: `DEV_TACHIBANA_USER_ID` / `DEV_TACHIBANA_PASSWORD` が必要（`DEV_TACHIBANA_DEMO` は未設定で OK、既定 demo）。手動の電話認証は別途ユーザーが済ませている前提
- **2 回目以降（JST 当日限り）**: `tachibana_session.json` が有効なら env 未設定でも起動できる
- **セッションをリセットしたいとき**: `cache_dir/tachibana/tachibana_session.json` を削除する（`cache_dir` はアプリのデータディレクトリ、Windows: `%APPDATA%\flowsurface\`）

セッションが切れている（`p_errno="2"` or 夜間閉局越え or 翌日）場合は起動時検証が失敗するので、env を設定して再ログインする。

### S4. GUI バイナリは `--ticker` / `--timeframe` を**読まない**

clap CLI は `src/headless.rs` にしか実装されていない。GUI バイナリに `--ticker BinanceLinear:BTCUSDT --timeframe M1` を渡しても**無視**され、保存済みダッシュボード設定が復元される。

> 旧 HTTP API（:9876）は廃止された。起動後の pane 差し替えは GUI 内のサイドバー UI から手動で行う。
> 自動化したい場合は `saved-state.json` を起動前に書き換えるか、Python helper 経由で WS IPC（:19876）に attach する。

### S5. ポート衝突（19876）に気をつける

flowsurface engine WS server は `:19876` を bind する。複数起動した場合、後発の engine は bind に失敗する。`FLOWSURFACE_ENGINE_TOKEN` を設定していると `start_or_attach` で既存 engine に attach する経路に入るため、**意図せず別プロセスの engine を駆動**しているケースが起きる。事前に既存プロセスを確認する:

```powershell
# PowerShell（Windows 既定）
Get-NetTCPConnection -LocalPort 19876 -ErrorAction SilentlyContinue
Stop-Process -Id <pid> -Force
```

```bash
# bash / git-bash
netstat -ano | grep 19876
taskkill //PID <pid> //F
```

### S6. 起動時ログで拾うべきサイン

| ログ | 意味 | 対処 |
| :--- | :--- | :--- |
| `Attempting to restore tachibana session from file cache` → `Session file loaded, validating...` → `validated successfully` | 正常（ファイルキャッシュ復元） | そのまま利用可 |
| `engine WS bind failed on 127.0.0.1:19876: os error 10048` | ポート衝突（S5） | 既存プロセスを kill / `start_or_attach` で attach させる |
| `Tachibana daily history fetch failed: API エラー: code=6, message=引数（p_no:[N] <= 前要求.p_no:[N+1]）エラー` | 起動時の p_no 競合（R4）。セッション復元と並行で走る history fetch が逆転するケースがある | 機能影響は軽微だが、`next_p_no()` の呼び出しパスを見直す価値あり（既知の軽微バグ） |
| `Unsupported ticker: 'Binance Linear': "币安人生USDT"` 等 | metadata 取得中の無害な警告 | 無視してよい |

---

## 絶対に守るべきルール

### R1. 本番環境では実弾が飛ぶ／URL リテラルは 1 箇所限定

- **本番 URL** `https://kabuka.e-shiten.jp/e_api_v4r8/` に接続すると、発注関連 API は**実際に市場へ注文が出る**。約定は取り消せない
- **開発・テストはデモ環境** `https://demo-kabuka.e-shiten.jp/e_api_v4r8/` を使う
- **URL リテラルの所在は 1 箇所限定（F-L1）**: `BASE_URL_PROD` / `BASE_URL_DEMO` を持てるのは **`python/engine/exchanges/tachibana_url.py` の冒頭定義 1 箇所のみ**。Rust 側に本番 URL リテラルを書かず、Python から venue 設定経由で受け取る（architecture.md §1）
- Python 側のテストでは `BASE_URL_DEMO` またはテスト用モック URL のみを使う（`HTTPXMock` 既定）

### R2. URL 形式は独自仕様（クエリ構造ではない）

- マニュアル根拠: `mfds_json_api_ref_text.html#ComP1`「【アクセス方法】」
- REQUEST I/F はすべて `{virtual_url}?{JSON 文字列}` の形で送る
  - `?` 以降に **JSON オブジェクトの文字列をそのまま**付ける（`key=value&...` 形式ではない）
  - `httpx` の `params=` / `requests` の `params=` / `urllib` の `params=` は**使えない**
  - URL 構築は **Python 側 `tachibana_url.py`** に集約。`build_request_url(base, json_obj)`（REQUEST 用、JSON 文字列パス）と `build_event_url(base, params)`（EVENT 用、key=value 形式）を別関数として実装する
- EVENT I/F だけは例外で **通常の `key=value&key=value` 形式**（`p_rid`, `p_board_no`, `p_gyou_no`, `p_issue_code`, `p_mkt_code`, `p_eno`, `p_evt_cmd`）。REQUEST と混同しない
- 認証は `{BASE_URL}/auth/?{JSON}` と `/auth/` セグメントを挟む。それ以外は仮想 URL に直接付ける（仮想 URL 自体の末尾に `/` が含まれている）

### R3. 認証フローと仮想 URL の寿命

1. ユーザーが **電話認証**（手動）を先に完了させる
2. `CLMAuthLoginRequest` でログインし、応答（`CLMAuthLoginAck`）から以下 5 個の**仮想 URL**（= セッション固有、1 日券）を取得する:
   - `sUrlRequest` — 業務機能（REQUEST I/F）
   - `sUrlMaster` — マスタ機能（REQUEST I/F）
   - `sUrlPrice` — 時価情報機能（REQUEST I/F）
   - `sUrlEvent` — 注文約定通知（EVENT I/F, HTTP long-polling）
   - `sUrlEventWebSocket` — EVENT I/F WebSocket 版（スキームは `wss://`）
   - 応答には他に `sZyoutoekiKazeiC`（譲渡益課税区分）などが含まれる。発注時の同名フィールドにはこの値をそのまま使うのが定石（`samples/e_api_login_tel.py/e_api_login_response.txt` 参照）
3. 夜間の閉局まで仮想 URL は有効。閉局後は電話認証からやり直し
4. **仮想 URL はセッション秘密**。ログ出力・テレメトリ送信時はマスクすること
5. 永続化は **Python 側 [`tachibana_file_store.py`](../../../python/engine/exchanges/tachibana_file_store.py)** が `tachibana_session.json` ファイルキャッシュで行う（JST 当日付で有効判定）。keyring は使用しない
6. ログイン応答パース → セッション変換は **Python 側 `tachibana_auth.py`** で実装する。`p_errno` → `sResultCode` → `sKinsyouhouMidokuFlg` の 3 段チェックを強制し、途中のいずれかが NG なら `LoginError` / `UnreadNoticesError` で早期脱出する

### R4. `p_no` と `p_sd_date` は全リクエストに必須

- `p_no` — リクエスト通番。**リクエストごとに単調増加**する整数（最大 10 桁）。セッション復元後も必ず前回より大きい値を使う
  - Python 側 `tachibana_auth.next_p_no()` を使う（`asyncio` 単一スレッド前提の単調増加カウンタ、Unix 秒で初期化）。**自前採番禁止**
- `p_sd_date` — 送信日時 `YYYY.MM.DD-hh:mm:ss.sss`（JST）。UTC で送らない
  - Python 側 `tachibana_auth.current_p_sd_date()` を使う（JST 固定）

### R5. `sJsonOfmt`="5" を必ず指定する

- "5" = bit1 ON（ブラウザで見やすい形式）+ bit3 ON（引数項目名称での応答）
- 指定しないとレスポンスのキーが数値 ID になりデシリアライズできない
- マスタダウンロード（`CLMEventDownload`）は "4" を使う（一行 1 データで保存しやすい — [references/master_download.md](references/master_download.md) 参照）

### R6. エラーは 2 段階で判定する

```
if p_errno != "0"       → API 共通エラー（認証・接続レベル）
if sResultCode != "0"   → 業務処理エラー（パラメータ不正・残高不足など）
```

- **両方**をチェックする。片方だけではエラーを見逃す
- `p_errno` はレスポンスで**空文字列のことがある**ため、`"0" または空文字 = 正常` として扱う（公式サンプル `e_api_login_tel.py` などで観測される挙動。マニュアル `ComT7` の結果コード表は `"0"=正常` のみ規定し、空文字の取り扱いはサンプル準拠）
- `sResultCode` 一覧は `ComT7`（[`#sResultCode`](manual_files/mfds_json_api_ref_text.html#sResultCode)）参照。警告コード `sWarningCode` / `sWarningText` も同セクションに一覧あり
- `p_errno="2"` は**仮想 URL 無効**（セッション切れ or 営業時間外） → 再ログインが必要
- ログインで `p_errno=0 && sResultCode=0` でも `sKinsyouhouMidokuFlg=="1"` なら仮想 URL が空で利用不可 → `UnreadNoticesError`
- 共通判定は Python 側 `tachibana_auth.check_response(payload)` に集約し、例外型 `ApiError(code, message)` / `LoginError` / `UnreadNoticesError` / `SessionExpiredError` で原因切り分けする

### R7. レスポンスは Shift-JIS

- 日本語テキスト（銘柄名・エラーメッセージ）は Shift-JIS エンコード
- Python 側 `tachibana_codec.decode_response_body(bytes)` を経由。`bytes.decode("utf-8")` 直叩きは文字化けする
- **`errors="ignore"` を本番経路で使わない**。サイレントにバイトを落とすと銘柄名・エラーメッセージが部分破損したまま処理継続してしまう。既定は `errors="strict"`、失敗を許容したいログ出力経路だけ `errors="replace"`（`?` 化＋警告ログ）に切り替える。Python 公式サンプルが `errors="ignore"` を使っているのは教育用の簡略化であり、規範ではない

### R8. 空配列は `""` で返る

- 注文ゼロ件などの場合、本来配列のフィールドが空文字列 `""` で返る
- Python 側 `tachibana_codec.deserialize_tachibana_list(value)` で `""` → `[]` 正規化する
- 新しい List 応答型を追加する際は必ずこのヘルパー（または同等のバリデータ）を通す

### R9. URL エンコードの非標準文字

JSON 文字列を `?` 以降に貼り付けた後、含まれる記号文字をパーセントエンコードする。Python サンプル [`e_api_login_tel.py` の `func_replace_urlecnode`](samples/e_api_login_tel.py/e_api_login_tel.py) が置換対象 30 文字を列挙している。代表的なもの:

```
' ' → '%20'    '!' → '%21'    '"' → '%22'    '#' → '%23'    '$' → '%24'
'%' → '%25'    '&' → '%26'    "'" → '%27'    '(' → '%28'    ')' → '%29'
'*' → '%2A'    '+' → '%2B'    ',' → '%2C'    '/' → '%2F'    ':' → '%3A'
';' → '%3B'    '<' → '%3C'    '=' → '%3D'    '>' → '%3E'    '?' → '%3F'
'@' → '%40'    '[' → '%5B'    ']' → '%5D'    '^' → '%5E'    '`' → '%60'
'{' → '%7B'    '|' → '%7C'    '}' → '%7D'    '~' → '%7E'
```

JSON 構造の `{` `}` `"` `:` `,` も**置換対象に含まれる**。つまり「生 JSON 文字列をそのまま全体エンコード」してから貼る運用ではなく、**key/value を個別にエンコードしつつ JSON 構造文字（`{` `}` `"` `:` `,`）はクエリにそのまま埋める**のが正しい:

```python
# 誤: httpx.get(url, params=payload)                       # 標準クエリエンコードされ立花仕様外
# 誤: url + "?" + urllib.parse.quote(json.dumps(payload))  # 構造文字までエンコードされ JSON にならない
# 正: url + "?" + tachibana_url.func_replace_urlecnode(json.dumps(payload))
#     # ↑ key/value 内の 30 文字を %xx 化、JSON 構造は保持
```

- パスワードに記号が含まれる場合は必ずエンコード。`func_replace_urlecnode` をそのまま `tachibana_url.py` に移植する（標準ライブラリの `urllib.parse.quote` の `safe` 引数チューニングでは立花仕様を再現しにくいので、置換テーブル直書きが安全）
- マルチバイト（日本語）は Shift-JIS エンコード後に `%xx` 化が公式流儀だが、現状マルチバイト送信は発生していないため未検証。拡張時は `api_web_access.xlsx` の事例に従う

### R10. シークレットは**絶対に**ハードコードしない

- `sUserId` / `sPassword` / `sSecondPassword` / 仮想 URL はすべて機密情報
- 運用時は Python の [`tachibana_file_store.py`](../../../python/engine/exchanges/tachibana_file_store.py)（ファイルキャッシュ）経由でのみ扱う。keyring は使用しない
- `DEV_TACHIBANA_USER_ID` / `DEV_TACHIBANA_PASSWORD` 環境変数による自動ログインは **Python 側 `tachibana_login_flow.py` の fast path** で扱い、release ビルドでは env を読まないようガードする
- 第二暗証番号は env / ファイルに保存しない。発注時に iced modal で取得しメモリのみ保持、idle forget タイマーで自動消去
- `.env` を使う場合は `.gitignore` に入れ、PR/コミットにも載せない
- ログ（`logger.info` / `log::info!`）に仮想 URL・パスワード・第二暗証番号を含めない（`debug` レベルですら生で流さず、`***` にマスク）。テストコード内でも同じ

---

## Python 実装ヘルパー（`python/engine/exchanges/tachibana*.py`）

立花 venue の I/O は Python 側に集約する。新しい sCLMID を追加する際は下記ヘルパーを踏襲する（未実装のものはこの規約に沿って新設する）。Rust 側に同等ヘルパーを実装してはいけない。

- `tachibana_url.build_request_url(base, json_obj)` — REQUEST 用 `{base}?{JSON文字列}` を組み立て（R2）
- `tachibana_url.build_event_url(base, params: dict)` — EVENT 用 `{base}?key=value&...` を組み立て（R2 例外）
- `tachibana_url.func_replace_urlecnode(s)` — 30 文字置換（R9）
- `tachibana_codec.decode_response_body(bytes)` — Shift-JIS デコード（R7）
- `tachibana_codec.parse_event_frame(data: str) -> list[tuple[str, str]]` — `^A^B^C` 区切り分解
- `tachibana_codec.deserialize_tachibana_list(value)` — 空配列 `""` → `[]` 正規化（R8）
- `tachibana_auth.next_p_no()` — `asyncio` 単一スレッド前提の単調増加カウンタ（R4、自前採番禁止）
- `tachibana_auth.current_p_sd_date()` — JST 固定の送信日時（R4）
- `tachibana_auth.check_response(payload)` — `p_errno` → `sResultCode` の二段判定（R6、`p_errno` 空文字 = 正常）
- 例外階層: `LoginError` / `UnreadNoticesError` / `SessionExpiredError` / `ApiError` を `tachibana_auth.py` で定義
- テストは [`pytest-httpx`](https://pypi.org/project/pytest-httpx/) の `HTTPXMock` フィクスチャでモック（既存 [`python/tests/test_binance_rest.py`](../../../python/tests/test_binance_rest.py) パターン踏襲）。本番 URL を絶対に踏まない（R1）。ログイン応答は [`samples/e_api_login_tel.py/e_api_login_response.txt`](samples/e_api_login_tel.py/e_api_login_response.txt) の実例を流用する

## Rust 側に置くもの／置かないもの

**置くもの**（IPC ライフサイクル＋ enum 定義のみ）:

- `engine-client/src/dto.rs` — IPC コマンド `RequestVenueLogin` / venue ライフサイクルイベント `VenueReady` / `VenueError` / `VenueLoginStarted` / `VenueLoginCancelled`
- `engine-client/src/capabilities.rs` — `Ready.capabilities.venue_capabilities[<venue>][<key>]` の型付き抽出
- `exchange/src/adapter.rs` — `Venue::Tachibana` / `MarketKind::Stock` / `Exchange::TachibanaStock` 列挙子

**置かないもの**（Python に集約）:

- `exchange/src/adapter/tachibana.rs` — venue adapter は新設しない
- `data/src/config/tachibana.rs` — Python autonomous 方針によりセッション永続化は Python `tachibana_file_store.py` 側
- `src/connector/auth.rs` / `src/replay_api.rs` の立花拡張 — 不要
- `src/screen/login.rs` の立花用ログイン UI — Python tkinter ヘルパー subprocess で開く（フィールド名・ラベル・順序を Rust 側に書かない）
- `#[cfg(debug_assertions)]` の env 取込みコード — env は Python `tachibana_login_flow.py` のみが読む
- 立花 WebSocket クライアント — Python `tachibana_ws.py` に集約

---

詳細は [docs/✅tachibana/](../../../docs/✅tachibana/) を参照。

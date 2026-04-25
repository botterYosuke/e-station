---
name: 立花証券・ｅ支店・ＡＰＩ
description: 立花証券 e支店 API（v4r7/v4r8）を使ったコーディング規約と運用クイックスタート。認証フロー・仮想URL管理・JSON クエリ形式・EVENT/WebSocket ストリーム・注文送信の不変条件に加え、flowsurface をローカル起動する際の debug/release 区別・.env ロード・keyring 優先・GUI CLI 制約・ポート衝突の落とし穴を定義する。
---

# 立花証券・ｅ支店・ＡＰＩ スキル

> **状態（2026-04-25, T0.2 同期完了）**: 立花 venue は **Phase 1 計画フェーズ → 型実装着手**。
>
> - **API 仕様（R1〜R10、sCLMID、EVENT 規約、Shift-JIS、URL 形式）は一次資料として信頼してよい**
> - **Rust 側 venue adapter は新設しない**（Python 集約方針、[architecture.md §1](../../../docs/plan/tachibana/architecture.md)）。本ファイルが旧版で参照していた `exchange/src/adapter/tachibana.rs` / `src/connector/auth.rs` / `src/replay_api.rs` などは **書かない**ファイルパス
> - **Rust 側に追加されるのは下記のみ**:
>   - `data/src/config/tachibana.rs` — 内部保持型 `TachibanaCredentials` / `TachibanaSession`（`SecretString` ラップ）+ keyring r/w（T0 で型骨格、T3 で完成）
>   - `engine-client/src/dto.rs` — IPC コマンド `SetVenueCredentials` / `RequestVenueLogin` と venue ライフサイクルイベント `VenueReady` / `VenueError` / `VenueCredentialsRefreshed` / `VenueLoginStarted` / `VenueLoginCancelled`（T0.2 で追加済み）
>   - `engine-client/src/capabilities.rs` — `Ready.capabilities.venue_capabilities` 抽出ヘルパー（T0.2）
>   - `exchange/src/adapter.rs` — `Venue::Tachibana` / `MarketKind::Stock` / `Exchange::TachibanaStock` 列挙子（T0.2）
> - **ログイン UI は Python tkinter ヘルパー subprocess で開く**。Rust 側にログイン画面コード（フィールド名・ラベル・順序）を**書かない**。旧版で参照していた `src/screen/login.rs` は不要
> - **env 名は venue prefix 付き**: `DEV_TACHIBANA_USER_ID` / `DEV_TACHIBANA_PASSWORD` / `DEV_TACHIBANA_DEMO`（既定 `true` = demo、F-Default-Demo）。**読むのは Python 側 `tachibana_login_flow`**（Rust 側に `#[cfg(debug_assertions)]` の env 取込みは不要）。`DEV_TACHIBANA_SECOND_PASSWORD` は **Phase 1 では不採用**（F-H5、env 名は Phase 2 着手時に確定）
> - **第二暗証番号は Phase 1 では収集しない**（DTO スキーマには `Option<SecretString>` で枠を切るが常に `None` を送る、F-H5）
> - 詳細は [docs/plan/tachibana/](../../../docs/plan/tachibana/) を参照

flowsurface 立花 venue 統合は **Python 側 `python/engine/exchanges/tachibana*.py` にロジックを集約**する。Rust 側はチャート描画と keyring の OS bridge に責務を絞る。本スキルは Claude が API 仕様に正しく沿って Python / IPC コードを書くためのルール集である。

> **以降、本ファイル本文中で「実装」「実装済み」と書かれた Rust 側ヘルパーへの言及は、特記がない限り**「将来実装予定（T1〜T3 で Python 側に新設、Rust 側は新設しない）」**と読み替えること**。

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
- **バージョン表記**: 本番 URL は現行 **v4r8**（`e_api_v4r8`）、ドキュメント類は v4r7 ファイル名のまま流用されている。**`BASE_URL_PROD` リテラル (`kabuka.e-shiten.jp`) を持てるのは `python/engine/exchanges/tachibana_url.py` の冒頭定義 1 箇所のみ**（F-L1）。Rust 側には本番 URL リテラルを書かず、Python から受け取る設計（architecture.md §1）。「Rust 側も `BASE_URL_*` は `v4r8`」という旧記述はこの方針変更により廃止。v4r7 と v4r8 で互換を保つ方針のため、パラメータ仕様は v4r7 ドキュメントを参照してよい
- **Python サンプル（1 サンプル = 1 サブディレクトリ）**: `.claude/skills/tachibana/samples/e_api_*_tel.py/`
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
- **計画文書**: [docs/plan/tachibana/](../../../docs/plan/tachibana/)（README / spec / architecture / data-mapping / implementation-plan / open-questions）。Phase 1 の作業は **Python 側に集約**するため、Rust adapter は**新設しない**

**原則**: 公式マニュアルが最優先。Python サンプルはマニュアル記載のパラメータを動作コードで示す参考実装。矛盾があればマニュアルに従う。Rust の既存実装は検証済みの参考パターンであり、新規コードはできるだけこの構造を踏襲する。

---

## いつこのスキルを発動するか

- 立花証券 API に対する新規エンドポイント・新しい `sCLMID` を追加するとき
- 立花 Python モジュール（`tachibana.py` / `tachibana_auth.py` / `tachibana_codec.py` / `tachibana_url.py` / `tachibana_ws.py` / `tachibana_master.py` 等）のリクエスト/レスポンス型を追加・修正するとき
- Python 側の EVENT / WebSocket 受信パース（`tachibana_codec.py` / `tachibana_ws.py`）を触るとき
- 注文入力・訂正・取消のパラメータ（Phase 2 以降）を扱うとき
- `sResultCode` / `p_errno` のハンドリングを設計するとき
- ユーザーが「立花」「e支店」「ｅ支店」「tachibana」に触れたとき
- flowsurface をローカルで起動して立花セッションを必要とする検証を行うとき（下記「運用クイックスタート」を参照）

---

## 運用クイックスタート（ローカル起動で立花セッションを作る）

E2E 検証やエージェント体験検証で flowsurface を起動し、立花セッションを使いたい場合の手順。**コードを書く前にまずこの節を読むこと。** ここに書かれた含意を見落とすと「env 設定したのにログイン画面が空のまま」「`--ticker` が効かない」等で数十分単位の時間を失う。

### S1. ビルドは **debug** を使う（release では自動ログイン不可）

`DEV_TACHIBANA_USER_ID` / `DEV_TACHIBANA_PASSWORD` / `DEV_TACHIBANA_DEMO`（既定 `true`）による自動ログインは **Python 側 `python/engine/exchanges/tachibana_login_flow.py`** が読む（`DEV_TACHIBANA_SECOND_PASSWORD` は Phase 1 不採用、F-H5）。Rust 側に `#[cfg(debug_assertions)]` の env 取込みコードは追加しない（経路が Python に閉じる）。release ビルドでは `DEV_TACHIBANA_*` を読み込まず常にユーザー入力を要求する。

| ビルド | 自動ログイン | デモトグル自動化 | 用途 |
| :--- | :--- | :--- | :--- |
| `target/debug/flowsurface.exe` | ✅（Python 側が env を読む） | ✅（`DEV_TACHIBANA_DEMO`） | E2E・検証・開発 |
| `target/release/flowsurface.exe` | ❌（Python 側も env を**完全無視**、release Python パスでガード） | ❌ | 本番配布のみ |

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
# DEV_TACHIBANA_SECOND_PASSWORD は Phase 1 では採用しない（F-H5）。env に書いても Python は読まない。Phase 2 着手時に env 名を確定する
DEV_TACHIBANA_DEMO=true            # demo 環境フラグ（**未設定時は demo 既定**で本番に飛ばない）
```

**`DEV_TACHIBANA_DEMO` 既定値は `true`**。未設定でも demo URL のみを叩く（spec.md §3.1 / architecture.md §7.7、F-Default-Demo）。**本番接続は別途 `TACHIBANA_ALLOW_PROD=1` を併用したときに限り Python URL builder が解禁する**（implementation-plan T7、Q7）。`DEV_IS_DEMO` / `TACHIBANA_USER_ID` / `TACHIBANA_PASSWORD` といった旧名は**いずれも採用しない**。`.env` に書かれていても Python は読まない（誤って残しても害は無いが混乱の元なので削除推奨）。

### S3. 2 回目以降の起動は **keyring が env より優先**される

初回ログイン成功後、セッション（仮想 URL 一式）は OS keyring に保存される（[`data::config::tachibana`](../../../data/src/config/tachibana.rs)、R3/R10）。**次回起動時は env を読む前に keyring を見て、有効なら自動復元する**。ログでは下記のような順序:

```
INFO -- Attempting to restore tachibana session from keyring
INFO -- Loaded tachibana session from keyring
INFO -- Tachibana session validated successfully, restoring
```

意味:
- **初回だけ**: `DEV_TACHIBANA_USER_ID` / `DEV_TACHIBANA_PASSWORD` が必要（`DEV_TACHIBANA_DEMO` は未設定で OK、既定 demo）。手動の電話認証は別途ユーザーが済ませている前提
- **2 回目以降**: 仮想 URL が夜間閉局までは keyring セッションで起動できる。env 未設定でも動く
- **keyring を壊したいとき**: 専用 HTTP API は **将来実装予定（T3 以降）**。Phase 1 では keyring エントリ（service `flowsurface.tachibana`）を OS の keyring CLI（macOS: `security delete-generic-password`, Windows: `cmdkey /delete`, Linux: `secret-tool clear`）で直接削除する

セッションが切れている（`p_errno="2"` or 夜間閉局越え）場合は起動時検証が失敗するので、env を再設定して再起動する。

### S4. GUI バイナリは `--ticker` / `--timeframe` を**読まない**

clap CLI は `src/headless.rs` にしか実装されていない。GUI バイナリに `--ticker BinanceLinear:BTCUSDT --timeframe M1` を渡しても**無視**され、保存済みダッシュボード設定が復元される。GUI の初期ペインを特定 ticker に向けたい場合は起動後に HTTP API で差し替える:

```bash
curl -s http://127.0.0.1:9876/api/pane/list   # pane_id を確認
curl -s -X POST http://127.0.0.1:9876/api/pane/set-ticker \
  -H 'Content-Type: application/json' \
  -d '{"pane_id":"<uuid>","ticker":"BinanceLinear:BTCUSDT"}'
curl -s -X POST http://127.0.0.1:9876/api/pane/set-timeframe \
  -H 'Content-Type: application/json' \
  -d '{"pane_id":"<uuid>","timeframe":"M1"}'
```

metadata fetch が完了するまで `{"error":"ticker info not loaded yet: ... (wait for metadata fetch)"}` が返る。リトライ（2〜5 秒間隔）で回避する。

### S5. ポート衝突（9876）に気をつける

flowsurface を複数起動すると後発の HTTP API server は `os error 10048` で bind に失敗し、**サイレントに API 無しで動き続ける**（GUI は表示されるので気付きにくい）。必ず事前に既存プロセスを落とす:

```bash
netstat -ano | grep 9876        # LISTENING の PID を確認
taskkill //PID <pid> //F
```

「curl は返るのに挙動が違う」と感じたら、まず `netstat` で port 9876 を持っているプロセスが今起動したほうかを確認する。

### S6. 起動時ログで拾うべきサイン

| ログ | 意味 | 対処 |
| :--- | :--- | :--- |
| `Attempting to restore tachibana session from keyring` → `Loaded` → `validated successfully` | 正常（keyring 復元） | そのまま利用可 |
| `Failed to bind replay API server on 127.0.0.1:9876: os error 10048` | ポート衝突（S5） | 既存プロセスを kill |
| `Tachibana daily history fetch failed: API エラー: code=6, message=引数（p_no:[N] <= 前要求.p_no:[N+1]）エラー` | 起動時の p_no 競合（R4）。セッション復元と並行で走る history fetch が逆転するケースがある | 機能影響は軽微だが、`next_p_no()` の呼び出しパスを見直す価値あり（既知の軽微バグ） |
| `Unsupported ticker: 'Binance Linear': "币安人生USDT"` 等 | metadata 取得中の無害な警告 | 無視してよい |

---

## 絶対に守るべきルール

### R1. 本番環境では実弾が飛ぶ

- **本番 URL** `https://kabuka.e-shiten.jp/e_api_v4r8/` に接続すると、発注関連 API は**実際に市場へ注文が出る**。約定は取り消せない
- **開発・テストはデモ環境** `https://demo-kabuka.e-shiten.jp/e_api_v4r8/` を使う
- **URL リテラルの所在は 1 箇所限定（F-L1、L41 と整合）**: `BASE_URL_PROD` / `BASE_URL_DEMO` を持てるのは **`python/engine/exchanges/tachibana_url.py` の冒頭定義 1 箇所のみ**。Rust 側には本番 URL リテラルを書かず、Python から venue 設定経由で受け取る（旧版で参照していた `exchange::adapter::tachibana` 経由の Rust 側切替は本計画で**廃止**）
- Python 側のテストでは `BASE_URL_DEMO` またはテスト用モック URL のみを使う（`HTTPXMock` 既定）

### R2. URL 形式は独自仕様（クエリ構造ではない）

- マニュアル根拠: `mfds_json_api_ref_text.html#ComP1`「【アクセス方法】」
- REQUEST I/F はすべて `{virtual_url}?{JSON 文字列}` の形で送る
  - `?` 以降に **JSON オブジェクトの文字列をそのまま**付ける（`key=value&...` 形式ではない）
  - reqwest の `.query()` / `urllib` の `params=` は**使えない**
  - URL 構築は **Python 側 `python/engine/exchanges/tachibana_url.py`** に集約予定（T1）。`build_request_url(base, json_obj)`（REQUEST 用、JSON 文字列パス）と `build_event_url(base, params)`（EVENT 用、key=value 形式）を別関数として実装する
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
5. 永続化は **Rust 側 [`data::config::tachibana`](../../../data/src/config/tachibana.rs)** が keyring 経由で行う（T0.2 で骨格、T3 で完成）。Python 側はメモリのみで保持
6. ログイン応答パース → `TachibanaSession` 変換は **Python 側 `python/engine/exchanges/tachibana_auth.py`** で実装する（T2）。`p_errno` → `sResultCode` → `sKinsyouhouMidokuFlg` の 3 段チェックを強制し、途中のいずれかが NG なら `LoginError` / `UnreadNoticesError` で早期脱出する

### R4. `p_no` と `p_sd_date` は全リクエストに必須

- `p_no` — リクエスト通番。**リクエストごとに単調増加**する整数（最大 10 桁）。セッション復元後も必ず前回より大きい値を使う
  - flowsurface では `tachibana::next_p_no()` が AtomicU64 + Unix 秒初期化で保証。自前で採番しない
- `p_sd_date` — 送信日時 `YYYY.MM.DD-hh:mm:ss.sss`（JST）。UTC で送らない
  - 既存: `current_p_sd_date()` が `chrono::FixedOffset::east_opt(9*3600)` で JST 固定

### R5. `sJsonOfmt`="5" を必ず指定する

- "5" = bit1 ON（ブラウザで見やすい形式）+ bit3 ON（引数項目名称での応答）
- 指定しないとレスポンスのキーが数値 ID になりデシリアライズできない
- マスタダウンロード（`CLMEventDownload`）は "4" を使う（一行 1 データで保存しやすい）

### R6. エラーは 2 段階で判定する

```
if p_errno != "0"       → API 共通エラー（認証・接続レベル）
if sResultCode != "0"   → 業務処理エラー（パラメータ不正・残高不足など）
```

- **両方**をチェックする。片方だけではエラーを見逃す
- `p_errno` はレスポンスで**空文字列のことがある**ため、`"0" または空文字 = 正常` として扱う（Rust 実装もそうしている）
- `sResultCode` 一覧は `ComT7`（[`#sResultCode`](manual_files/mfds_json_api_ref_text.html#sResultCode)）参照。警告コード `sWarningCode` / `sWarningText` も同セクションに一覧あり
- `p_errno="2"` は**仮想 URL 無効**（セッション切れ or 営業時間外） → 再ログインが必要
- ログインで `p_errno=0 && sResultCode=0` でも `sKinsyouhouMidokuFlg=="1"` なら仮想 URL が空で利用不可 → `TachibanaError::UnreadNotices`
- 既存 Rust 実装は `TachibanaError::ApiError { code, message }` に `sResultCode` / `p_errno` の値を埋めて返す。`code` で分岐する側のコードは、コードが数値（5 桁）か `"2"` かで原因切り分けできる

### R7. レスポンスは Shift-JIS

- 日本語テキスト（銘柄名・エラーメッセージ）は Shift-JIS エンコード
- Python サンプルでは `bytes.decode("shift-jis", errors="ignore")`
- Rust では `decode_response_body` を経由。`String::from_utf8` 直叩きは文字化けする

### R8. 空配列は `""` で返る

- 注文ゼロ件などの場合、本来配列のフィールドが空文字列 `""` で返る
- `deserialize_tachibana_list` カスタムデシリアライザを使う（既存）
- 新しい List 応答型を追加する際は必ず `#[serde(deserialize_with = "deserialize_tachibana_list")]` を付ける

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

- JSON 構造の `{` `}` `"` `:` `,` は**エンコードされる**。つまり「生 JSON 文字列をそのまま全体エンコード」してから仮想 URL の `?` 後ろに貼る運用ではない。サンプルは key / value を個別にエンコードしつつ `"` `:` `,` `{` `}` は構造維持のままクエリに埋める
- パスワードに記号が含まれる場合は必ずエンコード。`func_replace_urlecnode` をそのまま移植するか、Rust 側では `percent-encoding` クレート相当の独自実装を使う（`reqwest` 内蔵のエンコーダは使わない — R2 の独自形式と競合する）
- マルチバイト（日本語）は Shift-JIS エンコード後に `%xx` 化が公式流儀だが、flowsurface では現状マルチバイト送信は発生していないため未検証。拡張時は `api_web_access.xlsx` の事例に従う

### R10. シークレットは**絶対に**ハードコードしない

- `sUserId` / `sPassword` / `sSecondPassword` / 仮想 URL はすべて機密情報
- 運用時は keyring（[`data::config::tachibana`](../../../data/src/config/tachibana.rs)）経由でのみ扱う
- `DEV_TACHIBANA_USER_ID` / `DEV_TACHIBANA_PASSWORD` 環境変数による自動ログインは **Python 側 `tachibana_login_flow.py` の fast path** で扱う（**将来実装予定（T3 で新設）**）。Rust 側に `#[cfg(debug_assertions)]` の env 取込みコードは追加しない。release ビルドでも env を読まないようガードする
- `.env` を使う場合は `.gitignore` に入れ、PR/コミットにも載せない
- `log::info!` に仮想 URL・パスワード・第二暗証番号を含めない（`debug!` ですら生で流さず、`***` にマスク）。テストコード内でも同じ

---

## リクエスト体系（sCLMID 一覧）

マニュアルの章立てに対応。Claude が新しい機能を追加する際は、この表から該当 `sCLMID` を選び、マニュアル該当セクションを読んでパラメータを確定させる。

### 認証 I/F — `ComT2`
| sCLMID | 機能 | 接続先 |
| :--- | :--- | :--- |
| `CLMAuthLoginRequest` | ログイン（仮想 URL 取得） | `{BASE_URL}/auth/` |
| `CLMAuthLogoutRequest` | ログアウト | `sUrlRequest` |

### 業務機能（REQUEST I/F）— `ComT3` — 接続先 `sUrlRequest`
| sCLMID | 機能 |
| :--- | :--- |
| `CLMKabuNewOrder` | 株式新規注文（現物/信用、買/売、成行/指値/逆指値） |
| `CLMKabuCorrectOrder` | 株式訂正注文 |
| `CLMKabuCancelOrder` | 株式取消注文 |
| `CLMKabuCancelOrderAll` | 株式一括取消 |
| `CLMGenbutuKabuList` | 現物保有銘柄一覧 |
| `CLMShinyouTategyokuList` | 信用建玉一覧 |
| `CLMZanKaiKanougaku` | 買余力 |
| `CLMZanShinkiKanoIjiritu` | 建余力＆本日維持率 |
| `CLMZanUriKanousuu` | 売却可能数量 |
| `CLMOrderList` | 注文一覧 |
| `CLMOrderListDetail` | 注文約定一覧（詳細） |
| `CLMZanKaiSummary` | 可能額サマリー |
| `CLMZanKaiKanougakuSuii` | 可能額推移 |
| `CLMZanKaiGenbutuKaitukeSyousai` | 現物株式買付可能額詳細 |
| `CLMZanKaiSinyouSinkidateSyousai` | 信用新規建て可能額詳細 |
| `CLMZanRealHosyoukinRitu` | リアル保証金率 |

### マスタ機能 — `ComT4` — 接続先 `sUrlMaster`
| sCLMID | 機能 |
| :--- | :--- |
| `CLMEventDownload` | マスタ一括ダウンロード（ストリーム、約 21MB） |
| `CLMMfdsGetMasterData` | マスタ情報問合取得（個別列指定） |
| `CLMMfdsGetNewsHead` | ニュースヘッダー |
| `CLMMfdsGetNewsBody` | ニュースボディー（**Base64 エンコード**、デコード必須） |
| `CLMMfdsGetIssueDetail` | 銘柄詳細情報 |
| `CLMMfdsGetSyoukinZan` | 証金残 |
| `CLMMfdsGetShinyouZan` | 信用残 |
| `CLMMfdsGetHibuInfo` | 逆日歩 |

### 時価情報機能 — `ComT5` — 接続先 `sUrlPrice`
| sCLMID | 機能 |
| :--- | :--- |
| `CLMMfdsGetMarketPrice` | 時価スナップショット（最大 120 銘柄） |
| `CLMMfdsGetMarketPriceHistory` | 日足履歴（1 銘柄、最大約 20 年分） |

### EVENT I/F — `ComT6` — 接続先 `sUrlEvent` / `sUrlEventWebSocket`

プッシュ型。HTTP はチャンク長期接続（long-polling）、WebSocket 版もあり。詳細は別紙「立花証券・ｅ支店・ＡＰＩ、EVENT I/F 利用方法、データ仕様」（HTML 版 `api_event_if_v4r7.pdf` / Excel 版 `api_event_if.xlsx`、どちらも `manual_files/` には同梱なし）。手元では Python サンプル [`e_api_event_receive_tel.py`](samples/e_api_event_receive_tel.py/e_api_event_receive_tel.py) / [`e_api_websocket_receive_tel.py`](samples/e_api_websocket_receive_tel.py/e_api_websocket_receive_tel.py) の冒頭コメントが抜粋リファレンスとして機能する。

---

## 注文（CLMKabuNewOrder）パラメータの定石

マニュアル該当章: [`#CLMKabuNewOrder`](manual_files/mfds_json_api_ref_text.html#CLMKabuNewOrder)。Python サンプル [`e_api_order_genbutsu_buy_tel.py:460-518`](samples/e_api_order_genbutsu_buy_tel.py/e_api_order_genbutsu_buy_tel.py#L460) のコメントに No.1〜No.28 の項目解説が揃っている（入出力別、char 長、取り得る値）。頻出フィールドのみ抜粋:

| 項目 | 意味 | 代表値 |
| :--- | :--- | :--- |
| `sIssueCode` | 銘柄コード | 通常 4 桁 / 優先株 5 桁（例 `6501`, `25935`） |
| `sSizyouC` | 市場 | `00`=東証（現状これのみ） |
| `sBaibaiKubun` | 売買区分 | `1`=売 / `3`=買 / `5`=現渡 / `7`=現引 |
| `sCondition` | 執行条件 | `0`=指定なし / `2`=寄付 / `4`=引け / `6`=不成 |
| `sOrderPrice` | 注文値段 | `*`=指定なし / `0`=成行 / それ以外は指値（呼値単位で丸める — マスタデータ利用方法 `2-12 呼値`） |
| `sOrderSuryou` | 注文数量 | 整数（単元株数の倍数） |
| `sGenkinShinyouKubun` | 現金信用区分 | `0`=現物 / `2`=制度信用新規 6m / `4`=制度信用返済 6m / `6`=一般信用新規 6m / `8`=一般信用返済 6m |
| `sOrderExpireDay` | 注文期日 | `0`=当日 / それ以外は `YYYYMMDD`（10 営業日まで） |
| `sGyakusasiOrderType` | 逆指値注文種別 | `0`=通常 |
| `sGyakusasiZyouken` | 逆指値条件 | `0`=指定なし / 条件値段 |
| `sGyakusasiPrice` | 逆指値値段 | `*`=指定なし / `0`=成行 / それ以外 |
| `sTatebiType` | 建日種類 | `*`=指定なし（現物または新規）/ `1`=個別指定 / `2`=建日順 / `3`=単価益順 / `4`=単価損順 |
| `sZyoutoekiKazeiC` | 譲渡益課税区分 | `1`=特定 / `3`=一般 / `5`=NISA（**ログイン応答を流用**） |
| `sTategyokuZyoutoekiKazeiC` | 建玉譲渡益課税区分 | 現引/現渡時のみ意味を持つ（`*`/`1`/`3`/`5`） |
| `sSecondPassword` | 第二暗証番号 | **省略不可**（ブラウザ版と異なり API 発注では必須） |
| `aCLMKabuHensaiData` | 返済リスト | 個別指定時のみ必須。`sTategyokuNumber` / `sTatebiZyuni` / `sOrderSuryou` の配列 |

**出力項目の抜粋**: `sOrderNumber`（注文番号、訂正・取消に必要）/ `sEigyouDay`（営業日 YYYYMMDD）/ `sOrderUkewatasiKingaku`（受渡金額）/ `sOrderTesuryou`（手数料）/ `sOrderSyouhizei`（消費税）。注文番号は以降の訂正・取消 API の `sOrderNumber` 引数として必ず保存する。

**信用 6 ヶ月以外（無期限・短期）は `CLMKabuNewOrder` では直接指定できない**（関連マニュアル参照）。

**訂正・取消の関係**:
- `CLMKabuCorrectOrder`: `sOrderNumber` を指定し、変更可能なのは `sOrderPrice` / `sCondition` / `sOrderSuryou` / `sOrderExpireDay` など限定項目。新規注文と同じく `sSecondPassword` が必要
- `CLMKabuCancelOrder`: `sOrderNumber` 単位
- `CLMKabuCancelOrderAll`: 未約定全件。誤爆に注意

**参考**: 各発注系サンプルは `samples/e_api_order_*_tel.py/` 配下。現物買=`genbutsu_buy`、信用新規買=`shinyou_buy_shinki`、信用返済（建玉個別指定）=`shinyou_*_hensai_kobetsu` といった命名で、引数の組合せ例がそのまま読める。

---

## EVENT / WebSocket ストリームのパース規約

### 区切り文字

受信データは ASCII 制御文字を区切りとして項目を羅列する:

| 記号 | コード | 意味 |
| :--- | :--- | :--- |
| `^A` | `\x01` | 項目区切り |
| `^B` | `\x02` | 項目名と値の区切り |
| `^C` | `\x03` | 値と値の区切り（複数値時） |
| `\n` | 0x0A | メッセージ区切り（WebSocket は ^A 末尾でも区切る） |

形式例: `項目A1^B値B1^A項目A2^B値B21^CB22^CB23^A...`

### キー命名

キーは `<型>_<行番号>_<情報コード>` 形式:
- 例 `p_1_DPP` → 型 `p`（プレーン文字列）・行番号 `1`・情報コード `DPP`（現在値）
- 行番号は `p_gyou_no`（1〜120）と対応
- 既存: `parse_event_frame(data: &str) -> Vec<(&str, &str)>` で分解可能

### URL パラメータ（重要な固定値）

EVENT I/F は **REQUEST と違い通常の `key=value&...` 形式**で組み立てる（R2 参照）。サンプルの並び順と値に合わせる:

```
{sUrlEvent}?p_evt_cmd=ST,KP,EC,SS,US,FD
           &p_eno=0            ※イベント通知番号（0=全件、再送時は指定値の次から）
           &p_rid=22           ※株価ボード・アプリ識別値（No.2: e支店・API、時価配信あり）
           &p_board_no=1000    ※固定値（株価ボード機能）
           &p_gyou_no=N[,N,...]    ※行番号（1-120）
           &p_issue_code=NNNN[,NNNN,...]   ※銘柄コード
           &p_mkt_code=NN[,NN,...]         ※市場コード
```

`p_evt_cmd` の種別（マニュアル別紙「EVENT I/F 利用方法」 p3/26 および [`e_api_event_receive_tel.py` l.534-544](samples/e_api_event_receive_tel.py/e_api_event_receive_tel.py)）:

| コード | 意味 | 通知契機 |
| :--- | :--- | :--- |
| `ST` | エラーステータス | 発生時 |
| `KP` | キープアライブ | 5 秒間通知未送信時 |
| `FD` | 時価情報 | 初回はメモリ内スナップショット（全データ）、以降は変化分のみ |
| `EC` | 注文約定通知 | 初回は当日分の未削除通知を接続毎に再送、以降は発生時 |
| `NS` | ニュース通知 | 初回再送、以降発生時。**重いため必要時のみ** |
| `SS` | システムステータス | 初回再送、以降発生時 |
| `US` | 運用ステータス | 初回再送、以降発生時 |
| `RR` | 画面リフレッシュ | 現時点不使用（指定しても無視） |

### 注意点

- **EVENT URL に `\n` や `\t` を入れない**（制御文字でサーバがエラー応答する）
- WebSocket 接続は Python 側 `python/engine/exchanges/tachibana_ws.py` に集約する（本 SKILL L11 / L17 の方針）。`websockets.connect(uri, ping_interval=None, ping_timeout=None)` で `websockets` ライブラリの自動 ping を無効化し、**受信ループで ping を受け取ったら手動で pong を返す**（[`e_api_websocket_receive_tel.py:710-723`](samples/e_api_websocket_receive_tel.py/e_api_websocket_receive_tel.py#L710) の `pong_handler` を参照）。Rust 側で立花 WebSocket を直接張る経路は採用しない（Python 集約方針）
- `p_errno:"2"` は仮想 URL 無効 → 再ログイン（電話認証から）
- EVENT 受信データはメッセージ単位で `\n`（LF）または `^A` 終端。一塊のチャンクに複数メッセージが含まれるため、受信バッファを蓄積しながら区切り子で分割する必要がある
- 受信本文も Shift-JIS。REQUEST と同じく UTF-8 前提で読むと銘柄名・ニュース本文が文字化けする

---

## マスタダウンロードの特殊ルール

`CLMEventDownload` は他の REQUEST と流れが違う:

- ストリーム形式（`urllib3` の `preload_content=False` 相当）で全量配信
- 1 レコードの終端は `}`、**全体の終端はレコード `{"sCLMID":"CLMEventDownloadComplete", ...}` の到着**。Python サンプルは `str_terminate = 'CLMEventDownloadComplete'` を定数化している
- 接続先は `sUrlMaster`（`sUrlRequest` ではない — [`e_api_get_master_tel.py:578-580`](samples/e_api_get_master_tel.py/e_api_get_master_tel.py#L578)）
- `sJsonOfmt` は `"4"` を使う（1 行 1 JSON 形式、ファイル保存・後続パース向け。`"5"` を使うと区切れなくなる）
- 受信チャンクをバイト列で蓄積し `byte_data[-1:] == b'}'` で 1 レコード分として Shift-JIS デコード → `json.loads`（[`e_api_get_master_tel.py:492-518`](samples/e_api_get_master_tel.py/e_api_get_master_tel.py#L492)）
- データ量が大きいため、メモリ展開ではなくストリーム処理を守ること（Rust なら `reqwest::Response::bytes_stream()`）

マスタデータ識別子（`sTargetCLMID`）:
- `CLMIssueMstKabu` 銘柄マスタ（株）
- `CLMIssueSizyouMstKabu` 銘柄市場マスタ（株）
- `CLMIssueMstSak` 銘柄マスタ（先物）
- `CLMIssueMstOp` 銘柄マスタ（OP）
- `CLMIssueMstOther` 日経平均・為替など
- `CLMOrderErrReason` 取引所エラー理由コード
- `CLMDateZyouhou` 日付情報

---

## Python 実装ヘルパー（**将来実装予定（T1〜T3 で新設）**、`python/engine/exchanges/tachibana*.py`）

立花 venue の I/O は **Python 側に集約**される。新しい sCLMID を追加する際は下記の Python ヘルパーを踏襲する（T1 / T2 で実装する）。Rust 側に同等ヘルパーを実装してはいけない。

- `tachibana_url.build_request_url(base, json_obj)` — REQUEST 用 `{base}?{JSON文字列}` を組み立て（R2）
- `tachibana_url.build_event_url(base, params: dict)` — EVENT 用 `{base}?key=value&...` を組み立て（R2 例外）
- `tachibana_url.func_replace_urlecnode(s)` — 30 文字置換（R9）
- `tachibana_codec.decode_response_body(bytes)` — Shift-JIS デコード（R7）
- `tachibana_codec.parse_event_frame(data: str) -> list[tuple[str, str]]` — `^A^B^C` 区切り分解
- `tachibana_codec.deserialize_tachibana_list(value)` — 空配列 `""` → `[]` 正規化（R8）
- `tachibana_auth.next_p_no()` — `asyncio` 単一スレッド前提の単調増加カウンタ（R4、自前採番禁止）
- `tachibana_auth.current_p_sd_date()` — JST 固定の送信日時（R4）
- `tachibana_auth.check_response(payload)` — `p_errno` → `sResultCode` の二段判定（R6、`p_errno` 空文字 = 正常）
- エラー型: Python 例外クラス階層（`LoginError`, `UnreadNoticesError`, `SessionExpiredError` ほか）。`tachibana_auth.py` で定義
- テストは [`pytest-httpx`](https://pypi.org/project/pytest-httpx/) の `HTTPXMock` フィクスチャでモック（既存 [`python/tests/test_binance_rest.py`](../../../python/tests/test_binance_rest.py) パターン踏襲）。本番 URL を絶対に踏まない（R1、F11）。ログイン応答は [`samples/e_api_login_tel.py/e_api_login_response.txt`](samples/e_api_login_tel.py/e_api_login_response.txt) の実例を流用する

### Rust 側に新設されるもの（T0.2 で着手）

- [`data::config::tachibana`](../../../data/src/config/tachibana.rs) — 内部保持型 `TachibanaCredentials` / `TachibanaSession`（`SecretString` ラップ）+ keyring r/w + IPC `*Wire` への `From` 変換（**T0.2 で型骨格、T3 で keyring 完成**）
- [`engine_client::dto`](../../../engine-client/src/dto.rs) — IPC コマンド `SetVenueCredentials` / `RequestVenueLogin`、イベント `VenueReady` / `VenueError` / `VenueCredentialsRefreshed` / `VenueLoginStarted` / `VenueLoginCancelled`（T0.2 完了済み）
- [`engine_client::capabilities::venue_capability`](../../../engine-client/src/capabilities.rs) — `Ready.capabilities.venue_capabilities[<venue>][<key>]` の型付き抽出（T0.2 完了済み）
- [`engine_client::process::ProcessManager.venue_credentials`](../../../engine-client/src/process.rs) — managed-mode 復旧時に `SetProxy` 後に `SetVenueCredentials` を再送（T0.2 で土台、T3 で UI 起動経路と接続）

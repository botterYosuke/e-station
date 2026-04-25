# 立花証券統合: 仕様

## 1. ゴール

本アプリの venue として立花証券 e支店 API を追加し、**日本株のチャート閲覧体験を既存暗号資産 venue と同じ UI で提供する**。Phase 1 はリードオンリー（閲覧のみ）。

## 2. スコープ（Phase 1 = MVP）

### 2.1 含めるもの

- **新 venue `Venue::Tachibana`**（[exchange/src/adapter.rs L264](../../../exchange/src/adapter.rs#L264) に追加）
- **新 `MarketKind::Stock`**（株式現物市場。信用は内部的に同じ market でハンドル）。`qty_in_quote_value` は enum 内部分岐で `price * qty` を強制（[implementation-plan T0.1/T0.2](./implementation-plan.md) で呼出 6 箇所を棚卸し済み）
- **新 `Exchange::TachibanaStock`**
- **`Timeframe` の serde 形式統一（F-H1）**: 現状 `#[derive(Serialize)]` のみで `"D1"` / `"M1"` 等が出る既知の不整合がある。立花 capabilities (`["1d"]`) と既存 `Display` (`"1d"`) に合わせるため、`Timeframe` 全変種に `#[serde(rename = "...")]` を T0.2 で追加
- **Python 実装** `python/engine/exchanges/tachibana.py`（`ExchangeWorker` 実装、デモ環境のみ）
  - 認証フロー（`CLMAuthLoginRequest` → 5 つの仮想 URL を取得 → `TachibanaSession` 保持）
  - ティッカー一覧（`CLMEventDownload` の `CLMIssueMstKabu` から銘柄マスタを取り出す）
  - ティッカーメタデータ（呼値単位・売買単位・銘柄名）— **本線は銘柄マスタ（`CLMIssueMstKabu` + `CLMIssueSizyouMstKabu`）から合成**し、マスタ未掲載や追加情報が必要な場合のみ `CLMMfdsGetIssueDetail` をフォールバックで叩く（F9）
  - 日足 kline 履歴（`CLMMfdsGetMarketPriceHistory`）
  - 24h ticker stats 相当（`CLMMfdsGetMarketPrice` のスナップショットから派生）
  - **取引（FD frame）ストリーム**: EVENT WebSocket (`sUrlEventWebSocket`) で `p_evt_cmd=FD` を購読 → 現値変化を 1 件 = 1 trade として配信（`p_*_DPP` フィールド）
  - **板スナップショット**: `CLMMfdsGetMarketPrice` を周期的にポーリングし `DepthSnapshot` として配信（diff/L2 はサポートしない、§3 参照）
- **Rust 側の最小変更**:
  - `Venue` / `Exchange` / `MarketKind` 拡張
  - `TachibanaCredentials` 型と keyring 永続化（[data/src/config/](../../../data/src/config/) 配下、`tachibana.rs` 新設）
  - 起動時にクレデンシャルを Python へ渡す IPC コマンド `SetVenueCredentials` と、再ログイン後 session を Rust へ返す `VenueCredentialsRefreshed`
  - **ログイン関連の画面（フォーム・エラー表示・確認モーダル）は Python が tkinter で独立ウィンドウとして開く**（[architecture.md §7](./architecture.md#7-ログイン画面の-python-駆動f-login1)、F-Login1）。Rust 側に立花のログイン画面コード（フィールド名・ラベル・順序）を書かない
  - GUI ライブラリは **tkinter（Python 標準ライブラリ）** を採用（追加依存ゼロ、日本語 IME 対応、軽量）。tkinter の制約（メインスレッド要求）はログインヘルパー subprocess 隔離で回避
  - keyring に creds が無い／立花機能を使い始めた／session 期限切れの 3 ケースで Python が tkinter ヘルパー subprocess を spawn し、Rust UI には `VenueLoginStarted` / `VenueLoginCancelled` / `VenueReady` / `VenueError` で状態を伝える
  - `TickerInfo`・`Exchange::price_step` 等で立花特有の呼値単位を反映
  - `MarketKind::Stock` 追加に伴う UI / indicator / timeframe / market filter / 表示ラベルの網羅修正
- **デモ環境のみ**: `https://demo-kabuka.e-shiten.jp/e_api_v4r8/`
- **debug ビルドの env 自動ログイン**: `DEV_TACHIBANA_USER_ID` / `DEV_TACHIBANA_PASSWORD` / `DEV_TACHIBANA_SECOND_PASSWORD` / `DEV_TACHIBANA_DEMO=true`（venue prefix を付けて将来の他 venue ID と衝突させない方針）。SKILL.md 側の `DEV_USER_ID` 旧表記の書き換えは [implementation-plan.md T0.2 の SKILL.md 同期タスク](./implementation-plan.md) に集約

### 2.2 含めないもの（明示的に Phase 2+ 送り）

- **発注・訂正・取消**（`CLMKabuNewOrder` / `CLMKabuCorrectOrder` / `CLMKabuCancelOrder*`）
  - 第二暗証番号の取り扱い、注文台帳 UI、約定通知 (`p_evt_cmd=EC`) の表示など、**ビュアーアプリの非ゴール**（[docs/plan/✅python-data-engine/spec.md](../✅python-data-engine/spec.md) §8 と整合）
- **本番環境（実弾）接続** — `BASE_URL_PROD` の追加自体は許容するが、**設定値からの選択を Phase 1 では UI 露出させない**。env / 設定ファイルからの明示的フラグでのみ切替可能とし、デフォルトは demo 強制
- **板差分（Depth Diff）/ L2 リアルタイム板** — 立花 FD frame は「現値・気配 5 本」程度で、L2 差分配信ではない。Phase 1 では **snapshot のみ** を一定間隔で配信し、`DepthDiff` イベントは生成しない
- **OI（建玉残）チャート** — 株式に概念がない。`fetch_open_interest` は `NotImplementedError` を返す
- **ヒストリカル trade（tick by tick 過去履歴）** — 立花は当日 FD frame の累積のみ、過去日のティック取得 API なし。`fetch_trades` は `NotImplementedError`
- **分足・秒足 kline** — 立花の REST 履歴は日足のみ（`CLMMfdsGetMarketPriceHistory`）。分足は FD frame からアプリ側で集計可能だが Phase 1 では未対応
- **ニュース** (`CLMMfdsGetNewsHead/Body`)
- **ザラ場外（PTS・夜間）銘柄**

### 2.3 MVP 必須に昇格した項目

- **ザラ場時間帯の判定（MVP 必須、T5 で実装）**: JST 9:00–11:30 前場 / 12:30–15:25 後場連続 / **15:25–15:30 クロージング・オークション**（**2024-11-05 以降の現行東証取引時間**）。`Connected` を維持するのは **9:00–15:30 全体**。クロージング・オークション中は気配がほぼ動かなくても「市場時間外」UI を出さない。閉場（〜9:00 / 11:30〜12:30 / 15:30〜）でのみ subscribe を `Disconnected{reason:"market_closed"}` で停止する。**Phase 1 はハードコード**（祝日カレンダー判定なし）。営業日カレンダー動的取得（`CLMDateZyouhou`）は Phase 2 送り

### 2.4 ストレッチゴール（同フェーズ内で時間が許せば）

- マスタファイル（21MB ストリーム）のローカルキャッシュ（日次更新）

## 3. 非機能要件

### 3.1 セキュリティ

- ユーザー ID / パスワード / 第二暗証番号 / 仮想 URL 5 種は **すべて機密**（[SKILL.md R10](../../../.claude/skills/tachibana/SKILL.md)）
  - Rust 側で OS keyring に保存（`data::config::tachibana`、新設）
  - Python 側に渡すのは IPC ハンドシェイク後の **`SetVenueCredentials` コマンド**（stdin / 環境変数を使わない、ログ出力でマスク）
  - Python 側は **メモリのみ**で保持。ディスクには書かない
- **第二暗証番号は Phase 1 では収集も保持もしない（F-H5）**: DTO スキーマ上は `second_password: Option<SecretString>` を切るが、Phase 1 では Rust 側の収集 UI も Python 側のメモリ保持も実装せず、常に `None` を送る。発注しないものを保持して攻撃面（コアダンプ・スワップ・GC 残存）を増やさない。Phase 2（発注機能）で値の収集・保持を有効化する。スキーマは破壊変更にならないため移行コストはない
- ログ出力時は仮想 URL のホスト部分まで `***` マスク（プロセス再起動時に keyring から復元するため、URL がリークしても session 侵害にはなりうる）
- `DEV_TACHIBANA_*` env は `#[cfg(debug_assertions)]` ブロックでのみ参照（release では完全除外）。Phase 1 では `DEV_TACHIBANA_SECOND_PASSWORD` も**読み込まない**
- **`BASE_URL_PROD` 定数の所在は 1 ファイル限定（F-L1）**: 本番 URL リテラル `kabuka.e-shiten.jp` を持てるのは `python/engine/exchanges/tachibana_url.py` の冒頭定義 1 箇所のみ。Rust 側は本番 URL を持たない（Python から venue 設定経由で受け取る）。`tools/secret_scan.sh` の allowlist もこの 1 ファイルのみとする

### 3.2 セッション寿命と復旧

- 仮想 URL は **夜間閉局までの 1 日券**。閉局後は電話認証からやり直し（自動化不可）
- セッション切れ（`p_errno="2"`）検知時:
  - Python 側は `VenueError{venue:"tachibana", code:"session_expired", message, request_id:None}` を発出して全立花購読を停止（旧 `EngineError{code:"tachibana_session_expired"}` 表記は廃止、F1）
  - **バナー文言は Python 側が `message` フィールドに込める**（F-Banner1）。Rust UI は受け取った `message` をそのまま表示するだけで、Rust 側に固定文言を持たない。これにより:
    - 文言の正本が立花アダプタ 1 箇所に集約される（venue 固有の事情を venue コードが持つ原則）
    - 状況別の細分化（夜間閉局 / マスタ未読通知 / 認証失敗 / レート制限など）を Rust UI 側のコード変更なしで Python から出し分けられる
    - 将来の i18n も Python 側の責務として一本化できる
  - Rust 側は **`VenueError` を「バナー表示 + 該当 venue の購読停止状態への遷移」というレンダラ的な扱い**にする。`code` で UI の severity（warning / error）と再ログインボタンの出し分けだけ判定し、文言生成はしない
  - **購読中の runtime では自動再ログインを試みない**（電話認証が前提のため）。定期的な `validate_session` ポーリングも**実装しない**（runtime 中に切れを検知した場合の対処が再ログイン禁止と矛盾するため、検知は subscribe 経路の `p_errno=2` だけに任せる）
  - ただし **アプリ起動直後の session 復元フェーズ**に限り、keyring 上の session 検証が失敗した場合は `user_id/password` による再ログインを 1 回だけ試してよい。ここで成功した session を再永続化する
  - **夜間閉局またぎ運用（F-m1）**: アプリを起動しっぱなしで翌日のザラ場開始を迎えた場合、最初の subscribe で `p_errno=2` を踏むのは仕様通り。Python 側は `VenueError{code:"session_expired"}` を返し、Rust UI は再ログインバナーを表示する。**ここで自動再ログインはしない**（電話認証完了の確認が取れないため）。ユーザーがバナーから再ログイン操作を行うと、起動時 fallback と同じ経路（`SetVenueCredentials` 再投入 → 1 回限りの user/password ログイン）を辿る
- **WebSocket 死活監視**: EVENT WS は **5 秒周期で `p_evt_cmd=KP`（KeepAlive）frame** を送ってくる。Python 側は KP 受信をタイマリセットに使い、**12 秒（KP 2 回欠損相当 + 2 秒 jitter）以上 KP も含めて全 frame が来なければ切断とみなして再接続**する。`Ping` フレームは `tokio-tungstenite` 等のライブラリ自動応答に頼らず手動で `Pong` を返す（SKILL.md EVENT 規約）
- `VenueReady{venue}` は **冪等イベント**として扱う。session が新たに validate / 再ログインされるたびに送ってよく、Rust 側は最後に受信した状態を保持する。`Disconnected{venue}`（プロセス再起動・WS 切断）受信で `VenueReady` 状態をリセットし、再 ready 後に既存購読の重複再送を行わないこと（`ProcessManager` 側で active subscriptions を 1 度だけ resubscribe する）
- Python 単独再起動時（[docs/plan/✅python-data-engine/spec.md](../✅python-data-engine/spec.md) §5.3 Python プロセス復旧プロトコル）は、**`ProcessManager` が source of truth になって** `SetProxy` に続けて `SetVenueCredentials` を再送し、`VenueReady` を待ってから metadata fetch / resubscribe を再開する

### 3.3 整合性 / レイテンシ

- FD frame 受信→ Rust 描画キュー投入: 中央値 < 50ms（株式は暗号資産より頻度が低いので緩め）
- **板更新は FD ストリーム駆動が正**（FD frame ごとに `DepthSnapshot` を再生成、data-mapping §4）。REST `CLMMfdsGetMarketPrice` polling は (a) ザラ場前後の初回 snapshot 1 発、(b) FD WS が一定時間（KP 含めて 12 秒）無通信で再接続中のフォールバック時のみ。**runtime の定期 polling は実装しない**
- マスタダウンロードは **起動時 1 回 + 日次更新のみ**、各 ticker subscribe ごとには走らせない。**kick タイミングは `VenueReady` 受信直後**（`sUrlMaster` が必要なため）。完了は `ListTickers` 応答到着で判定（F-H6、`VenueReady` 自体には含めない）
- 立花 venue の `ListTickers` / `GetTickerMetadata` / `FetchTickerStats` / `Subscribe` は **`VenueReady` 前に送らない**。`Ready` 直後に sidebar が自動 metadata fetch する既存導線があるため、Rust 側に venue-ready ゲートを追加する
- マスタキャッシュを永続化する場合は、Python が保存先を推測しない。**Rust から `config_dir` または `cache_dir` を起動時に明示的に渡す**

### 3.4 ベースライン計測

[docs/plan/✅python-data-engine/benchmarks/](../✅python-data-engine/benchmarks/) に立花用ベースラインを別ファイルで追加。暗号資産 venue と統合 CPU 比較は意味が薄いため、**立花単体での FD 受信スループット・メモリ使用量** を計測する。

## 4. 受け入れ条件（Phase 1 完了の定義）

1. デモ環境で `DEV_TACHIBANA_*` 設定 → debug ビルド起動 → keyring 保存 → 再起動で keyring 復元、までが手動で確認できる（demo 環境にも夜間閉局があるため、CI demo ジョブは閉局帯（demo の運用時間 = 平日 8:00–18:00 JST 想定、確定値は T2 で実機確認）を避けてスケジュールする）
2. 任意の主要銘柄（例 `7203` トヨタ）を ticker selector から選び、日足チャート + 直近 trade（FD 由来）+ 5 本気配 snapshot が表示される
3. ザラ場時間中、FD frame ストリームが `Connected` → trade イベントを継続配信できる（10 分以上連続稼働、drop なし）
4. 閉場時間に subscribe しても `Disconnected` → 「市場時間外」状態で UI が破綻しない
5. Python が異常終了しても、Rust が指数バックオフで再起動しセッションを再注入、UI から見て自動復旧する
6. **本番 URL `kabuka.e-shiten.jp` がデフォルト設定では絶対に呼ばれないこと**（CI ジョブで `grep -E "kabuka\.e-shiten"` + ユニットテストで Python 側 URL 切替ロジックを検証。pre-commit / CI 双方で同一スクリプトを呼ぶ。重複定義を避けるため正本は `tools/secret_scan.sh` に置く — T7 で実装。**ただし `BASE_URL_PROD` 定数定義ファイル 1 箇所（`python/engine/exchanges/tachibana_url.py` の先頭数行）だけは allowlist で除外**し、それ以外からのリテラル出現を全て失敗させる、F11）
7. ログとエラー応答に `sUserId` / `sPassword` / `sSecondPassword` / 仮想 URL の生値が現れないことを `tests/secret_redaction.py` で検証
8. 起動時の session 復元に失敗した場合のみ再ログインが 1 回実行され、購読開始後の `p_errno="2"` では再ログインせず UI を明示エラーに遷移させる

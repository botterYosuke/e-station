# 立花証券統合: 仕様

## 1. ゴール

本アプリの venue として立花証券 e支店 API を追加し、**日本株のチャート閲覧体験を既存暗号資産 venue と同じ UI で提供する**。Phase 1 はリードオンリー（閲覧のみ）。

## 2. スコープ（Phase 1 = MVP）

### 2.1 含めるもの

- **新 venue `Venue::Tachibana`**（[exchange/src/adapter.rs L264](../../../exchange/src/adapter.rs#L264) に追加）
- **新 `MarketKind::Stock`**（株式現物市場。信用は内部的に同じ market でハンドル）
- **新 `Exchange::TachibanaStock`**
- **Python 実装** `python/engine/exchanges/tachibana.py`（`ExchangeWorker` 実装、デモ環境のみ）
  - 認証フロー（`CLMAuthLoginRequest` → 5 つの仮想 URL を取得 → `TachibanaSession` 保持）
  - ティッカー一覧（`CLMEventDownload` の `CLMIssueMstKabu` から銘柄マスタを取り出す）
  - ティッカーメタデータ（呼値単位・売買単位・銘柄名）— `CLMMfdsGetIssueDetail` または銘柄マスタから合成
  - 日足 kline 履歴（`CLMMfdsGetMarketPriceHistory`）
  - 24h ticker stats 相当（`CLMMfdsGetMarketPrice` のスナップショットから派生）
  - **取引（FD frame）ストリーム**: EVENT WebSocket (`sUrlEventWebSocket`) で `p_evt_cmd=FD` を購読 → 現値変化を 1 件 = 1 trade として配信（`p_*_DPP` フィールド）
  - **板スナップショット**: `CLMMfdsGetMarketPrice` を周期的にポーリングし `DepthSnapshot` として配信（diff/L2 はサポートしない、§3 参照）
- **Rust 側の最小変更**:
  - `Venue` / `Exchange` / `MarketKind` 拡張
  - `TachibanaCredentials` 型と keyring 永続化（[data/src/config/](../../../data/src/config/) 配下、`tachibana.rs` 新設）
  - 起動時にクレデンシャルを Python へ渡す IPC コマンド `SetVenueCredentials` と、再ログイン後 session を Rust へ返す `VenueCredentialsRefreshed`
  - `TickerInfo`・`Exchange::price_step` 等で立花特有の呼値単位を反映
  - `MarketKind::Stock` 追加に伴う UI / indicator / timeframe / market filter / 表示ラベルの網羅修正
- **デモ環境のみ**: `https://demo-kabuka.e-shiten.jp/e_api_v4r8/`
- **debug ビルドの env 自動ログイン**: `DEV_TACHIBANA_USER_ID` / `DEV_TACHIBANA_PASSWORD` / `DEV_TACHIBANA_SECOND_PASSWORD` / `DEV_TACHIBANA_DEMO=true`（venue prefix を付けて将来の他 venue ID と衝突させない方針）。**SKILL.md S2/S3 の `DEV_USER_ID` / `DEV_PASSWORD` 系は架空ファイル `src/screen/login.rs` 等を前提にした旧表記**であり、本計画では採用しない。T0 で SKILL.md 側を本計画の env 名に同期させる

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

### 2.3 ストレッチゴール（同フェーズ内で時間が許せば）

- ザラ場時間帯の判定（JST 9:00–11:30 前場 / 12:30–15:25 後場連続 / **15:25–15:30 クロージング・オークション**、**2024-11-05 以降の現行東証取引時間**）。**`Connected` を維持するのは 9:00–15:30 全体**で、クロージング・オークション中は気配がほぼ動かなくても「市場時間外」UI を出さない。閉場（〜9:00 / 11:30〜12:30 / 15:30〜）でのみ subscribe を `Disconnected{reason:"market_closed"}` で停止する省電力モードを有効化。営業日カレンダーは可能なら `CLMDateZyouhou`（マスタ）から動的に取得し、ハードコード回避
- マスタファイル（21MB ストリーム）のローカルキャッシュ（日次更新）

## 3. 非機能要件

### 3.1 セキュリティ

- ユーザー ID / パスワード / 第二暗証番号 / 仮想 URL 5 種は **すべて機密**（[SKILL.md R10](../../../.claude/skills/tachibana/SKILL.md)）
  - Rust 側で OS keyring に保存（`data::config::tachibana`、新設）
  - Python 側に渡すのは IPC ハンドシェイク後の **`SetVenueCredentials` コマンド**（stdin / 環境変数を使わない、ログ出力でマスク）
  - Python 側は **メモリのみ**で保持。ディスクには書かない
- ログ出力時は仮想 URL のホスト部分まで `***` マスク（プロセス再起動時に keyring から復元するため、URL がリークしても session 侵害にはなりうる）
- `DEV_TACHIBANA_*` env は `#[cfg(debug_assertions)]` ブロックでのみ参照（release では完全除外）

### 3.2 セッション寿命と復旧

- 仮想 URL は **夜間閉局までの 1 日券**。閉局後は電話認証からやり直し（自動化不可）
- セッション切れ（`p_errno="2"`）検知時:
  - Python 側は `EngineError{code:"tachibana_session_expired"}` を発出して全立花購読を停止
  - Rust 側は UI に「立花のセッションが切れました。再ログインしてください」バナー表示
  - **購読中の runtime では自動再ログインを試みない**（電話認証が前提のため）。定期的な `validate_session` ポーリングも**実装しない**（runtime 中に切れを検知した場合の対処が再ログイン禁止と矛盾するため、検知は subscribe 経路の `p_errno=2` だけに任せる）
  - ただし **アプリ起動直後の session 復元フェーズ**に限り、keyring 上の session 検証が失敗した場合は `user_id/password` による再ログインを 1 回だけ試してよい。ここで成功した session を再永続化する
- **WebSocket 死活監視**: EVENT WS は **5 秒周期で `p_evt_cmd=KP`（KeepAlive）frame** を送ってくる。Python 側は KP 受信をタイマリセットに使い、**12 秒（KP 2 回欠損相当 + 2 秒 jitter）以上 KP も含めて全 frame が来なければ切断とみなして再接続**する。`Ping` フレームは `tokio-tungstenite` 等のライブラリ自動応答に頼らず手動で `Pong` を返す（SKILL.md EVENT 規約）
- `VenueReady{venue}` は **冪等イベント**として扱う。session が新たに validate / 再ログインされるたびに送ってよく、Rust 側は最後に受信した状態を保持する。`Disconnected{venue}`（プロセス再起動・WS 切断）受信で `VenueReady` 状態をリセットし、再 ready 後に既存購読の重複再送を行わないこと（`ProcessManager` 側で active subscriptions を 1 度だけ resubscribe する）
- Python 単独再起動時（[docs/plan/✅python-data-engine/spec.md](../✅python-data-engine/spec.md) §5.3 Python プロセス復旧プロトコル）は、**`ProcessManager` が source of truth になって** `SetProxy` に続けて `SetVenueCredentials` を再送し、`VenueReady` を待ってから metadata fetch / resubscribe を再開する

### 3.3 整合性 / レイテンシ

- FD frame 受信→ Rust 描画キュー投入: 中央値 < 50ms（株式は暗号資産より頻度が低いので緩め）
- 板スナップショット polling 周期: 既定 1 秒、UI フォーカス pane のみ。バックグラウンド pane は 5 秒
- マスタダウンロードは **起動時 1 回 + 日次更新のみ**、各 ticker subscribe ごとには走らせない
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
6. **本番 URL `kabuka.e-shiten.jp` がデフォルト設定では絶対に呼ばれないこと**（CI ジョブで `grep -E "kabuka\.e-shiten"` + ユニットテストで Python 側 URL 切替ロジックを検証。pre-commit / CI 双方で同一スクリプトを呼ぶこと。重複定義を避けるため正本は `tools/secret_scan.sh` に置く — T7 で実装）
7. ログとエラー応答に `sUserId` / `sPassword` / `sSecondPassword` / 仮想 URL の生値が現れないことを `tests/secret_redaction.py` で検証
8. 起動時の session 復元に失敗した場合のみ再ログインが 1 回実行され、購読開始後の `p_errno="2"` では再ログインせず UI を明示エラーに遷移させる

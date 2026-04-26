# 立花証券統合: 未確定事項

| # | 論点 | 候補 | 決定タイミング | 備考 |
| :-- | :--- | :--- | :--- | :--- |
| Q1 | クレデンシャル IPC コマンド名 | `SetVenueCredentials`（汎用）/ `SetTachibanaCredentials`（venue 固有） | T0 | **決定: 前者（generic + venue-tagged typed enum）**。payload は `serde_json::Value` ではなく `VenueCredentialsPayload::Tachibana(TachibanaCredentialsWire)` で typed に持たせ、`Debug` マスク可能にする。`request_id` で `VenueReady`/`VenueError` と相関（F1/F6） |
| Q2 | `MinTicksize` の価格帯対応 | (A) 最小値固定 / (B) 型拡張 / (C) 動的更新 | T4 | Phase 1 リードオンリーなら (A) で十分。Phase 2 で再検討（[data-mapping.md §5](./data-mapping.md#5-ticker-metadata呼値売買単位)） |
| Q3 | 「diff のない venue」表現 | capabilities フラグ / stream-kind 追加 / 既存 DepthSnapshot の繰返しで運用 | T0 | **実装確認の結果、Phase 1 は `DepthSnapshot` の繰返しだけで成立見込み**。capabilities は主に UI 非活性化用途として残す |
| Q4 | EVENT は HTTP long-poll か WebSocket か | (a) WS のみ / (b) フォールバック付き | T5 | サンプル WS 版のほうが軽量で構築が単純。**推奨: WS のみ**。HTTP long-poll は閉鎖環境向けの予備なので Phase 1 では切り捨て |
| Q5 | 銘柄マスタ 21MB の保管場所 | メモリ展開のみ / アプリのキャッシュディレクトリに永続 | T4 | 起動時間と再ログイン頻度のトレードオフ。**推奨: 日付つきファイルでキャッシュ + 起動時に当日分なければ再取得** |
| Q6 | 現物 / 信用の UI 区別 | しない（同一 ticker） / pane 単位で切替 | Phase 2 | Phase 1 はリードオンリーなので「区別不要」。発注時のみ重要 |
| Q7 | release ビルドでの本番接続許可方法 | `TACHIBANA_ALLOW_PROD=1` env / 設定ファイル / 完全禁止 | T7 | **決定（M8 改訂）**: `TACHIBANA_ALLOW_PROD=1` env を立てた起動セッションに限り、**Python tkinter ログインダイアログ内にデモ/本番ラジオを描画**して都度選択させる。env 無し → デモ固定（ラジオ非表示）。本番選択時は二段警告 modal で確認。メイン画面・設定からの常時 UI 露出は引き続きしない |
| Q8 | 立花レート制限値 | サンプル準拠（3 秒リトライ）/ 公式値（不明） | T2 | サンプル `e_api_get_master_tel.py` の `time.sleep(3)` を上限の代理として採用、公式値が判明したら更新 |
| Q9 | 銘柄セレクタの絞り込み UX | 全件表示 / 市場別 / 上場区分別 | T4 | 数千銘柄を一気に出すと UI 重い。インクリメンタル検索（コード or 名前先頭一致）を入れる |
| Q10 | JST 表示と UTC ms 内部表現の境界 | チャート軸ラベルだけ JST / 全データ JST 化 | T4 | 既存暗号資産は UTC。venue ごとに表示タイムゾーンを切替できる仕組みが必要かは要 UX 議論 |
| Q11 | 第二暗証番号は Phase 1 で受け取るか | (a) keyring + Python メモリ保持 / (b) スキーマだけ切って収集も保持もしない / (c) Phase 2 で追加 | T3 | **決定: (b)**（F-H5、レビュー指摘で更新）。`Option<SecretString>` を DTO に切るが Phase 1 では Rust UI が値を収集せず常に `None` を送る。Python は値を保持しない。発注しないものを保持して攻撃面（コアダンプ・スワップ・GC 残存）を増やさないため。Phase 2 で値の収集・保持を有効化（スキーマ破壊変更なし） |
| Q12 | 当日統計の表記 | "Daily Change" 固定 / venue 別ラベル | T4 | UI 文言の都合。i18n の問題でもあるので軽め |
| Q13 | SKILL.md の架空ファイル参照と env 名の扱い | (a) T0 で実在パス + 新 env 名へ書き換え / (b) SKILL.md は仕様抽象として残す | T0 | SKILL.md は `exchange/src/adapter/tachibana.rs` / `data/src/config/tachibana.rs` / `src/screen/login.rs` / `src/connector/auth.rs` / `src/replay_api.rs` を実在として記述、かつ env 名 `DEV_USER_ID` 系を使用しているが、いずれも git 履歴に存在しない。**決定: (a)** 実在パスと新 env 名 `DEV_TACHIBANA_*` で T0 実施（implementation-plan T0.2 に明記） |
| Q14 | 英字混在 5 桁 ticker（例 `130A0`）対応 | 実データ確認のみ / 万一落ちたら修正 | T4 | 既存 `Ticker::new` は ASCII 英数字を受け入れるため、設計論点というより受け入れ確認に近い |
| Q15 | ザラ場時間のソース | ハードコード（9:00–11:30 / 12:30–15:30）/ `CLMDateZyouhou` から動的取得 | T5 | 2024-11-05 の延長を踏まえると、将来の取引時間変更を吸収するためマスタ動的取得が望ましい。**推奨: Phase 1 はハードコード、Phase 2 で動的化** |
| Q16 | 日本語銘柄名の IPC 伝搬方法 | `TickerInfo` 拡張 / `TickerInfo` event payload 拡張 / UI 側別キャッシュ | T0 | 既存 `display_symbol` は ASCII 制約あり。**決定: `EngineEvent::TickerInfo.tickers[*]` の各 ticker dict（現状 `Vec<serde_json::Value>`）に Python 側が `display_name_ja: Option<String>` キーを詰める方式で運搬する（型新設なし）。Rust UI 側は `HashMap<Ticker, TickerDisplayMeta>` で別管理し、`TickerInfo` の Hash には含めない**（implementation-plan T0.2 / README.md L58 に明記。旧記述の `engine-client::dto::TickerListed` 型は存在しないため不採用） |
| Q17 | `NotImplementedError` の表現 | 現行 `not_implemented` のまま / venue 専用コード追加 | T6 | server 実装は一律 `not_implemented` を返す。Phase 1 は現行踏襲、専用コードは必要性が出てから |
| Q18 | `TickerInfo` フィールド追加による Hash 影響 | (a) フィールド追加で永続 state を migrate / (b) 別 struct に分離 / (c) `quote_currency` を enum 化して破壊変更を最小化 | T0 | `TickerInfo` は `#[derive(Hash, Eq)]` で `HashMap` キーとして使われる。**決定: (c)** `QuoteCurrency` を `Option<QuoteCurrency>` で追加し（F-M6a）、`None` 復元時は venue 由来値で正規化。永続 state は T0 で旧 `state.json` 起動テスト必須（F-M4） |
| Q19 | trade `side` 推定アルゴリズム | (a) 直前 bid/ask との比較のみ（quote rule） / (b) Lee-Ready (quote rule + tick rule fallback) / (c) UI で常に neutral 色表示 | T5 | チャート上で buy/sell カラーを正しく出すため。**決定: (b)** quote rule を主とし、中値ぴったりは直前 trade との tick rule にフォールバック（data-mapping.md §3 に明記） |
| Q20 | Shift-JIS デコード時の不正バイト | `errors="ignore"`（脱落） / `errors="replace"`（`?` 表示） / `errors="strict"`（例外） | T1 | 銘柄名の一部脱落は ticker selector 検索に支障。**推奨: `errors="replace"`** で `?` を出して表示の存在を残す。エラーメッセージ系は `replace` のままで可 |
| Q21 | demo 環境の運用時間と CI スケジュール | 平日 8:00–18:00 JST 想定 / 公式値（不明） | T2 | demo にも夜間閉局あり。CI demo ジョブが閉局時に走ると毎回 fail。T2 でログイン応答などから運用時間を実機確認し、決まり次第 spec.md §4 / implementation-plan T7 を更新 |
| Q22 | FD trade side 推定の quote 基準 | 当該 frame の bid/ask / 前 frame の bid/ask | T5 | FD は DPP と GAK/GBK が同一 frame で同時更新される。**決定: 前 frame bid/ask を保持して比較**（data-mapping §3、F3） |
| Q23 | FD 初回 frame の扱い | trade 発火する / 発火しない | T5 | 初回は `prev_dv=None` / `prev_quote=None` のため qty も side も判定不能。**決定: 初回 frame は trade 発火せず、2 件目以降で合成開始**（F4） |
| Q24 | `sequence_id` リセット時の整合性 | 厳格連続 / `stream_session_id` 更新時はリセット許可 | T5 | Python 再起動で local counter が 0 に戻る。**決定: `stream_session_id` 切替時は消費側 gap-detector がリセット**（F7） |
| Q25 | `VenueReady` の完了境界 | session 検証完了のみ / マスタ DL 完了も含む | T0 | **決定: session 検証のみ**。マスタ DL は `ListTickers` 応答で判定（F12） |
| Q26 | venue エラーの DTO | `EngineError{code:"tachibana_*"}` / venue-scoped `VenueError{venue, code, ...}` | T0 | **決定: `VenueError`** に統一。venue 名をコード文字列に埋め込まない（F1） |
| Q27 | `Ready.capabilities.venue_capabilities` の型表現 | typed struct / `serde_json::Value` のまま | T0 | 既存 `Ready.capabilities` は `serde_json::Value`（`engine-client/src/dto.rs::EngineEvent::Ready`）。**決定: Phase 1 は `Value` のまま、Python 側で生成・Rust はパスを deserialize で読む**。typed 化は Phase 2（F-M8）。capabilities 抽出ヘルパ `venue_capability<T>(value, venue, key)` は T0.2 で集約済み。本論点は T0.2 完了をもって閉鎖 |
| Q28 | `TachibanaSession.expires_at_ms` の決定方法 | 当日 19:00 JST ハードコード / `CLMDateZyouhou` 動的 / `Option<i64>` の None 許容 | T2 | 立花 API は明示的な期限を返さない。**決定: `Option<i64>`、ログイン直後は `None`、`None` のとき起動時 validation 必須**（F-B3）。`CLMDateZyouhou` 経由の動的取得は Phase 2 へ繰越 |
| Q29 | `secrecy` クレートの導入と Serialize 経路 | `secrecy` + 2 層 DTO（内部 `SecretString` / 送出 plain `String`） / 自前マスク手実装 | T0 | `SecretString` は `Serialize` を実装しない。**決定: `secrecy = "0.8"` を導入し、`*Wire` 送出用 DTO を別途定義**（F-B1, F-B2、architecture.md §2.1） |
| Q30 | バナー文言の所在 | (a) Rust UI 側で `code` ごとに固定文言 / (b) Python 側で `message` に user-facing 文言を詰めて送る | T6 | venue 固有事情を venue コードが持つ原則に従い、**決定: (b)** Python 発信。Rust UI は `message` をそのまま描画し固定文言を持たない（F-Banner1、architecture.md §6） |
| Q31 | ログイン画面の所在 | (a) Rust iced で実装 / (b) Python が UI ツリー DSL を送り Rust が汎用レンダラで描画 / (c) Python が独立 GUI ライブラリ（tkinter）でウィンドウを開く | T0 | venue 固有 UI を venue コードに閉じ込める原則を最大化。**決定: (c)** Python が tkinter で独立ウィンドウを開く。Rust UI は立花のログイン画面コードを持たない（F-Login1、architecture.md §7） |
| Q32 | Python GUI ライブラリ選定 | tkinter / Kivy / PySide6 / DearPyGui / Toga / Flet | T0 | 追加バイナリサイズ・日本語 IME・asyncio 互換・FOSS 性で評価。**決定: tkinter（標準ライブラリ）**。理由は架空依存ゼロ・全 OS で IME OK・短命ダイアログに十分（architecture.md §7.2）。モダン外観が必要になったら CustomTkinter を追加採用 |
| Q33 | tkinter とデータエンジン asyncio の共存 | (a) 同一プロセスで tkinter root を asyncio に統合 / (b) ログインヘルパーを別 subprocess に隔離 | T0 | tkinter は main thread 占有。**決定: (b)** `python -m engine.exchanges.tachibana_login_dialog` を `asyncio.create_subprocess_exec` で spawn し stdout/stdin で creds をやり取り（architecture.md §7.3） |
| Q34 | iced + tkinter の 2 ウィンドウ同居の許容 | (a) GUI 一貫性のため避ける / (b) 許容する | T0 | **決定: (b) 許容**。ユーザーから明示の許諾あり。判断軸は「venue 固有 UI を venue コードに閉じ込めること」と「将来の Python 単独モード移行コスト低減」（README.md §長期方針） |
| Q35 | 将来の Python 単独モード対応 | (a) Phase 1 では考慮しない / (b) Python に置く実装は Rust 非依存に保ち再利用可能にする | 設計指針 | **決定: (b)**。venue 固有の認証・パース・tkinter ログイン UI・バナー文言は Python に集約し、`engine-client` IPC を経由せずに直接呼べる構造を維持。Python 単独モード本体は別計画で扱う（README.md §長期方針） |

| Q36 | `Timeframe` の serde 形式 | (a) 既存の derive 任せ（`"D1"`） / (b) `#[serde(rename = "...")]` で `Display` と一致させる | T0 | レビュー指摘 F-H1。**決定: (b)**。capabilities (`["1d"]`) と既存 `Display` (`"1d"`) に揃える。既存暗号資産 venue 経路に IPC 形式変更が波及するため `cargo test --workspace` で回帰確認必須 |
| Q37 | マスタ DL の kick タイミング | (a) `VenueReady` に含める / (b) `VenueReady` 直後に非同期 kick / (c) 初回 `ListTickers` で lazy | T4 | レビュー指摘 F-H6。**決定: (b)**。`VenueReady` 受信直後に `_ensure_master_loaded()` を `asyncio.create_task` で kick、`list_tickers` 等は内部で `await` する。`VenueReady` 自体は session 検証完了のみを意味する原則は維持 |
| Q38 | 祝日のフェイルセーフ | (a) Phase 1 では考慮せず取引所エラーを `VenueError` に流す / (b) 「市場休業」相当エラーを検出して `Disconnected{reason:"market_closed"}` に倒す | T5 | レビュー指摘 F-M5a。**決定: (b)**。誤判定防止のため対象は明示的なエラーコードのみ。動的祝日カレンダーは引き続き Phase 2 |
| Q39 | `BASE_URL_PROD` 定数の所在 | (a) Python 側 1 ファイル限定 / (b) Rust と Python 両方に持つ | T0 | レビュー指摘 F-L1。**決定: (a)** `python/engine/exchanges/tachibana_url.py` のみ。Rust 側は本番 URL リテラルを持たず、`tools/secret_scan.sh` の allowlist もこの 1 ファイルのみ |
| Q40 | `phone_auth_required` の発火経路（実機調査必要） | (a) 立花 API のどの応答フィールド（`p_errno` / `sResultCode` / message 文字列）で電話認証未済を検出するか確定する / (b) 検出経路が無いことを公式に確認し dead code として削除 / (c) 防御的に table 登録を残しつつ実機調査を後続イテレーションへ繰越 | Phase O1 | MEDIUM-8 (ラウンド 6 強制修正 / Group F)。現状 `engine-client/src/error.rs::classify_venue_error` のテーブルに `phone_auth_required` が登録されているが、Python 側 (`python/engine/exchanges/tachibana_auth.py` / `server.py`) で該当 code を生成する経路が **存在しない** — 実機ログイン時に電話認証画面が出るケースを再現できる環境（demo or prod）で `p_errno` / `sResultCode` / `sCLMID` の応答コードを採取し、Python 側 emitter を追加した上で本テーブルを生かすか、削除するかを決める。**現時点では (c)** で運用、Phase O1 の実機調査タスクとして繰越。`docs/plan/tachibana/architecture.md §6` 失敗モード表にも同旨を明記済み |
| Q41 | panic backtrace の secret 漏出ガード | (a) `std::panic::set_hook` で `password` / `session.url_*` を含むフレームをマスクし、Python supervisor 側の traceback も `_redact_secrets` 経由で stderr 出力する規約を導入 / (b) Phase 1 では現行（標準 panic hook）のまま受容 / (c) Rust 側のみ hook を入れ Python supervisor は別タスクで扱う | Phase O1 | LOW Finding（ラウンド 6 残）。`engine-client/src/dto.rs::EngineEvent::Disconnected` の reason 文字列や立花 `sUrl*` 系仮想 URL が panic backtrace に焼き付くと keyring/HMAC で守った前提が崩れる。`test_panic_hook_redacts_virtual_url` を pin する形で (a) を採用候補とし、Phase O1 で実装方針を確定 |
| Q42 | nautilus 互換境界 lint の CI 組込 | (a) 立花用語（`sCLMID` / `p_eda_no` / `sUrl*` / `p_no` / `sJsonOfmt`）の境界漏出を CI grep ゲートとして強制化 / (b) Phase 1 ではコードレビューのみで運用 / (c) ローカル `tools/secret_scan.sh` 系スクリプトに統合し CI からは呼ばない | Phase O1 | LOW Finding（ラウンド 6 残）。対象 path: `engine-client/src/**` `exchange/src/**` `src/**`、除外: `python/**`。yml ファイル名（例: `.github/workflows/tachibana_boundary_lint.yml`）と禁止語リストの所在（`docs/plan/tachibana/` か `tools/` か）を Phase O1 で確定。`engine-client/src/dto.rs::TickerInfo` など nautilus 互換の DTO 境界に立花用語が漏れていないことを CI でガードする |
| Q43 | `PNoCounter` プロセス再起動またぎの単調性 | (a) 前回保存値 +1 で初期化（永続ファイルから読み戻し）し wall-clock 巻き戻しに依存しない / (b) 巻き戻しケースは Phase O1 TODO として明示し Phase 1 では `time.time_ns()` 由来の初期値で許容 / (c) 起動時のみ NTP 同期確認を入れる | Phase O1 | LOW Finding（ラウンド 6 残）。Phase 2 発注時の冪等性に直結するが Phase 1 リードオンリーでは表面化しない。`test_pno_counter_init_when_clock_goes_backwards` を候補テストとして pin し、Phase O1 で (a)/(b) を確定。立花 `p_no` の単調性要件は `docs/plan/tachibana/architecture.md` 側にも反映予定 |

## 決定済み（参考）

- [x] **本番 URL は Phase 1 では UI 露出しない** — env でのみ許可（[spec.md §2.2](./spec.md#22-含めないもの明示的に-phase-2-送り)）
- [x] **`MarketKind::Stock` を新設**（既存 Spot は暗号資産現物と意味的に異なるため流用しない）
- [x] **NativeBackend は実装しない**（最初から `EngineClientBackend` のみ、[architecture.md §1](./architecture.md#1-配置原則)）
- [x] **Phase 1 はリードオンリー**（[spec.md §2.2](./spec.md#22-含めないもの明示的に-phase-2-送り)）
- [x] **電話認証はアプリの関与外**（ユーザーが事前完了している前提）
- [x] **runtime の session expiry では自動再ログインしない**（起動時 session 復元失敗時のみ 1 回 fallback 可）
- [x] **第二暗証番号は Phase 1 では収集も保持もしない**（F-H5、Q11 改訂）。DTO スキーマには `Option<SecretString>` を切るが Rust UI / keyring / Python メモリのいずれにも値を入れず常に `None` を送る。Phase 2 着手時に値の収集・保持を有効化（スキーマ破壊変更なし）
- [x] **managed mode の credentials 再注入は `ProcessManager` を source of truth にする**
- [x] **立花 venue の metadata fetch / subscribe は `VenueReady` 後にのみ許可する**

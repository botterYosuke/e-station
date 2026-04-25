# 立花証券統合: 未確定事項

| # | 論点 | 候補 | 決定タイミング | 備考 |
| :-- | :--- | :--- | :--- | :--- |
| Q1 | クレデンシャル IPC コマンド名 | `SetVenueCredentials`（汎用）/ `SetTachibanaCredentials`（venue 固有） | T0 | **決定: 前者（generic + venue-tagged typed enum）**。payload は `serde_json::Value` ではなく `VenueCredentialsPayload::Tachibana(TachibanaCredentialsWire)` で typed に持たせ、`Debug` マスク可能にする。`request_id` で `VenueReady`/`VenueError` と相関（F1/F6） |
| Q2 | `MinTicksize` の価格帯対応 | (A) 最小値固定 / (B) 型拡張 / (C) 動的更新 | T4 | Phase 1 リードオンリーなら (A) で十分。Phase 2 で再検討（[data-mapping.md §5](./data-mapping.md#5-ticker-metadata呼値売買単位)） |
| Q3 | 「diff のない venue」表現 | capabilities フラグ / stream-kind 追加 / 既存 DepthSnapshot の繰返しで運用 | T0 | **実装確認の結果、Phase 1 は `DepthSnapshot` の繰返しだけで成立見込み**。capabilities は主に UI 非活性化用途として残す |
| Q4 | EVENT は HTTP long-poll か WebSocket か | (a) WS のみ / (b) フォールバック付き | T5 | サンプル WS 版のほうが軽量で構築が単純。**推奨: WS のみ**。HTTP long-poll は閉鎖環境向けの予備なので Phase 1 では切り捨て |
| Q5 | 銘柄マスタ 21MB の保管場所 | メモリ展開のみ / アプリのキャッシュディレクトリに永続 | T4 | 起動時間と再ログイン頻度のトレードオフ。**推奨: 日付つきファイルでキャッシュ + 起動時に当日分なければ再取得** |
| Q6 | 現物 / 信用の UI 区別 | しない（同一 ticker） / pane 単位で切替 | Phase 2 | Phase 1 はリードオンリーなので「区別不要」。発注時のみ重要 |
| Q7 | release ビルドでの本番接続許可方法 | `TACHIBANA_ALLOW_PROD=1` env / 設定ファイル / 完全禁止 | T7 | 暫定: env でのみ許可（GUI には出さない）。UI 露出は別議論 |
| Q8 | 立花レート制限値 | サンプル準拠（3 秒リトライ）/ 公式値（不明） | T2 | サンプル `e_api_get_master_tel.py` の `time.sleep(3)` を上限の代理として採用、公式値が判明したら更新 |
| Q9 | 銘柄セレクタの絞り込み UX | 全件表示 / 市場別 / 上場区分別 | T4 | 数千銘柄を一気に出すと UI 重い。インクリメンタル検索（コード or 名前先頭一致）を入れる |
| Q10 | JST 表示と UTC ms 内部表現の境界 | チャート軸ラベルだけ JST / 全データ JST 化 | T4 | 既存暗号資産は UTC。venue ごとに表示タイムゾーンを切替できる仕組みが必要かは要 UX 議論 |
| Q11 | 第二暗証番号は Phase 1 で受け取るか | はい（keyring + Python メモリ保持、未使用） / いいえ（Phase 2 で追加） | T3 | **決定寄り**: 他文書は「Phase 1 から受け取る」前提で揃える。後続の発注フェーズでスキーマ移行を避けるため |
| Q12 | 当日統計の表記 | "Daily Change" 固定 / venue 別ラベル | T4 | UI 文言の都合。i18n の問題でもあるので軽め |
| Q13 | SKILL.md の架空ファイル参照と env 名の扱い | (a) T0 で実在パス + 新 env 名へ書き換え / (b) SKILL.md は仕様抽象として残す | T0 | SKILL.md は `exchange/src/adapter/tachibana.rs` / `data/src/config/tachibana.rs` / `src/screen/login.rs` / `src/connector/auth.rs` / `src/replay_api.rs` を実在として記述、かつ env 名 `DEV_USER_ID` 系を使用しているが、いずれも git 履歴に存在しない。**決定: (a)** 実在パスと新 env 名 `DEV_TACHIBANA_*` で T0 実施（implementation-plan T0.2 に明記） |
| Q14 | 英字混在 5 桁 ticker（例 `130A0`）対応 | 実データ確認のみ / 万一落ちたら修正 | T4 | 既存 `Ticker::new` は ASCII 英数字を受け入れるため、設計論点というより受け入れ確認に近い |
| Q15 | ザラ場時間のソース | ハードコード（9:00–11:30 / 12:30–15:30）/ `CLMDateZyouhou` から動的取得 | T5 | 2024-11-05 の延長を踏まえると、将来の取引時間変更を吸収するためマスタ動的取得が望ましい。**推奨: Phase 1 はハードコード、Phase 2 で動的化** |
| Q16 | 日本語銘柄名の IPC 伝搬方法 | `TickerInfo` 拡張 / `TickerInfo` event payload 拡張 / UI 側別キャッシュ | T0 | 既存 `display_symbol` は ASCII 制約あり。**決定: `engine-client::dto::TickerListed` / `TickerMetadata` 応答に `display_name_ja: Option<String>` を追加し、Rust UI 側は `HashMap<Ticker, TickerDisplayMeta>` で別管理。`TickerInfo` の Hash には含めない**（implementation-plan T0.2 に明記） |
| Q17 | `NotImplementedError` の表現 | 現行 `not_implemented` のまま / venue 専用コード追加 | T6 | server 実装は一律 `not_implemented` を返す。Phase 1 は現行踏襲、専用コードは必要性が出てから |
| Q18 | `TickerInfo` フィールド追加による Hash 影響 | (a) フィールド追加で永続 state を migrate / (b) 別 struct に分離 / (c) `quote_currency` を enum 化して破壊変更を最小化 | T0 | `TickerInfo` は `#[derive(Hash, Eq)]` で `HashMap` キーとして使われる。**決定: (c)** `QuoteCurrency` を `Option<QuoteCurrency>` で追加し（F-M6）、`None` 復元時は venue 由来値で正規化。永続 state は T0 で旧 `state.json` 起動テスト必須（F-M4） |
| Q19 | trade `side` 推定アルゴリズム | (a) 直前 bid/ask との比較のみ（quote rule） / (b) Lee-Ready (quote rule + tick rule fallback) / (c) UI で常に neutral 色表示 | T5 | チャート上で buy/sell カラーを正しく出すため。**決定: (b)** quote rule を主とし、中値ぴったりは直前 trade との tick rule にフォールバック（data-mapping.md §3 に明記） |
| Q20 | Shift-JIS デコード時の不正バイト | `errors="ignore"`（脱落） / `errors="replace"`（`?` 表示） / `errors="strict"`（例外） | T1 | 銘柄名の一部脱落は ticker selector 検索に支障。**推奨: `errors="replace"`** で `?` を出して表示の存在を残す。エラーメッセージ系は `replace` のままで可 |
| Q21 | demo 環境の運用時間と CI スケジュール | 平日 8:00–18:00 JST 想定 / 公式値（不明） | T2 | demo にも夜間閉局あり。CI demo ジョブが閉局時に走ると毎回 fail。T2 でログイン応答などから運用時間を実機確認し、決まり次第 spec.md §4 / implementation-plan T7 を更新 |
| Q22 | FD trade side 推定の quote 基準 | 当該 frame の bid/ask / 前 frame の bid/ask | T5 | FD は DPP と GAK/GBK が同一 frame で同時更新される。**決定: 前 frame bid/ask を保持して比較**（data-mapping §3、F3） |
| Q23 | FD 初回 frame の扱い | trade 発火する / 発火しない | T5 | 初回は `prev_dv=None` / `prev_quote=None` のため qty も side も判定不能。**決定: 初回 frame は trade 発火せず、2 件目以降で合成開始**（F4） |
| Q24 | `sequence_id` リセット時の整合性 | 厳格連続 / `stream_session_id` 更新時はリセット許可 | T5 | Python 再起動で local counter が 0 に戻る。**決定: `stream_session_id` 切替時は消費側 gap-detector がリセット**（F7） |
| Q25 | `VenueReady` の完了境界 | session 検証完了のみ / マスタ DL 完了も含む | T0 | **決定: session 検証のみ**。マスタ DL は `ListTickers` 応答で判定（F12） |
| Q26 | venue エラーの DTO | `EngineError{code:"tachibana_*"}` / venue-scoped `VenueError{venue, code, ...}` | T0 | **決定: `VenueError`** に統一。venue 名をコード文字列に埋め込まない（F1） |
| Q27 | `Ready.capabilities.venue_capabilities` の型表現 | typed struct / `serde_json::Value` のまま | T0 | 既存 `Ready.capabilities` は `serde_json::Value`（[engine-client/src/dto.rs:102](../../../engine-client/src/dto.rs#L102)）。**決定: Phase 1 は `Value` のまま、Python 側で生成・Rust はパスを deserialize で読む**。typed 化は Phase 2（F-M8） |
| Q28 | `TachibanaSession.expires_at_ms` の決定方法 | 当日 19:00 JST ハードコード / `CLMDateZyouhou` 動的 / `Option<i64>` の None 許容 | T2 | 立花 API は明示的な期限を返さない。**決定: `Option<i64>`、ログイン直後は `None`、`None` のとき起動時 validation 必須**（F-B3）。`CLMDateZyouhou` 経由の動的取得は Phase 2 へ繰越 |
| Q29 | `secrecy` クレートの導入と Serialize 経路 | `secrecy` + 2 層 DTO（内部 `SecretString` / 送出 plain `String`） / 自前マスク手実装 | T0 | `SecretString` は `Serialize` を実装しない。**決定: `secrecy = "0.8"` を導入し、`*Wire` 送出用 DTO を別途定義**（F-B1, F-B2、architecture.md §2.1） |
| Q30 | バナー文言の所在 | (a) Rust UI 側で `code` ごとに固定文言 / (b) Python 側で `message` に user-facing 文言を詰めて送る | T6 | venue 固有事情を venue コードが持つ原則に従い、**決定: (b)** Python 発信。Rust UI は `message` をそのまま描画し固定文言を持たない（F-Banner1、architecture.md §6） |
| Q31 | ログイン画面の所在 | (a) Rust iced で実装 / (b) Python が UI ツリー DSL を送り Rust が汎用レンダラで描画 / (c) Python が独立 GUI ライブラリ（tkinter）でウィンドウを開く | T0 | venue 固有 UI を venue コードに閉じ込める原則を最大化。**決定: (c)** Python が tkinter で独立ウィンドウを開く。Rust UI は立花のログイン画面コードを持たない（F-Login1、architecture.md §7） |
| Q32 | Python GUI ライブラリ選定 | tkinter / Kivy / PySide6 / DearPyGui / Toga / Flet | T0 | 追加バイナリサイズ・日本語 IME・asyncio 互換・FOSS 性で評価。**決定: tkinter（標準ライブラリ）**。理由は架空依存ゼロ・全 OS で IME OK・短命ダイアログに十分（architecture.md §7.2）。モダン外観が必要になったら CustomTkinter を追加採用 |
| Q33 | tkinter とデータエンジン asyncio の共存 | (a) 同一プロセスで tkinter root を asyncio に統合 / (b) ログインヘルパーを別 subprocess に隔離 | T0 | tkinter は main thread 占有。**決定: (b)** `python -m engine.exchanges.tachibana_login_dialog` を `asyncio.create_subprocess_exec` で spawn し stdout/stdin で creds をやり取り（architecture.md §7.3） |
| Q34 | iced + tkinter の 2 ウィンドウ同居の許容 | (a) GUI 一貫性のため避ける / (b) 許容する | T0 | **決定: (b) 許容**。ユーザーから明示の許諾あり。判断軸は「venue 固有 UI を venue コードに閉じ込めること」と「将来の Python 単独モード移行コスト低減」（README.md §長期方針） |
| Q35 | 将来の Python 単独モード対応 | (a) Phase 1 では考慮しない / (b) Python に置く実装は Rust 非依存に保ち再利用可能にする | 設計指針 | **決定: (b)**。venue 固有の認証・パース・tkinter ログイン UI・バナー文言は Python に集約し、`engine-client` IPC を経由せずに直接呼べる構造を維持。Python 単独モード本体は別計画で扱う（README.md §長期方針） |

## 決定済み（参考）

- [x] **本番 URL は Phase 1 では UI 露出しない** — env でのみ許可（[spec.md §2.2](./spec.md#22-含めないもの明示的に-phase-2-送り)）
- [x] **`MarketKind::Stock` を新設**（既存 Spot は暗号資産現物と意味的に異なるため流用しない）
- [x] **NativeBackend は実装しない**（最初から `EngineClientBackend` のみ、[architecture.md §1](./architecture.md#1-配置原則)）
- [x] **Phase 1 はリードオンリー**（[spec.md §2.2](./spec.md#22-含めないもの明示的に-phase-2-送り)）
- [x] **電話認証はアプリの関与外**（ユーザーが事前完了している前提）
- [x] **runtime の session expiry では自動再ログインしない**（起動時 session 復元失敗時のみ 1 回 fallback 可）
- [x] **第二暗証番号は Phase 1 から受け取って保持する**（keyring + Python メモリ。発注には使わない）
- [x] **managed mode の credentials 再注入は `ProcessManager` を source of truth にする**
- [x] **立花 venue の metadata fetch / subscribe は `VenueReady` 後にのみ許可する**

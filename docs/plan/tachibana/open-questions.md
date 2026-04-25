# 立花証券統合: 未確定事項

| # | 論点 | 候補 | 決定タイミング | 備考 |
| :-- | :--- | :--- | :--- | :--- |
| Q1 | クレデンシャル IPC コマンド名 | `SetVenueCredentials`（汎用）/ `SetTachibanaCredentials`（venue 固有） | T0 | 後者は venue 増えるたびにコマンド追加。前者は shape が venue 依存になる。**推奨: 前者（generic + venue-tagged value）** |
| Q2 | `MinTicksize` の価格帯対応 | (A) 最小値固定 / (B) 型拡張 / (C) 動的更新 | T4 | Phase 1 リードオンリーなら (A) で十分。Phase 2 で再検討（[data-mapping.md §5](./data-mapping.md#5-ticker-metadata呼値売買単位)） |
| Q3 | 「diff のない venue」表現 | capabilities フラグ / stream-kind 追加 / 既存 DepthSnapshot の繰返しで運用 | T0 | 既存の depth gap 検知ロジックが Snapshot only venue で誤動作しないかを確認 |
| Q4 | EVENT は HTTP long-poll か WebSocket か | (a) WS のみ / (b) フォールバック付き | T5 | サンプル WS 版のほうが軽量で構築が単純。**推奨: WS のみ**。HTTP long-poll は閉鎖環境向けの予備なので Phase 1 では切り捨て |
| Q5 | 銘柄マスタ 21MB の保管場所 | メモリ展開のみ / アプリのキャッシュディレクトリに永続 | T4 | 起動時間と再ログイン頻度のトレードオフ。**推奨: 日付つきファイルでキャッシュ + 起動時に当日分なければ再取得** |
| Q6 | 現物 / 信用の UI 区別 | しない（同一 ticker） / pane 単位で切替 | Phase 2 | Phase 1 はリードオンリーなので「区別不要」。発注時のみ重要 |
| Q7 | release ビルドでの本番接続許可方法 | `TACHIBANA_ALLOW_PROD=1` env / 設定ファイル / 完全禁止 | T7 | 暫定: env でのみ許可（GUI には出さない）。UI 露出は別議論 |
| Q8 | 立花レート制限値 | サンプル準拠（3 秒リトライ）/ 公式値（不明） | T2 | サンプル `e_api_get_master_tel.py` の `time.sleep(3)` を上限の代理として採用、公式値が判明したら更新 |
| Q9 | 銘柄セレクタの絞り込み UX | 全件表示 / 市場別 / 上場区分別 | T4 | 数千銘柄を一気に出すと UI 重い。インクリメンタル検索（コード or 名前先頭一致）を入れる |
| Q10 | JST 表示と UTC ms 内部表現の境界 | チャート軸ラベルだけ JST / 全データ JST 化 | T4 | 既存暗号資産は UTC。venue ごとに表示タイムゾーンを切替できる仕組みが必要かは要 UX 議論 |
| Q11 | 第二暗証番号は Phase 1 で受け取るか | はい（メモリのみ）/ いいえ（Phase 2 で追加） | T3 | UI と keyring スキーマの将来互換性に影響。**推奨: Phase 1 から受け取って保持（使わない）**。後で発注機能を追加する際にスキーマ移行が不要 |
| Q12 | 当日統計の表記 | "Daily Change" 固定 / venue 別ラベル | T4 | UI 文言の都合。i18n の問題でもあるので軽め |
| Q13 | SKILL.md の架空ファイル参照の扱い | (a) 本計画完了時に SKILL.md を実在パスへ置換 / (b) SKILL.md は仕様抽象として残し、参照は plan 側のみ更新 | T0 | SKILL.md は `exchange/src/adapter/tachibana.rs`（4,350 行）等を実在として記述しているが、git 全履歴で未確認。**推奨: (a)** 実装後に同期。それまでは plan 側に「架空参照」注記を入れる |
| Q14 | 英字混在 5 桁 ticker（例 `130A0`）対応 | (a) 既存 `Ticker::new` を英数字許容に緩和 / (b) `MarketKind::Stock` のみ別ロジック / (c) 英字混在は除外 | T0/T4 | 新興市場の優先出資証券・新株予約権付社債等で発生。ASCII 数字のみ許容ロジックがあれば緩和か除外を選ぶ。**推奨: (a)** 全 venue で英数字許容（暗号資産にも害なし） |
| Q15 | ザラ場時間のソース | ハードコード（9:00–11:30 / 12:30–15:30）/ `CLMDateZyouhou` から動的取得 | T5 | 2024-11-05 の延長を踏まえると、将来の取引時間変更を吸収するためマスタ動的取得が望ましい。**推奨: Phase 1 はハードコード、Phase 2 で動的化** |

## 決定済み（参考）

- [x] **本番 URL は Phase 1 では UI 露出しない** — env でのみ許可（[spec.md §2.2](./spec.md#22-含めないもの明示的に-phase-2-送り)）
- [x] **`MarketKind::Stock` を新設**（既存 Spot は暗号資産現物と意味的に異なるため流用しない）
- [x] **NativeBackend は実装しない**（最初から `EngineClientBackend` のみ、[architecture.md §1](./architecture.md#1-配置原則)）
- [x] **Phase 1 はリードオンリー**（[spec.md §2.2](./spec.md#22-含めないもの明示的に-phase-2-送り)）
- [x] **電話認証はアプリの関与外**（ユーザーが事前完了している前提）

# 修正計画: 立花銘柄一覧がサイドバーに表示されない 2026-04-27

## 状況: 修正実装済み

## 1. 不具合の概要

アプリ起動後、サイドバーの "Search for a ticker..." 欄に立花証券の銘柄が一切表示されない。
`VenueReady` は正常に受信されており、認証自体は成功している。

## 2. 根本原因（コード調査による確定）

### Bug 1（確定・Primary）: `MarketKind::Stock` が `selected_markets` に含まれない

`filtered_rows` は以下のフィルタで全行を評価する：

```rust
let matches_market = |row: &TickerRowData| self.selected_markets.contains(&row.ticker.market_type());
```

立花銘柄は `Exchange::TachibanaStock` → `market_type() = MarketKind::Stock`。

`saved-state.json` の `selected_markets` は `["Spot","InversePerps","LinearPerps"]` であり、
`MarketKind::Stock` が存在しない。これは **`Stock` バリアントが追加される前に保存されたファイル**が
そのまま読み込まれるため。`Settings::default()` は `MarketKind::ALL`（Stock を含む）を使うが、
デシリアライズ時に旧フォーマットに対して migration が走らない。

結果として全立花銘柄が `matches_market = false` となり、`ticker_rows` に行があっても表示が0件になる。

**証拠**:
- `saved-state.json` の実データ: `"selected_markets":["Spot","InversePerps","LinearPerps"]`
- `data/src/tickers_table.rs` L25: `selected_markets: MarketKind::ALL.into_iter().collect()`（default のみ正しい）
- `src/screen/dashboard/tickers_table.rs` L934–936: Stock フィルタボタンが UI に存在しない

---

### Bug 2（確定・Secondary）: `fetch_ticker_stats("__all__", "stock")` のバルク処理未実装

Rust 側 `fetch_ticker_stats_task` は全銘柄の stats を一括取得するため
`ticker = "__all__"` を送り、`{symbol: stats}` 形式のバルク応答を期待する：

```rust
let cmd = Command::FetchTickerStats {
    ticker: "__all__".to_string(),
    ...
};
// 受信側
if ticker == "__all__" {
    let bulk: HashMap<String, serde_json::Value> = serde_json::from_value(stats)...
```

Python の `_do_fetch_ticker_stats` はそのまま `worker.fetch_ticker_stats("__all__", "stock")` を
呼ぶが、`TachibanaWorker.fetch_ticker_stats` は単一銘柄向け実装（`CLMMfdsGetMarketPrice` を
`sTargetIssueCode="__all__"` で呼んでしまう）のため API エラーまたは空応答となる。

`UpdateMetadata` が成功して `tickers_info` に銘柄が登録されても、`update_ticker_rows` は
stats が来なければ `ticker_rows` に行を追加しない。よって **stats fetch 失敗 → ticker_rows 空**
→ 表示ゼロ になる。

**証拠**:
- `engine-client/src/backend.rs` L641–644: `ticker = "__all__"` を明示的に送信
- `python/engine/server.py` L1455: `worker.fetch_ticker_stats(ticker, ...)` をそのまま呼ぶ
- `python/engine/exchanges/tachibana.py` L524–: `fetch_ticker_stats` に `__all__` 分岐なし
- `src/screen/dashboard/tickers_table.rs` L587–626: `update_ticker_rows` は stats がないと行追加しない

---

## 3. 修正内容

### 3.1 `data/src/tickers_table.rs` — `Settings::migrate()` 追加

```rust
impl Settings {
    pub fn migrate(&mut self) {
        for kind in MarketKind::ALL {
            if !self.selected_markets.contains(&kind) {
                self.selected_markets.push(kind);
            }
        }
    }
}
```

既存 saved-state に存在しない `MarketKind` バリアント（今回は `Stock`）を自動補完する。
将来の新バリアント追加時も同じ migration コードで対応可能。

### 3.2 `src/screen/dashboard/tickers_table.rs` — `new_with_settings` で migration 呼び出し

```rust
pub fn new_with_settings(settings: &Settings, handles: AdapterHandles) -> (Self, Task<Message>) {
    let mut settings = settings.clone();
    settings.migrate();            // ← 追加
    let settings = &settings;
    // ...
}
```

### 3.3 `src/screen/dashboard/tickers_table.rs` — "Stock" フィルタボタン追加

`sort_and_filter_col` の market filter 行に `MarketKind::Stock` ボタンを追加：

```rust
let stock_market_btn = self.market_filter_btn("Stock", MarketKind::Stock);
// ...
row![
    spot_market_button.width(Length::Fill),
    linear_markets_btn.width(Length::Fill),
    inverse_markets_btn.width(Length::Fill),
    stock_market_btn.width(Length::Fill),   // ← 追加
]
```

ユーザーが Stock を明示的に on/off できるようになる。

### 3.4 `python/engine/exchanges/tachibana.py` — `fetch_ticker_stats.__all__` バルク対応

```python
async def fetch_ticker_stats(self, ticker: str, market: str = "stock") -> dict:
    await self._ensure_master_loaded()

    if ticker == "__all__":
        sizyou_rows = self._master_records.get("CLMIssueSizyouMstKabu", [])
        bulk: dict[str, Any] = {}
        for row in sizyou_rows:
            code = str(row.get("sIssueCode", "")).strip()
            if code:
                bulk[code] = {"mark_price": 0, "daily_price_chg": 0, "daily_volume": 0}
        return bulk
    # ... 以降は従来の単一銘柄処理
```

マスタ（`VenueReady` → `list_tickers` で既ロード済み）からゼロ値プレースホルダーを返す。
これにより `ticker_rows` に全銘柄が登録され、サイドバーに一覧が現れる。
実際の価格は FD ストリーム購読後に上書きされる。

## 4. なぜ既存テストで発見できなかったか

| テスト | 見逃した理由 |
|--------|------------|
| `test_server_dispatch.py` | `FetchTickerStats` を `__all__` で呼ぶケースが未カバー |
| `tickers_table` 単体テスト | `push_ticker_row_for_test` ヘルパーで直接 rows を操作しており、stats fetch パスを通らない |
| 手動実機テスト | saved-state が新しく作成された場合（default）は `Stock` が含まれるため再現しない。saved-state が残った状態での起動を試みたときのみ再現する |
| `filtered_rows` テスト | `selected_markets` が default（全種含む）で固定されており、旧フォーマット移行シナリオを試験していない |

## 5. Acceptance criteria

- [x] 1. 既存 saved-state（Stock なし）でアプリを起動しても立花銘柄が表示される
- [x] 2. "Stock" フィルタボタンが UI に表示され、on/off が機能する
- [x] 3. `fetch_ticker_stats("__all__", "stock")` がマスタ銘柄数分のプレースホルダーを返す
- [x] 4. `Settings::migrate()` のユニットテスト追加
- [x] 5. `fetch_ticker_stats.__all__` のユニットテスト追加
- [x] 6. /bug-postmortem（MISSES.md に「saved-state フォーマット migration 未実装」パターン追記）

## レビュー反映 (2026-04-27, ラウンド 1)

### 解消した指摘
- ✅ M-1: `[DBG-P1]` デバッグログ削除（CLAUDE.md 規約違反）
- ✅ H-2: `__all__` セッションスキップ意図コメント追加
- ✅ H-1: 空マスタ時 warning ログ追加（サイレント障害防止）
- ✅ M-5: `sIssueCode.strip()` 一貫性確認・対応
- ✅ M-4: `fetch_ticker_stats` 戻り値型アノテーション改善
- ✅ H-3: `Settings::migrate()` ユニットテスト 3 件追加
- ✅ H-4: `fetch_ticker_stats("__all__")` ユニットテスト 3 件追加

### 持ち越し項目
- M-2: `selected_exchanges` migrate — `selected_markets` と混同した懸念で無効。`migrate()` は `selected_markets` のみ変更。LOW に降格して追跡終了。
- M-3: `_ensure_master_loaded` が raise 時の `__all__` エラー伝播 — 既存 `_spawn_fetch` の `except Exception` で Rust に error response として届く。サイレント障害なし。解決済み。
- ✅ M-6: `/bug-postmortem` 実行・MISSES.md 追記 — 完了（2026-04-27）

## レビュー反映 (2026-04-27, ラウンド 2)

### 解消した指摘
- ✅ R2-M-1: `list_tickers()` の `sIssueCode` に `.strip()` を追加し `__all__` パスとキーを統一（Rust 側 tickers_info との不一致リスク除去）
- ✅ コメント修正: `__all__` ブランチの `.strip()` 根拠コメントを正確な説明に更新

### 確認済み（問題なし）
- AC-1〜5 全件達成
- `_ensure_master_loaded` エラー伝播: Rust に error response として届く（サイレント障害なし）
- デバッグログ: 皆無を確認

### 残存 LOW（対応不要）
- L-1: `fetch_ticker_stats -> dict[str, Any]` はシグネチャとして許容範囲（`dict[str, dict[str, Any]]` を型システム上包含）
- L-2: テストの `_download_master` 呼び出し回数未検証 — 機能影響なし
- L-3: `sIssueCode = "0"` スルー — 実データで影響未確認、現状維持

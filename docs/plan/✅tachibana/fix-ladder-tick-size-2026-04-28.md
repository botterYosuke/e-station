# Ladder 呼値（tick size）自動修正 — 修正計画

作成: 2026-04-28

## 問題

Ladder 板表示でトヨタ（7203、現値 ~3108 円）が **0.1 円刻み**で表示される。
正しい東証の呼値は **1.0 円**（3000〜5000 円帯）。

### 根本原因

`python/engine/exchanges/tachibana.py` の `list_tickers()` が
`resolve_min_ticksize_for_issue(sizyou, self._yobine_table, snapshot_price=None)` を呼ぶ。

`snapshot_price=None` の場合、`tachibana_master.py::resolve_min_ticksize_for_issue()` は
CLMYobine テーブルの **第 1 バンド（最小価格帯 = 最細刻み）** を返す仕様。

例: TOPIX100 特例呼値（yobine_code 例: "103"）
| バンド | 基準値段 | 呼値単価 |
|--------|---------|---------|
| 1      | 1000    | 0.1     |
| 2      | 3000    | 0.5     |
| 3      | 5000    | 1.0     |
| ...    | ...     | ...     |

→ 起動直後（価格不明）は第 1 バンド = **0.1 円** が `min_ticksize` として Rust に送られる。
→ `engine-client/src/backend.rs` が `min_ticksize` を起動時にキャプチャし、
　 以降変更されないため Ladder は永続的に 0.1 円刻みになる。

### 影響範囲

- `python/engine/exchanges/tachibana.py` — `list_tickers()` / `fetch_ticker_stats()`
- `engine-client/src/backend.rs` — 変更不要（`min_ticksize` は TickerInfo から取得済み）
- `data/src/panel/ladder.rs` — 変更不要

---

## 修正方針

`fetch_ticker_stats()` で取得した直近終値（`pDPP`）を `_price_cache` にキャッシュし、
`list_tickers()` 呼び出し時にキャッシュ済み価格で正しいバンドを解決する。

### なぜこのアプローチか

- `fetch_ticker_stats()` は Ladder を開く前に **必ず** 呼ばれる（チャートパネルの 24h 統計取得）
- `GetTickerMetadata` は `list_tickers()` を再呼出しするため、キャッシュがあれば正しい tick が得られる
- Python 側のみの変更で完結し、Rust・IPC スキーマの変更が不要
- `resolve_min_ticksize_for_issue(sizyou, yobine_table, snapshot_price)` は既に価格ベース解決に対応済み

> **注意**: `FetchTickerStats("__all__")` は全銘柄プレースホルダーを返すパスであり `_price_cache` を更新しない。Ladder 開始導線では個別 `FetchTickerStats(ticker)` が先行するため問題ない。

### タイミング図

```
Rust                             Python
 |                                 |
 |-- FetchTickerStats(7203) ------>|
 |                        fetch_ticker_stats() → REST CLMMfdsGetMarketPrice
 |                        → last_price = "3108"
 |                        → _price_cache["7203"] = Decimal("3108")  ← NEW
 |<-- TickerStats(last_price=3108) |
 |                                 |
 |-- GetTickerMetadata(7203) ----->|
 |                        list_tickers() → resolve_min_ticksize_for_issue(
 |                            sizyou, yobine_table,
 |                            snapshot_price=Decimal("3108")  ← NEW: キャッシュ使用
 |                        ) → 1.0 円  ✓
 |<-- TickerInfo(min_ticksize=1.0) |
 |                                 |
 |-- Subscribe(depth, min_ticksize=1.0)
 → Ladder が 1.0 円刻みで描画  ✓
```

---

## 変更ファイルと変更内容

### 1. `python/engine/exchanges/tachibana.py`

#### 1-A: `__init__` に `_price_cache` フィールドを追加

```python
# 既存コード（L152 付近）
self._p_no_counter = p_no_counter or PNoCounter()

# ↓ この行を追加
self._price_cache: dict[str, Decimal] = {}
```

#### 1-B: `fetch_ticker_stats()` で価格をキャッシュ

```python
# 既存コード（L611 付近）
first = parsed.aCLMMfdsMarketPrice[0]
return {
    "symbol": ticker,
    "last_price": str(first.get("pDPP", "")),
    ...
}
```

↓ `return` の直前に追加:

```python
# キャッシュ: 次回 list_tickers() が正しい呼値バンドを解決できるようにする
dpp_str = str(first.get("pDPP", "")).strip()
if dpp_str:
    try:
        self._price_cache[ticker] = Decimal(dpp_str)
    except InvalidOperation:
        pass
return { ... }  # 既存の return は変更なし
```

#### 1-C: `list_tickers()` でキャッシュ価格を使用

```python
# code は L372 で定義済み（for ループの sizyou から取得）
# 既存コード（L398-403）
if self._yobine_table:
    try:
        tick = resolve_min_ticksize_for_issue(sizyou, self._yobine_table, None)
        entry["min_ticksize"] = float(tick)
    except (KeyError, ValueError):
        pass
```

↓ `None` を `self._price_cache.get(code)` に変更:

```python
if self._yobine_table:
    try:
        tick = resolve_min_ticksize_for_issue(
            sizyou, self._yobine_table, self._price_cache.get(code)
        )
        entry["min_ticksize"] = float(tick)
    except (KeyError, ValueError):
        pass
```

---

## エッジケース・制約

| ケース | 挙動 |
|--------|------|
| キャッシュなし（初回起動・ticker_stats 前） | 従来通り第 1 バンド（最細刻み）にフォールバック。GetTickerMetadata 後に補正される |
| `fetch_ticker_stats` が `pDPP=""` を返す | キャッシュしない。第 1 バンドのまま |
| `fetch_ticker_stats` が例外 or 空レスポンス | キャッシュ未更新 → 第 1 バンドフォールバック（既存動作維持） |
| 株価が価格帯をまたいで大きく変動した場合 | 次回 `fetch_ticker_stats` 呼び出しでキャッシュ更新 → 次の `GetTickerMetadata` で補正 |
| 同一プロセス内のセッションリセット | `_price_cache` は保持して問題なし。プロセス再起動時はキャッシュリセットされるが、spec §3.2 の再接続シーケンスで `FetchTickerStats` → `GetTickerMetadata` の順が保たれるため第 1 バンド一時表示後に補正される |
| 同一 ticker の複数 pane | `list_tickers()` は 1 回の呼び出しで全銘柄を返すため、全 pane が同時に補正される |

---

## テスト方針

### Python ユニットテスト（`python/tests/`）

1. **`test_tachibana_price_cache.py`（新規）**
   - `fetch_ticker_stats()` を mock して `_price_cache` に価格が入ることを確認
   - `list_tickers()` がキャッシュあり/なしで異なる `min_ticksize` を返すことを確認（例: `assert result["min_ticksize"] == 1.0`）
   - TOPIX100 特例銘柄（yobine_code="103" 相当）で価格 3108 → tick=1.0 を確認
   - `fetch_ticker_stats()` が例外を投げた場合 `_price_cache` が汚染されないことを確認
   - `pDPP=""` のとき `_price_cache` にキャッシュしないことを確認
   - `InvalidOperation` 例外が catch されキャッシュしないことを確認（`pytest.mark.parametrize` 推奨）
   - `snapshot_price=None` のとき `min_ticksize == 0.1`（第 1 バンド）を assert する
   - `fetch_ticker_stats()` → `_price_cache` 更新 → `list_tickers()` の連鎖シナリオを統合テストとして含める
   - 実行コマンド: `uv run pytest python/tests/test_tachibana_price_cache.py -v`

2. **既存テストの回帰確認**
   - `uv run pytest python/tests/ -v` でグリーン維持

### 手動確認

1. `.env` に `DEV_TACHIBANA_*` 設定後 `cargo run` でアプリ起動
2. トヨタ（7203）を選択してチャート表示（`fetch_ticker_stats` 発火）
3. Ladder ペインを開く → **1.0 円刻み**で板が表示されることを確認
4. 価格が 1000 円以下の銘柄（例: 低位株）も確認 → バンドに応じた tick が出ること

---

## 非対応事項（スコープ外）

- **ストリーム実行中の動的 tick 更新**: `depth_stream()` が `min_ticksize` を起動時にキャプチャするため、ストリーム再起動なしに tick を変えることはできない。価格帯をまたぐ大変動（例: 1000 円 → 5000 円）への対応は将来フェーズ送り（`open-questions.md` に登録予定）
- **`GetTickerMetadata` での即時 REST 呼び出し**: `fetch_ticker_stats` を内部で呼ぶ方式は余分な REST コールを発生させるため採用しない
- **FD フレームからの tick 補正**: ストリーム中に `TickerInfo` を再送しても `depth_stream()` のキャプチャ済み値には影響しないため実施しない

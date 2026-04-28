# Fix: 買余力 ¥0 表示 — フィールド名の不一致 (2026-04-28)

## 症状

デモ口座（初期資金 2000万円）にログインしても、買余力パネルが `現物余力: ¥0 / 信用余力: ¥0` と表示される。

## 原因

**H2: APIレスポンスのフィールド名が期待と異なる（確定）**

`CLMZanKaiKanougaku` / `CLMZanShinkiKanoIjiritu` の `sJsonOfmt="5"` レスポンスは
旧仕様フィールド名ではなく Summary 系フィールドを返す。

| エンドポイント | 期待していたフィールド（誤） | 実際のフィールド（正） |
|---|---|---|
| `CLMZanKaiKanougaku` | `sZanKaiKanougakuGoukei` | `sSummaryGenkabuKaituke` |
| `CLMZanKaiKanougaku` | `sZanKaiKanougakuHusoku`（不足額） | `sHusokukinHasseiFlg`（0/1フラグ） |
| `CLMZanShinkiKanoIjiritu` | `sZanShinkiKanoIjirituGoukei` | `sSummarySinyouSinkidate` |

旧フィールドが存在しないため `dict.get()` が `"0"` にフォールバックし、`int("0") = 0` が返っていた。

## デバッグ手順

```bash
uv run python scripts/diagnose_buying_power.py
```

生 JSON を出力し、以下を確認した：
- `sSummaryGenkabuKaituke = "20000000"` → デモ口座の 2000万円が正しく入っている
- `sZanKaiKanougakuGoukei` キーはレスポンスに存在しない

## 修正箇所

`python/engine/exchanges/tachibana_orders.py`

- `fetch_buying_power`: `sZanKaiKanougakuGoukei` → `sSummaryGenkabuKaituke`
- `fetch_buying_power`: `sZanKaiKanougakuHusoku`（int）→ `sHusokukinHasseiFlg`（"1"=不足ありで1、それ以外0）
- `fetch_credit_buying_power`: `sZanShinkiKanoIjirituGoukei` → `sSummarySinyouSinkidate`

## テスト更新

`python/tests/test_tachibana_buying_power.py` のモックデータを実際のAPIレスポンス形式に修正。
948 passed, 2 skipped。

## 追加ファイル

- `scripts/diagnose_buying_power.py` — 今後の再発防止・実機検証用の診断スクリプト

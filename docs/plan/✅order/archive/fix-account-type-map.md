# B-M4 修正計画: `sZyoutoekiKazeiC` マッピング確定

**作成日**: 2026-04-28  
**完了日**: 2026-04-28  
**ステータス**: ✅ 実装済み・テスト全緑・全受け入れ条件クリア  
**根拠**: マニュアル調査完了（B-M4 pin 要件の解消）  
~~**優先度**: HIGH（現行コードのマッピングに誤りがあり、実発注時に誤った口座区分で送信される）~~

---

## 1. 調査結果

### 1.1 マニュアル定義

| ソース | フィールド | 値 | 意味 |
|--------|-----------|-----|------|
| `mfds_json_api_ref_text.html` `CLMKabuNewOrder` § | `sZyoutoekiKazeiC` | `"1"` | 特定 |
| 〃 | 〃 | `"3"` | 一般 |
| 〃 | 〃 | `"5"` | NISA（一般NISA、2024年以降は売却のみ可） |
| 〃 | 〃 | `"6"` | N成長（NISA成長投資枠、2024年から） |
| `mfds_json_api_ref_text.html` `CLMAuthLoginAck` § | `sZyoutoekiKazeiC` | `"1"/"3"/"5"` | ログイン応答でユーザーの口座属性を返す |
| `samples/e_api_cancel_order_tel.py` ほか全サンプル | コメント | `1：特定 3：一般 5：NISA` | `"6"` はサンプル未収録（2024年以降の追加） |
| `samples/e_api_order_shinyou_buy_shinki_tel.py` | 実装パターン | `sTokuteiKouzaKubunSinyou=="0"` → `"3"`, else `"1"` | 信用注文時の条件分岐（ログイン応答ベース） |

**重要**: `"0"` は `sZyoutoekiKazeiC` の有効値に**存在しない**。

### 1.2 現行 `_ACCOUNT_TYPE_MAP` との差分

※ 以下の差分表は修正前の現行状態を示す。§3 の各ファイル修正（Step 5 を含む）で解消される。

| 現行タグ名 | 現行値 | マニュアルでの意味 | 誤りの種類 |
|-----------|-------|-----------------|-----------|
| `account_type=specific_with_withholding` | `"1"` | 特定 | タグ名過剰（源泉区分はこのフィールドで区別しない） |
| `account_type=specific_without_withholding` | `"3"` | **一般** | **値の意味が完全に誤り（特定ではなく一般）** |
| `account_type=general` | `"0"` | **存在しない値** | **無効な値** |
| `account_type=nisa_growth` | `"5"` | **一般NISA** | タグ名と値の意味が逆（"5"は成長ではなく一般NISA） |
| `account_type=nisa_tsumitate` | `"6"` | **NISA成長投資枠** | タグ名と値の意味が逆（"6"はつみたてではなく成長投資枠） |

### 1.2.1 調査結果（2026-04-28）— 修正は既に適用済み

実際のコード（`python/engine/exchanges/tachibana_orders.py`）を確認した結果、**§1.2 の差分表に示した誤りは既に修正済み**であった。

現行 `_ACCOUNT_TYPE_MAP`（確認済み）:

```python
_ACCOUNT_TYPE_MAP: dict[str, str] = {
    "account_type=specific":    "1",  # 特定口座
    "account_type=general":     "3",  # 一般口座
    "account_type=nisa":        "5",  # 一般NISA（2024年以降売却のみ可）
    "account_type=nisa_growth": "6",  # NISA成長投資枠（N成長）
}
```

- 旧タグ名（`specific_with_withholding` / `specific_without_withholding` / `nisa_tsumitate`）: **コードに存在しない** ✅  
- 無効値 `"0"`: **コードに存在しない** ✅  
- `src/api/order_api.rs`: 旧タグ名は**存在しない** ✅  
- `architecture.md §10.4`: B-M4 を「確定（2026-04-28）」で更新済み ✅  
- `invariant-tests.md`: B-M4 不変条件エントリ追加済み ✅  
- `test_account_type_map_matches_manual`: PASS ✅

### 1.3 源泉徴収区分について

- `sZyoutoekiKazeiC` は「特定 or 一般 or NISA」の大分類のみ
- 源泉徴収あり/なしの区分は **ログイン応答の別フィールド** `sTokuteiKouzaKubunGenbutu` （`0=一般, 1=特定源泉なし, 2=特定源泉あり`）で管理される口座属性であり、`CLMKabuNewOrder` の `sZyoutoekiKazeiC` には反映しない
- つみたて投資枠は株式注文（`CLMKabuNewOrder`）では使用不可（積み立て専用商品のため）

---

## 2. 正しいマッピング

```python
_ACCOUNT_TYPE_MAP: dict[str, str] = {
    "account_type=specific":     "1",  # 特定口座
    "account_type=general":      "3",  # 一般口座
    "account_type=nisa":         "5",  # 一般NISA（2024年以降売却のみ）
    "account_type=nisa_growth":  "6",  # NISA成長投資枠（N成長）
}
```

**省略時の動作**: `session.zyoutoeki_kazei_c`（ログイン応答の口座属性）をパススルー — **変更不要**（現行実装が正しい）

---

## 3. 修正対象ファイル

### 3.1 `python/engine/exchanges/tachibana_orders.py`

| 箇所 | 変更内容 |
|------|---------|
| `TachibanaWireOrderRequest.account_type フィールドのコメント` | 変更前: `"1"=特定源泉, "3"=特定非源泉, "0"=一般 etc.` → 変更後: `"1"=特定, "3"=一般, "5"=一般NISA, "6"=NISA成長投資枠` |
| `_ACCOUNT_TYPE_MAP 定数` | 上記「正しいマッピング」に差し替え |

### 3.2 `docs/plan/✅order/architecture.md` §10.4

`account_type` タグ表を以下に更新:

| tag 値 | 立花値 | 意味 |
|--------|--------|------|
| `account_type=specific` | `"1"` | 特定口座 |
| `account_type=general` | `"3"` | 一般口座 |
| `account_type=nisa` | `"5"` | 一般NISA（2024年以降売却のみ可） |
| `account_type=nisa_growth` | `"6"` | NISA成長投資枠（Phase O4） |

B-M4 注記を「**確定（2026-04-28）**」に更新し「マニュアルで pin 必須」文言を削除。

※ open-questions.md に B-M4 の独立エントリが存在しないが、architecture.md §10.4 の B-M4 注記を「確定（2026-04-28）」に更新することをもって代替とする。

### 3.3 テストファイルの旧タグ名更新

旧タグ名を使用している箇所を検索して修正:

**修正対象ファイル**:
- `python/tests/` 配下の各テストファイル
- `src/api/order_api.rs`（`account_type=specific_with_withholding` が 5 箇所残存）
- `docs/plan/✅order/spec.md`（L169 のサンプル JSON に `account_type=specific_with_withholding` が残存）

```bash
# 検索コマンド
grep -r "account_type=specific_with_withholding\|account_type=specific_without_withholding\|account_type=general.*\"0\"\|account_type=nisa_growth.*\"5\"\|account_type=nisa_tsumitate" python/tests/ src/ docs/plan/✅order/spec.md
```

---

## 4. テスト追加

### 4.1 `_ACCOUNT_TYPE_MAP` 正本化テスト（`test_tachibana_order_mapping.py` に追加）

```python
def test_account_type_map_matches_manual():
    """B-M4: sZyoutoekiKazeiC の値がマニュアル確定値と一致することを assert。"""
    from engine.exchanges.tachibana_orders import _ACCOUNT_TYPE_MAP
    assert _ACCOUNT_TYPE_MAP["account_type=specific"]    == "1"
    assert _ACCOUNT_TYPE_MAP["account_type=general"]     == "3"
    assert _ACCOUNT_TYPE_MAP["account_type=nisa"]        == "5"
    assert _ACCOUNT_TYPE_MAP["account_type=nisa_growth"] == "6"
    # 旧タグ名が存在しないこと
    assert "account_type=specific_with_withholding" not in _ACCOUNT_TYPE_MAP
    assert "account_type=specific_without_withholding" not in _ACCOUNT_TYPE_MAP
    assert "account_type=nisa_tsumitate" not in _ACCOUNT_TYPE_MAP
    # "0" が値として存在しないこと
    assert "0" not in _ACCOUNT_TYPE_MAP.values()
```

また、既存テスト `test_account_type_uses_session_zyoutoeki_when_no_tag`（`session.zyoutoeki_kazei_c` パススルー）が修正後も PASS することを合わせて確認する。

---

## 5. 実装順序

```
1. python/engine/exchanges/tachibana_orders.py を修正（_ACCOUNT_TYPE_MAP + コメント）
2. grep で旧タグ名の使用箇所を確認・修正（python/tests/、src/api/order_api.rs の旧タグ名を新タグ名に置換、docs/plan/✅order/spec.md）
   - Rust 側 src/api/order_api.rs の旧タグ名を新タグ名に置換
3. test_account_type_map_matches_manual を追加
3.5. invariant-tests.md に B-M4 不変条件エントリを追記
      （キー: B-M4 / 条件: _ACCOUNT_TYPE_MAP が sZyoutoekiKazeiC マニュアル定義値と一致 / テスト: test_account_type_map_matches_manual）
4. uv run pytest python/tests/ -q で全緑確認
5. architecture.md §10.4 を更新（B-M4 確定化）
```

---

## 6. 影響範囲

- **IPC スキーマ変更なし**（タグは `tags: Vec<String>` として Rust 側は中身を検証しない）
- **SCHEMA_MAJOR/MINOR 変更不要**
- **既存の `cash_margin` マッピングへの影響なし**
- **Phase O4 のスコープ変更なし**（NISA成長投資枠 `"6"` は Phase O4 着手時に有効化）

今回の修正でマップへ `account_type=nisa_growth: "6"` エントリを追加するが、Rust 側 order_entry の UI 選択肢に `nisa_growth` は含まれていないため、wire に `"6"` が到達することはない（dead code 扱い）。Phase O4 で UI 選択肢追加と合わせて有効化する。

---

## 7. 受け入れ条件

- [x] ✅ `_ACCOUNT_TYPE_MAP` にキー `account_type=specific_with_withholding` が存在しない
- [x] ✅ `_ACCOUNT_TYPE_MAP` に値 `"0"` が存在しない
- [x] ✅ `_ACCOUNT_TYPE_MAP["account_type=general"] == "3"`
- [x] ✅ `_ACCOUNT_TYPE_MAP["account_type=nisa"] == "5"`
- [x] ✅ `_ACCOUNT_TYPE_MAP["account_type=nisa_growth"] == "6"`
- [x] ✅ `test_account_type_map_matches_manual` が PASS
- [x] ✅ `uv run pytest python/tests/ -q` 全緑
- [x] ✅ `architecture.md §10.4` の B-M4 注記が「確定（2026-04-28）」になっている
- [x] ✅ `src/api/order_api.rs` に旧タグ名が存在しない
- [x] ✅ `invariant-tests.md` に B-M4 不変条件エントリが追加されている

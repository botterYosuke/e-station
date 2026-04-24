---
name: marimo
description: marimo のノートブック (.py) 開発における包括的なコーディング規約・ベストプラクティス。リアクティブプログラミング、状態管理、UI連携の基準。
origin: Custom
---

# marimo Coding Standards & Best Practices

AIエージェントが `marimo` アプリケーション（リアクティブな Python ノートブック）を構築・編集する際の、最高品質のガイドラインです。
公式ドキュメント (`https://docs.marimo.io/`) のセオリーおよび、独自性である「リアクティブプログラミングモデル」を正確に反映します。

## When to Activate

- `.py` 形式の marimo スクリプトを新規作成・編集するとき
- データ分析ダッシュボードや双方向な UI ツールを構築するとき
- 既存の Jupyter Notebook から marimo アプリ等への移行処理を行うとき

---

## 1. Core Principles (基本原則)

### 1-1. Reactive Execution (リアクティブな実行)
marimo は **トップダウン（上から下）ではなく、データ依存関係（DAG: 有向非巡回グラフ）** に基づいてセルを実行します。
- **RULE**: セルの配置順序（上下関係）に依存したコードを書かない。
- **RULE**: 変数は「ただ一つのセル」でのみ定義（初期化）可能。同じ変数名を複数のセルで再定義（上書き）してはならない（Multiple Definitions Error になるため）。

### 1-2. No Hidden State（隠し状態の排除）
従来のノートブックのように「手動の実行順序による想定外の変数状態」は発生しません。
- **RULE**: グローバルに共有される変数は明示的に `return` し、他のセルで引数（引数名は変数名と一致）として受け取るか、外部スコープとして参照する。

---

## 2. File Structure (基本構造)

marimo ファイルの構造は厳密に定められています。

```python
import marimo

__generated_with = "0.23.1"
app = marimo.App()

@app.cell
def _():
    import marimo as mo
    # 全てのセルで利用可能な変数は return してエクスポートする
    return mo,

@app.cell
def _(mo):
    # 他のセルで定義・returnされた `mo` に依存して自動的に実行される
    mo.md("# Highest Quality marimo App 🚀")
    return

if __name__ == "__main__":
    app.run()
```

---

## 3. 状態管理（State Management）

UIのボタンクリックなどで、データフローとは別に内部の「状態（State）」を保持・変更する必要がある場合は `mo.state()` を使用します。Pythonのグローバル変数（`global`句）は副作用を伴うため使用してはなりません。

```python
@app.cell
def _(mo):
    # state の初期化。値の取得(getter)と、更新(setter)を明示的にタプルで受け取る
    get_count, set_count = mo.state(0)
    return get_count, set_count

@app.cell
def _(get_count, set_count, mo):
    # getter は呼び出して値を取得し、setter は新しい値を渡す
    increment_btn = mo.ui.button(
        label=f"Count is: {get_count()}",
        on_change=lambda _: set_count(get_count() + 1)
    )
    return increment_btn,
```

### 3-1. self-loop と allow_self_loops

`mo.state` のデフォルト動作：**setter を呼び出したセル自身は再実行されない**（`allow_self_loops=False`）。

- **RULE**: `on_click` / `on_change` コールバック内で `set_xxx()` を呼び出し、かつ同じセルで `get_xxx()` を表示している場合、そのセルは再実行されないため画面が更新されない。
- **解決策**: `mo.state(..., allow_self_loops=True)` を指定する。

```python
# FAIL: デフォルトでは setter を呼んだセルは再実行されない → 状態表示が更新されない
@app.cell
def _(mo):
    get_s, set_s = mo.state("初期値")    # allow_self_loops=False (デフォルト)
    return get_s, set_s

@app.cell
def _(get_s, mo, set_s):
    btn = mo.ui.button(value=0, label="更新", on_click=lambda v: set_s("更新済") or v + 1)
    mo.vstack([btn, mo.md(get_s())])     # ← クリックしても "初期値" のまま変わらない
    return (btn,)

# PASS: allow_self_loops=True で setter 呼び出しセルも再実行される
@app.cell
def _(mo):
    get_s, set_s = mo.state("初期値", allow_self_loops=True)
    return get_s, set_s

@app.cell
def _(get_s, mo, set_s):
    btn = mo.ui.button(value=0, label="更新", on_click=lambda v: set_s("更新済") or v + 1)
    mo.vstack([btn, mo.md(get_s())])     # ← クリックで "更新済" に変わる
    return (btn,)
```

### 3-2. mo.state の初期化セル分離（必須）

> **Note (出典)**: このルールは公式ドキュメント（[State - marimo](https://docs.marimo.io/api/state/) / [Dangerously set state - marimo](https://docs.marimo.io/guides/state/)）に**明示的には記載されていない経験則**である。ただし以下 2 つの公式仕様から必然的に導かれる：
> 1. `mo.state(initial)` はセル評価ごとに呼び出され、新しい state を生成する。
> 2. `allow_self_loops=True` のセルは `set_xxx()` を呼ぶと自身が再実行される。
>
> → 初期化と getter 表示を同居させると、setter 実行 → 自セル再実行 → `mo.state(initial)` 再評価 → state がリセットされる。

- **RULE**: `mo.state()` の初期化は**必ず独立したセルに分離**する。
- **理由**: getter を呼び出すセルと同じセルに `mo.state()` を置くと、状態変化でそのセルが再実行されるたびに初期値にリセットされる。

```python
# FAIL: state 初期化と getter 呼び出しが同じセル → 再実行で初期値にリセット
@app.cell
def _(mo):
    get_s, set_s = mo.state("初期値")
    mo.md(get_s())    # ← セル再実行のたびに mo.state("初期値") が呼ばれリセット
    return get_s, set_s

# PASS: 初期化セルと使用セルを分離
@app.cell
def _(mo):
    get_s, set_s = mo.state("初期値")    # ← 初期化のみ。このセルは再実行されない
    return get_s, set_s

@app.cell
def _(get_s, mo):
    mo.md(get_s())    # ← 状態変化で再実行されるが、初期化セルは再実行されない
    return
```

### 3-3. レシピ：ボタン + 副作用 + 結果表示を 1 セルに収める

「ボタンを押す → API を叩く → 結果をそのボタンの直下に表示する」という頻出パターンは、
3-1（`allow_self_loops=True`）+ 3-2（初期化セル分離）+ 4-3（`on_click` で value 更新）の合わせ技で、
**UI 定義と結果表示を 1 セルに収める**ことができる。

```python
# セル1: 状態の初期化（独立セル、必須 — ルール 3-2）
@app.cell
def _(mo):
    get_status, set_status = mo.state(
        {"ok": False, "label": "未実行"},
        allow_self_loops=True,      # ← setter を呼んだセル自身も再実行させる（ルール 3-1）
    )
    return get_status, set_status

# セル2: ボタン定義 + 副作用 + 結果表示（1 セル）
@app.cell
def _(api_url, get_status, httpx, mo, set_status):
    def _on_click(v):
        try:
            _resp = httpx.get(f"{api_url.value}/api/replay/status", timeout=2.0)
            _ok = _resp.status_code == 200
            set_status({"ok": _ok, "label": f"status={_resp.status_code}"})
        except Exception as _e:
            set_status({"ok": False, "label": f"失敗: {_e}"})
        return v + 1                # ← ボタン value もインクリメント（ルール 4-3）

    check_button = mo.ui.button(value=0, label="✓ 実行", on_click=_on_click)
    mo.vstack([check_button, mo.md(f"_状態: {get_status()['label']}_")])
    return (check_button,)
```

**なぜ成立するか**:
1. ルール 4-1（同一セル内 `.value` アクセス禁止）を回避 — `button.value` には触れず、副作用は `on_click` コールバック内で `set_xxx()` 経由で起こす。
2. ルール 3-1 の `allow_self_loops=True` で自セル再実行を許可 → 同セル内の `get_xxx()` 表示が更新される。
3. ルール 3-2 で初期化セルを分離しているので、再実行しても state がリセットされない。

**ハマりどころ**:
- `on_click` の戻り値でボタン自身の `value` を更新しないと、下流セルが `button.value` を監視していても再評価されない。`return v + 1` を必ず書く（4-3 参照）。
- `mo.state` の初期化を同居させると state がリセットされる（3-2 参照）。
- **逆に言うと**：`mo.state` を使わない版（クロージャや可変 dict での状態保持）はこのパターンに組み込めない — セル再実行で初期化されるため。

**merge 可否の判定表**（「できる限りセルを統合したい」要望への指針）:

| 対象 | 同一セル化 | 理由 |
|---|---|---|
| `mo.ui.button` 定義 + `on_click` 内の副作用 + `get_state()` 表示 | ✅ OK | 本レシピ。`.value` に触れないことが条件 |
| `mo.ui.X` 定義 + `X.value` を使う計算 | ❌ NG | ルール 4-1（RuntimeError） |
| `mo.state()` 初期化 + `get_state()` 表示 | ❌ NG | ルール 3-2（リセット） |
| 同じ UI 要素の `.value` に依存する下流セル同士（例: dropdown → method 計算 + dropdown → form 生成） | ✅ OK | `.value` アクセスは定義セルの外なら同居可 |

---

## 4. ユーザーインターフェース (UI Elements)

marimo の `mo.ui.*` はリアクティブなコンポーネントです。ユーザーがUIを操作すると、そのコンポーネントの `value` (またはコンポーネント自体) に依存しているセルが自動的に再実行されます。

### 4-1. UIコンポーネントのセル分離（必須）

- **RULE**: 「UIコンポーネントを定義するセル」と、「その値を使って計算を行うセル」を**必ず**別のセルに分離する。
- **理由**: 同一セル内で `widget.value` にアクセスすると `RuntimeError` が発生する（marimo の制約）。
  分離することで、重い計算を伴う再レンダリングループや、ダッシュボードのフリーズも防ぎます。

```python
# FAIL: 定義と値アクセスが同一セル → RuntimeError
@app.cell
def _(mo):
    btn = mo.ui.button(label="送信")
    if btn.value:          # ← RuntimeError: Accessing the value of a UIElement
        do_something()     #   in the cell that created it is not allowed.
    return btn,

# PASS: セルを分離（定義セル）
@app.cell
def _(mo):
    btn = mo.ui.button(label="送信")
    btn                    # ← スタンドアロン式で表示（必須。return だけでは表示されない）
    return btn,

# PASS: セルを分離（値アクセスセル）
@app.cell
def _(btn):
    if btn.value:
        do_something()
    return
```

### 4-2. UIコンポーネントの表示（スタンドアロン式）

- **RULE**: UI要素を画面に表示するには、セル内で**スタンドアロン式**（変数名だけの行）として書く。`return (widget,)` だけでは表示されない。
- **RULE**: UI要素は**定義したセルでのみ表示する**。別のセルの `mo.vstack` 等に含めると非インタラクティブなコピーになり、クリックしても `value` が更新されない。

```python
# FAIL: return だけでは表示されない
@app.cell
def _(mo):
    btn = mo.ui.button(label="送信")
    return btn,             # ← 他セルには export されるが画面には表示されない

# FAIL: 別セルで vstack に含める → 非インタラクティブ（クリック無効）
@app.cell
def _(mo, btn):
    mo.vstack([btn, mo.md("説明")])   # ← btn は定義セルと別セルなので dead copy

# PASS: 定義セルでスタンドアロン式として表示
@app.cell
def _(mo):
    btn = mo.ui.button(label="送信")
    btn                     # ← これで画面に表示される（インタラクティブ）
    return btn,

# PASS: 別セルには別の要素だけ表示
@app.cell
def _(mo, btn):
    _result = mo.md("クリックされました！") if btn.value else mo.md("")
    _result                 # ← btn は表示しない、結果だけ表示
```

### 4-3. ボタンの正しい初期化パターン

`mo.ui.button` は **`on_click` を指定しないと `value` が更新されない**。クリックカウンターとして使うには `value=0` と `on_click=lambda v: v + 1` が必須。

| 用途 | 正しい書き方 | 誤った書き方 |
|------|------------|------------|
| クリックカウンター | `mo.ui.button(value=0, on_click=lambda v: v + 1)` | `mo.ui.button(label="送信")` のみ ← value が更新されない |
| クリック判定 | `if btn.value:` | `if btn.clicked:` ← AttributeError |

```python
# FAIL: on_click なし → クリックしても value が None のまま
@app.cell
def _(mo):
    btn = mo.ui.button(label="送信")
    btn
    return btn,

# PASS: on_click でクリックごとに value をインクリメント
@app.cell
def _(mo):
    btn = mo.ui.button(value=0, label="送信", on_click=lambda v: v + 1)
    btn
    return btn,

@app.cell
def _(btn):
    if btn.value:    # 1回以上クリックされたら truthy
        do_something()
    return
```

### 4-4. 条件付きUI表示パターン

接続状態などの条件でUI要素の表示・非表示を切り替える場合、UI要素は**常に定義**して表示だけを条件分岐する。

```python
# FAIL: 条件ブランチ内で定義すると early return で NameError が起きる
@app.cell
def _(mo, is_connected):
    if not is_connected:
        return (None,)          # ← downstream が dropdown を期待してエラー
    dropdown = mo.ui.dropdown(options=[...])
    dropdown
    return (dropdown,)

# PASS: 常に定義し、表示だけ切り替える
@app.cell
def _(mo, is_connected):
    dropdown = mo.ui.dropdown(options=[...])
    _content = dropdown if is_connected else mo.md("")
    _content
    return (dropdown,)
```

### 4-5. 複数フィールドのフォームパターン

複数の入力フィールドをまとめるには `mo.ui.dictionary` を使う。`mo.ui.form(mo.vstack([...]))` は `.value` が機能しないため使用しない。

```python
# FAIL: mo.vstack は UIElement ではないため form.value が取得できない
params_form = mo.ui.form(mo.vstack([
    mo.ui.text(label="ticker"),
    mo.ui.number(label="qty"),
]))

# PASS: mo.ui.dictionary でまとめる → .value が dict で返る
params_form = mo.ui.dictionary({
    "ticker": mo.ui.text(label="ticker", value="BTC"),
    "qty": mo.ui.number(label="qty", start=0.001, stop=1000.0, step=0.001, value=0.01),
})

# 使用側: params_form.value は {"ticker": "BTC", "qty": 0.01} の dict
body = dict(params_form.value) if params_form else {}
```

### 4-6. セルローカル変数（`_` プレフィックス）

変数名を `_` で始めると、そのセルのローカル変数になり、他セルには export されない。

```python
@app.cell
def _(mo):
    _tmp = "このセルだけで使う一時変数"   # ← export されない
    result = _tmp.upper()               # ← export される
    result
    return (result,)
```

---

## 5. セルの return 一貫性

- **RULE**: セル内の全ブランチで `return` するタプルの変数名セットを統一する。Early return でシグネチャが変わると、下流セルで `NameError` が発生する。

```python
# FAIL: ブランチによって return シグネチャが異なる
@app.cell
def _(mo, condition):
    if not condition:
        return (None,)          # ← path_only だけ。method は未定義
    path_only = "/api/foo"
    method = "GET"
    return (path_only, method)  # ← 2変数

# PASS: デフォルト値で統一する
@app.cell
def _(mo, condition):
    path_only = None
    method = None
    if condition:
        path_only = "/api/foo"
        method = "GET"
    return (path_only, method)  # ← 常に同じシグネチャ
```

---

## 6. Anti-Patterns (避けるべきコードの臭い)

1. **セルの肥大化（God Cells）**:
   1つの `@app.cell` 内に、データの読み込み、UI定義、前処理、描画などをすべて同居させることは避けてください。役割（設定、データ抽出、UI定義、ビジネスロジック）ごとに細かくセルを分割します。

2. **同一セル内での UIElement.value アクセス**:
   `mo.ui.*` を定義したセルの中でその `.value` を読み取ると `RuntimeError` になります。
   必ず別セルに分離してください（→ 4-1 参照）。

3. **定義セル以外での UI 要素の表示**:
   別セルの `mo.vstack` 等に UI 要素を含めると非インタラクティブなコピーになります（→ 4-2 参照）。

4. **`on_click` なしのボタン**:
   `mo.ui.button` は `on_click` がないと `value` が更新されません（→ 4-3 参照）。

5. **存在しない属性の使用**:
   - `button.clicked` → 存在しない。`.value` を使う。
   - `button.is_pressed` → 存在しない。`.value` を使う。

6. **暗黙のループや副作用 (Side Effects)**:
   `while True` や過度な `sleep` によるポーリングは、データフローの評価をブロックします。
   定期的な更新が必要な処理などは、UIイベント等に紐づけて駆動させてください。

7. **import の乱用とスコープ漏れ**:
   特定のセルでのみ使用し、他のセルで再利用しないモジュールは、そのセル内で閉じる（`return` しない）ようにします。

---

**Remember**: marimo は「Python の書きやすさ」と「React のようなリアクティブな UI 構築」を兼ね備えています。常に**データフロー（DAG: 依存関係グラフ）**を意識し、役割が明確でステートレスなセル設計を心がけてください。

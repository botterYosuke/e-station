---
name: jupyter
description: VS Code Jupyter + ipywidgets ノートブック開発の落とし穴と回避策。特に Output widget の重複描画・ハンドラ重複登録・セル再実行時の状態汚染への対処。
origin: Custom (flowsurface tests/python/api_jupyter.ipynb から抽出)
---

# Jupyter + ipywidgets Skill

flowsurface の E2E テスト補助ノートブック（`tests/python/api_jupyter.ipynb`）開発で得た、
VS Code Jupyter 拡張 + ipywidgets 特有の罠と確立した回避策をまとめる。

## When to Activate

- `tests/python/*.ipynb` 等の Jupyter ノートブックを新規作成・編集するとき
- `ipywidgets` の `Button` / `Output` / `VBox` などを使った対話 UI を組むとき
- 「セル再実行ごとに出力が N 個重複する」「ボタン 1 クリックで複数回ハンドラが走る」系のバグ調査

---

## 1. 最重要: Output widget が「N 個重複描画」される問題

### 症状

- セル（または「すべてのセルを実行」）を N 回実行してから、`widgets.Button` を **1 回だけ** クリック
- `widgets.Output` に同一のレスポンスが **N 個** 並ぶ
- N はセル実行回数に一致する

### 間違った診断

「ハンドラが N 重登録されている」と考えがちだが、`_click_handlers.callbacks` を覗くと **1 個しかない**。
`on_send` は 1 回しか呼ばれていない。`resp_out.outputs` の長さも 1 のまま。
それでも画面には N 個見える。

### 真の原因

**`resp_out` の widget モデルは 1 個だが、`display()` するたびに新しい「ビュー」が DOM に追加される**。
VS Code Jupyter では `clear_output(wait=False)` を呼んでも widget ビューの DOM 要素が
残留することがある。結果：

```
resp_out (model 1 個)
  ├─ View 1 (1 回目の display で作成) → outputs を描画
  ├─ View 2 (2 回目の display で作成) → 同じ outputs を描画
  └─ View N (N 回目の display で作成) → 同じ outputs を描画
```

widget state は正しく 1 件なのに、N 個のビューが同じ state を同時描画して「N 個重複」に見える。

### 効かない修正（やっても無駄）

以下はすべて試されたが **解決しない**：

1. `resp_out.clear_output(wait=True)` を `with resp_out:` の外で呼ぶ
2. `resp_out.outputs = ()` を `with resp_out:` の外で呼ぶ
3. `with resp_out:` の内側で `clear_output(wait=False)` を呼ぶ（← クリック累積は止まるが初期 N 重複は残る）
4. `send_btn._click_handlers.callbacks.clear()` でハンドラクリア後に再登録
5. `_ui_created` シングルトンガードで widget 再生成を防ぐ

どれも state 側（`outputs` タプル）の話で、**ビューの数**を減らす修正ではない。

### 正しい修正（確立されたパターン）

次の 3 点を組み合わせる：

```python
from IPython.display import clear_output, display, update_display
import ipywidgets as widgets

if "_ui_created" not in globals():
    # ① widget とメイン VBox は 1 度だけ生成
    resp_out = widgets.Output()
    send_btn = widgets.Button(description="送信")

    def _set_resp_markdown(md_text):
        # ③ `with resp_out:` を使わず、outputs を直接代入する
        resp_out.outputs = (
            {
                "output_type": "display_data",
                "data": {"text/markdown": md_text, "text/plain": md_text},
                "metadata": {},
            },
        )

    def on_send(b):
        _set_resp_markdown("### レスポンス ✅ ...")

    send_btn.on_click(on_send)

    _ui_main       = widgets.VBox([send_btn, resp_out])
    _ui_display_id = "my_ui_main"  # 固定文字列の display_id
    _ui_displayed  = False
    _ui_created    = True

# ② セル末尾は display_id で「インプレース更新」
clear_output(wait=True)  # wait=True 推奨（クリアと新描画のレース回避）
if not _ui_displayed:
    display(_ui_main, display_id=_ui_display_id)
    _ui_displayed = True
else:
    update_display(_ui_main, display_id=_ui_display_id)
```

### なぜこれで直るか

- **① シングルトン**: widget モデルは 1 個に固定
- **② `display_id` + `update_display`**: `display()` を複数回呼んでも新しいビューを DOM に追加せず、既存の表示ハンドルをインプレース更新する → ビュー数が 1 に収束
- **③ `outputs` 直接代入**: `with output_widget:` コンテキストマネージャ経由の capture を避け、state 更新経路を一本化

---

## 2. `with output_widget:` は避ける

ipywidgets の `Output` に書き込む慣用句：

```python
with resp_out:
    clear_output(wait=False)
    display(Markdown("..."))
```

一見クリーンだが、VS Code Jupyter では以下の不具合源になる：

- stdout/stderr/display のキャプチャがネストして予期せぬ挙動をする
- セル実行中の capture とレース
- 例外時に clear と display が中途半端な状態で残る

**推奨**: `output_widget.outputs = (display_data_dict,)` で直接代入する。
または `output_widget.append_display_data(obj)` / `output_widget.clear_output()` メソッドを使う。

---

## 3. セル再実行時のハンドラ重複問題

`on_click` や `.observe()` をセル本文に裸で書くと、セル再実行のたびにハンドラが追加登録されて
1 クリックで複数回発火する。

### 解決パターン（シングルトンガード）

```python
if "_ui_created" not in globals():
    # ここで 1 度だけウィジェット生成 + ハンドラ登録
    send_btn.on_click(on_send)
    endpoint_dd.observe(on_change, names="value")
    _ui_created = True
# セル再実行時はここはスキップされる
```

`_ui_created` はグローバル変数として残るので、カーネル再起動するまで再実行されない。

### カーネル再起動が必要な場面

- ハンドラや widget 構造を書き換えた後は **必ずカーネル再起動**
- `_ui_created` が残っていると古いハンドラ定義のままになる
- 書き換え → 再起動 → 「すべてのセルを実行」 が安全ルーチン

---

## 4. 診断テンプレート（重複バグ調査用）

上記パターンで直らない場合に使う診断セル：

```python
# 診断 1: on_send 呼び出し回数
_call_count = 0
_orig = on_send
def _debug(b):
    global _call_count
    _call_count += 1
    print(f"[DEBUG] on_send #{_call_count}")
    _orig(b)
send_btn._click_handlers.callbacks.clear()
send_btn.on_click(_debug)

# 診断 2: Output widget の state
print(f"resp_out.model_id: {resp_out.model_id}")
print(f"len(resp_out.outputs): {len(resp_out.outputs)}")

# 診断 3: ハンドラ数
print(f"handlers: {send_btn._click_handlers.callbacks}")
print(f"count: {len(send_btn._click_handlers.callbacks)}")
```

これで判別：

| `on_send` 呼び出し | `outputs` 長 | 画面重複 | 原因 |
|:---|:---|:---|:---|
| 1 回 | 1 | N 個 | **ビュー重複**（本 SKILL の ① ② ③ で対処） |
| N 回 | N | N 個 | ハンドラ N 重登録（シングルトンガードで対処） |
| 1 回 | N | N 個 | `clear_output` 経路不良（`outputs` 直接代入で対処） |

---

## 5. `clear_output(wait=True)` vs `wait=False`

- `wait=False`: 即座にクリア → 次の `display()` まで空白が見える、描画レースの可能性
- `wait=True`: 次の新しい出力が来るまでクリアを遅延 → ちらつきなし、レース回避

**推奨**: セル末尾で UI を描画し直す系は `wait=True`。

---

## 6. ノートブック編集の実務 Tips

- 大きなセル編集は `NotebookEdit` ツール（cell_id 指定）で行う
- 編集後は `python -c "import json; json.load(open(...))"` で JSON 妥当性を即確認
- `.ipynb` の `outputs` はコミット前にクリアする（diff 肥大化 & widget state 汚染の防止）

---

## 7. 参考: 実例

- 修正実例: [tests/python/api_jupyter.ipynb](../../../tests/python/api_jupyter.ipynb) の `ui` セル
- 調査記録: [docs/plan/jupyter-resp-out-duplicate-fix.md](../../../docs/plan/jupyter-resp-out-duplicate-fix.md)
- 類似トピック（リアクティブノートブック側）: [.claude/skills/marimo/SKILL.md](../marimo/SKILL.md)

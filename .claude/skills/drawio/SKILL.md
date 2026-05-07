---
name: drawio
description: draw.io 図（.drawio / .drawio.svg / .drawio.png）をコードから生成する。「drawio を作って」「図を drawio で」「layout を svg に」「フローチャートを書いて」「アーキテクチャ図」「ER図」「シーケンス図」「ワイヤーフレーム」と言われたら起動する。
---

# Draw.io Diagram Skill

draw.io ネイティブ形式（`.drawio`）で図を生成し、必要に応じて PNG / SVG / PDF にエクスポートする。

## 手順

1. **XML 生成** — mxGraphModel 形式の draw.io XML を生成する
2. **ファイル書き出し** — Write ツールで `.drawio` ファイルに書き出す
3. **後処理（任意）** — `npx @drawio/postprocess` が使える場合は実行してエッジルーティングを最適化する。使えなければスキップ（インストールの案内不要）
4. **エクスポート（ユーザー指定時のみ）** — draw.io CLI で `--embed-diagram` 付きエクスポート後、元の `.drawio` ファイルを削除する。CLI が見つからなければ `.drawio` のまま残し、ユーザーに手動でエクスポートするよう案内する
5. **ファイルを開く** — エクスポートしたファイル（または `.drawio` ファイル）をOSに合ったコマンドで開く。失敗したらパスを表示する

## 出力形式の選択

| ユーザー指定 | 出力 |
|------------|------|
| 形式指定なし | `name.drawio` を作ってそのまま開く |
| `png` / `svg` / `pdf` | `name.drawio.png` 等にエクスポート後 `.drawio` を削除 |

PNG / SVG / PDF は `--embed-diagram` で XML が埋め込まれ、draw.io で再編集できる。

## draw.io CLI

### CLI の場所

| 環境 | パス |
|------|------|
| Windows (native) | `"C:\Program Files\draw.io\draw.io.exe"` |
| WSL2 | `` `/mnt/c/Program Files/draw.io/draw.io.exe` `` |
| macOS | `/Applications/draw.io.app/Contents/MacOS/draw.io` |
| Linux | `drawio`（PATH 経由） |

WSL2 検出: `grep -qi microsoft /proc/version 2>/dev/null`

Per-user インストールの場合は `/mnt/c/Users/$WIN_USER/AppData/Local/Programs/draw.io/draw.io.exe`

### エクスポートコマンド

```bash
drawio -x -f <format> -e -b 10 -o <output> <input.drawio>
```

主なフラグ:

| フラグ | 意味 |
|--------|------|
| `-x` | エクスポートモード |
| `-f` | フォーマット（png / svg / pdf / jpg） |
| `-e` | 図の XML を埋め込む（PNG・SVG・PDF のみ） |
| `-b 10` | 余白 10px |
| `-o` | 出力ファイルパス |
| `-t` | 透過背景（PNG のみ） |
| `-s` | スケール |
| `--width` / `--height` | アスペクト比を保ってリサイズ |
| `-a` | 全ページ（PDF のみ） |

### ファイルを開くコマンド

| 環境 | コマンド |
|------|---------|
| macOS | `open <file>` |
| Linux | `xdg-open <file>` |
| WSL2 | `cmd.exe /c start "" "$(wslpath -w <file>)"` |
| Windows | `start <file>` |

## XML 形式

`.drawio` はネイティブ mxGraphModel XML。Mermaid や CSV はサーバー変換が必要なので使わない。

### 基本構造

```xml
<mxGraphModel adaptiveColors="auto">
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <!-- 図形セル（id="2" 以降、parent="1"） -->
  </root>
</mxGraphModel>
```

### グリッド（座標計算はこれだけ）

- 列 x = `col × 180 + 40`（col 0 → 40, col 1 → 220, …）
- 行 y = `row × 120 + 40`（row 0 → 40, row 1 → 160, …）
- 標準サイズ: 矩形 `140×60`、菱形 `140×80`、円 `60×60`

### XML を書くときの禁止事項

- **XML コメント（`<!-- -->`）を絶対に書かない** — パースエラーの原因になる
- `<Array as="points">` でウェイポイントを追加しない — ELK が自動ルーティングする
- `exitX` / `exitY` / `entryX` / `entryY` は明確な幾何的意図がない限り設定しない
- XML を出力した後に座標の再確認・調整をしない

### 図形スタイル早見表

```xml
<!-- 角丸矩形 -->
<mxCell id="2" value="ラベル"
        style="rounded=1;whiteSpace=wrap;html=1;fillColor=#dae8fc;strokeColor=#6c8ebf;"
        vertex="1" parent="1">
  <mxGeometry x="40" y="40" width="140" height="60" as="geometry"/>
</mxCell>

<!-- 菱形（分岐） -->
<mxCell id="3" value="条件?"
        style="rhombus;whiteSpace=wrap;html=1;"
        vertex="1" parent="1">
  <mxGeometry x="40" y="160" width="140" height="80" as="geometry"/>
</mxCell>

<!-- エッジ（必ず子要素に mxGeometry を持つ） -->
<mxCell id="4" value="" style="edgeStyle=orthogonalEdgeStyle;html=1;"
        edge="1" source="2" target="3" parent="1">
  <mxGeometry relative="1" as="geometry"/>
</mxCell>
```

### HTML ラベル

`html=1;` が必要。値の中の HTML は XML エスケープする:

```xml
value="&lt;b&gt;タイトル&lt;/b&gt;&lt;br&gt;説明文"
```

改行は `&#xa;`（html=0 でも動く）または `&lt;br&gt;`（html=1 必須）。`\n` は使わない。

### よく使う色

| 用途 | fillColor | strokeColor |
|------|-----------|-------------|
| 青系（メニュー・プロセス） | `#dae8fc` | `#6c8ebf` |
| 黄系（フォーム・注意） | `#fff2cc` | `#d6b656` |
| 緑系（完了・成功） | `#d5e8d4` | `#82b366` |
| グレー（補助・無効） | `#f5f5f5` | `#666666` |
| 濃色（ステータスバー） | `#343a40` | `#343a40` |

### エッジスタイル選択

| ダイアグラム種 | スタイル |
|------------|---------|
| フローチャート・アーキテクチャ | `edgeStyle=orthogonalEdgeStyle;rounded=1;` |
| UML クラス・シーケンス | スタイル指定なし（直線） |
| ER 図 | `edgeStyle=entityRelationEdgeStyle;` |
| マインドマップ | `curved=1;` |

---

## `.drawio.svg`（CLI なし・ドキュメント埋め込み用）

draw.io デスクトップアプリが使えない環境でも draw.io で開ける SVG を生成したい場合に使う。  
**素の SVG を `.drawio.svg` と命名してはいけない** — `content` 属性がないと draw.io が "図面ファイルではありません" エラーを出す。

### 必須構造

```xml
<svg host="app.diagrams.net"
     xmlns="http://www.w3.org/2000/svg"
     version="1.1"
     width="W" height="H" viewBox="0 0 W H"
     content="&lt;mxGraphModel ...&gt;...&lt;/mxGraphModel&gt;">
  <!-- 静的 SVG レンダリング（ブラウザ・VS Code プレビュー用） -->
</svg>
```

必須属性:
- `host="app.diagrams.net"` — draw.io がこれでファイルを認識する
- `content="..."` — HTML エンコードした mxGraphModel XML

### PowerShell 生成スクリプト

```powershell
# 1. シングルクォートヒアストリングで mxGraphModel を定義（特殊文字が安全）
$mx = @'
<mxGraphModel adaptiveColors="auto">
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="ラベル"
            style="rounded=1;whiteSpace=wrap;html=1;fillColor=#dae8fc;strokeColor=#6c8ebf;"
            vertex="1" parent="1">
      <mxGeometry x="40" y="40" width="140" height="60" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>
'@

# 2. HTML エンコード（& を必ず最初に変換する）
$encoded = $mx.Trim()
$encoded = $encoded -replace '&', '&amp;'
$encoded = $encoded -replace '<', '&lt;'
$encoded = $encoded -replace '>', '&gt;'
$encoded = $encoded -replace '"', '&quot;'

# 3. ダブルクォートヒアストリングで SVG を組み立て（$encoded が展開される）
$svg = @"
<svg host="app.diagrams.net" xmlns="http://www.w3.org/2000/svg"
     xmlns:xlink="http://www.w3.org/1999/xlink" version="1.1"
     width="800" height="400" viewBox="0 0 800 400"
     content="$encoded">
  <defs/>
  <rect x="40" y="40" width="140" height="60" fill="#dae8fc" stroke="#6c8ebf"/>
  <text x="110" y="70" text-anchor="middle" dominant-baseline="middle"
        font-size="12" font-family="sans-serif" fill="#333">ラベル</text>
</svg>
"@

# 4. UTF-8（BOM なし）で書き出し
[System.IO.File]::WriteAllText("output.drawio.svg", $svg,
    [System.Text.Encoding]::UTF8)
```

**エンコードの罠**: `&` の変換を最後にすると `&lt;` の `&` が `&amp;lt;` に二重エンコードされる。必ず `&` を最初に変換すること。

---

## ファイル命名規則

- 小文字ハイフン区切り: `login-flow.drawio`、`database-schema.drawio`
- エクスポート時はダブル拡張子: `name.drawio.png`、`name.drawio.svg`
- エクスポート成功後は元の `.drawio` ファイルを削除する

---

## XML 完全リファレンス

コンテナ・スイムレーン・レイヤー・メタデータ・ダークモード・UML シーケンス図など詳細スタイルは:

```
https://raw.githubusercontent.com/jgraph/drawio-mcp/main/shared/xml-reference.md
```

---

## トラブルシューティング

| エラー | 原因 | 対処 |
|--------|------|------|
| "図面ファイルではありません" | `content` 属性なし または `host` 属性なし | SVG に両属性を追加する |
| "Cannot read properties of null (reading 'getAttribute')" | `content` の XML が壊れている | エンコード順序（`&` が最初か）を確認 |
| 図形が表示されない | `vertex="1"` が抜けている | `vertex="1" parent="1"` を追加 |
| エッジが描画されない | エッジセルが自己閉じタグ | `<mxGeometry relative="1" as="geometry"/>` 子要素を追加 |
| テキストが文字化け | UTF-16 で書き出した | `[System.Text.Encoding]::UTF8` を明示 |
| CLI が見つからない | draw.io デスクトップ未インストール | `.drawio` のまま保存してユーザーに手動エクスポートを案内 |

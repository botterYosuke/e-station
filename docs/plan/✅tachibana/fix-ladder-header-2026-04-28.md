# Ladder ペイン カラムヘッダ追加

## 目的

[src/screen/dashboard/panel/ladder.rs](../../../src/screen/dashboard/panel/ladder.rs) の Ladder（DOM）ペインに固定カラムヘッダ行を追加する。  
現状はヘッダなしで各列の意味がわからないため、スクロールに追従しない固定ヘッダをキャンバス最上部に描画する。

## 現状分析

### 列構成（5列）

```
[BidOrderQty] [SellTradeQty] [  Price  ] [BuyTradeQty] [AskOrderQty]
   買い板残      売り約定量       価格        買い約定量       売り板残
```

- `column_ranges()` が幅を計算し `ColumnRanges` 構造体で各列の `(x_start, x_end)` を返す
- `visible_rows()` が `bounds.height * 0.5` を中心 Y とし、`scroll_px` を引いて各行の Y 座標を計算する
- 全描画は `self.cache: canvas::Cache` の 1 ジオメトリに収まる

### 問題

- ヘッダがないためユーザーが列の意味を判断できない
- ヘッダをスクロール対象の同一キャッシュに描いても、中心 Y が固定のため行がヘッダに潜り込む

## 実装方針

**2 キャッシュ方式**でヘッダをコンテンツの上にオーバーレイする。

```
draw() → vec![content_geo, header_geo]
                ↑ 下層            ↑ 上層（上書き）
```

- `content_geo`：既存 `self.cache`。中心 Y を `HEADER_HEIGHT` 分だけ下へずらす
- `header_geo`：新規 `self.header_cache`。最上部に不透明背景＋ラベルを描く

スクロール時にコンテンツ行が上部に潜り込んでも `header_geo` に隠れるため、クリッピング不要。

## 変更ファイル

| ファイル | 変更内容 |
|---------|---------|
| `src/screen/dashboard/panel/ladder.rs` | 定数追加・`header_cache` フィールド追加・`draw()` 修正・`invalidate()` 修正・中心 Y 調整 |

Python 側・data クレート・`engine-client` は無変更。

## 詳細実装手順

### Step 1：定数・フィールド追加

```rust
// 追加する定数
const HEADER_HEIGHT: f32 = ROW_HEIGHT; // 16.0

// Ladder 構造体に追加するフィールド
header_cache: canvas::Cache,
```

`Ladder::new()` で `header_cache: canvas::Cache::default()` を初期化する。

### Step 2：`invalidate()` で両キャッシュをクリア

> **注記**: 修正対象は `Panel::invalidate` ラッパー（`impl super::Panel for Ladder` ブロック内）ではなく、
> `Ladder::invalidate`（`impl Ladder` ブロック内のメソッド、現在行 183 付近）を修正する。
> `Panel::invalidate` は `Ladder::invalidate` に委譲しているため、両者を同時に変更すると
> `header_cache.clear()` が二重呼び出しになる点に注意する。
>
> **テーマ変更時の経路**: `Message::ThemeChanged` → `Panel::invalidate(None)` →
> `Ladder::invalidate(self, None)` → `header_cache.clear()` という経由で
> `header_cache` が自動的にクリアされる。追加実装は不要。

```rust
pub fn invalidate(&mut self, now: Option<Instant>) -> Option<super::Action> {
    self.cache.clear();
    self.header_cache.clear();
    if let Some(now) = now {
        self.last_tick = now;
    }
    None
}
```

### Step 3：`visible_rows()` の中心 Y を調整

```rust
// 変更前
let mid_screen_y = bounds.height * 0.5;

// 変更後
let mid_screen_y = HEADER_HEIGHT + (bounds.height - HEADER_HEIGHT) * 0.5;
```

> **ガード**: `bounds.height < HEADER_HEIGHT` の場合は `mid_screen_y` が負になる可能性があるため、
> `(bounds.height - HEADER_HEIGHT).max(0.0) * 0.5 + HEADER_HEIGHT` と書くか、
> あるいは計算後に `.max(HEADER_HEIGHT)` でガードする。

可視判定の上端カットオフ（`0.0`）は変更不要 — `header_geo` がオーバーレイされるため
ヘッダ領域に潜り込んだ行は視覚上隠れる。

### Step 4：`price_to_screen_y()` も同様に調整

```rust
// 変更前
let mid_screen_y = bounds_height * 0.5;

// 変更後
let mid_screen_y = HEADER_HEIGHT + (bounds_height - HEADER_HEIGHT) * 0.5;
```

チェーストレイル（Chase Trail）の Y 座標計算に使われるため同期が必要。

### Step 5：`draw()` でヘッダジオメトリを生成

`draw()` の戻り値を `vec![orderbook_visual]` から `vec![orderbook_visual, header_geo]` に変更。

ヘッダ描画ロジック（クロージャ外で事前計算してからキャプチャする）：

```rust
// クロージャ外で事前計算する
let grid = self.build_price_grid();
let cols_opt = if let Some(ref g) = grid {
    let layout = self.price_layout_for(bounds.width, g);
    let cols = self.column_ranges(bounds.width, layout.price_px);
    Some(cols)
} else {
    None
};
// layout_opt は不要（price_px は cols 計算時にのみ使用し、クロージャはキャプチャしない）

// クロージャはコピー済みの値をキャプチャする（self.header_cache の &mut 借用と競合しない）
let header_geo = self.header_cache.draw(renderer, bounds.size(), |frame| {
    // 不透明背景（コンテンツ行を隠す）
    let bg = palette.background.base.color;
    frame.fill_rectangle(
        Point::new(0.0, 0.0),
        Size::new(bounds.width, HEADER_HEIGHT),
        bg,
    );

    // 下境界線
    frame.fill_rectangle(
        Point::new(0.0, HEADER_HEIGHT - 1.0),
        Size::new(bounds.width, 1.0),
        divider_color,
    );

    // 列ラベル（price_grid が取れない場合はヘッダのみ描く）
    if let Some(cols) = cols_opt {
        let label_color = palette.background.base.text.scale_alpha(0.55);

        let labels: &[(&str, f32, Alignment)] = &[
            ("買板",  cols.bid_order.0 + 6.0,                Alignment::Start),
            ("売T",   cols.sell.1 - 6.0,                     Alignment::End),
            ("価格",  (cols.price.0 + cols.price.1) * 0.5,   Alignment::Center),
            ("買T",   cols.buy.0 + 6.0,                      Alignment::Start),
            ("売板",  cols.ask_order.1 - 6.0,                Alignment::End),
        ];

        for &(label, x, align) in labels {
            // draw_cell_text 第4引数（y）は行上端 Y。内部で +ROW_HEIGHT/2.0 を加算するため
            // y=0.0 を渡すとヘッダ行（0..HEADER_HEIGHT）の縦中央に描画される。
            Self::draw_cell_text(frame, label, x, 0.0, label_color, align);
        }
    }
});
```

> **`draw_cell_text` y 引数の規約**: 第4引数は行上端 Y である。
> 関数内部で `y + ROW_HEIGHT / 2.0` を加算して縦中央に配置するため、
> `y=0.0` を渡すとヘッダ行（Y範囲: `0.0..HEADER_HEIGHT`）の縦中央に描画される。

`price_grid` が `None`（データ未到着）のときは背景と境界線だけ描き、ラベルは省略する。  
列位置は `build_price_grid()` → `column_ranges()` を通して確定するため、幅変化やティックサイズ変更に自動追従する。

### Step 6：縦仕切り線の描画範囲を `HEADER_HEIGHT` 以降に限定

既存の `draw_vsplit` クロージャは `y=0` から描いているため、ヘッダ領域まで伸びる。  
`HEADER_HEIGHT` を起点に変更する。

**gap あり版（`Some((top, bottom))` アーム）の変更前・変更後:**

実コード（変更前）の gap あり分岐:
```rust
// 変更前（gap あり版）— 実コードと一致
Some((top, bottom)) => {
    if top > 0.0 {
        frame.fill_rectangle(
            Point::new(x, 0.0),
            Size::new(1.0, top.max(0.0)),
            divider_color,
        );
    }
    if bottom < bounds.height {
        frame.fill_rectangle(
            Point::new(x, bottom),
            Size::new(1.0, (bounds.height - bottom).max(0.0)),
            divider_color,
        );
    }
}
```

変更後:
```rust
// 変更後（gap あり版）— HEADER_HEIGHT を起点に制限
Some((top, bottom)) => {
    if top > HEADER_HEIGHT {
        // gap の上端がヘッダより下にある場合のみ、ヘッダ下端〜gap 上端を描く
        let seg_height = top - HEADER_HEIGHT;
        frame.fill_rectangle(
            Point::new(x, HEADER_HEIGHT),
            Size::new(1.0, seg_height),
            divider_color,
        );
    }
    if bottom < bounds.height {
        frame.fill_rectangle(
            Point::new(x, bottom),
            Size::new(1.0, (bounds.height - bottom).max(0.0)),
            divider_color,
        );
    }
}
```

**gap なし版（`None` アーム）の変更前・変更後:**

```rust
// 変更前（gap なし版）— 実コードと一致
None => {
    frame.fill_rectangle(
        Point::new(x, 0.0),
        Size::new(1.0, bounds.height),
        divider_color,
    );
}

// 変更後（gap なし版）— HEADER_HEIGHT を起点に制限
None => {
    frame.fill_rectangle(
        Point::new(x, HEADER_HEIGHT),
        Size::new(1.0, (bounds.height - HEADER_HEIGHT).max(0.0)),
        divider_color,
    );
}
```

## ラベル設計

| 列 | ラベル | 意味 |
|----|--------|------|
| BidOrderQty | `買板` | 板の買い残（指値注文残量） |
| SellTradeQty | `売T` | 直近の売り約定累積（8 分ウィンドウ） |
| Price | `価格` | 価格ティック |
| BuyTradeQty | `買T` | 直近の買い約定累積（8 分ウィンドウ） |
| AskOrderQty | `売板` | 板の売り残（指値注文残量） |

文字色は `background.base.text` を 55% alpha で薄くして、データ行のテキストと区別する。

## 受け入れ条件

### 手動目視確認

1. Ladder ペインの最上部に固定ヘッダ行が表示される
2. スクロールしてもヘッダは動かない（コンテンツ行がヘッダの下に潜る）
3. ペイン幅変更・ティックサイズ変更時もラベル位置が列に追従する
4. データ未到着時（空板）でもヘッダが表示される

### 機械検証

5. `cargo test --workspace` がすべて通る
6. `cargo clippy -- -D warnings` が警告ゼロ

**追加ユニットテスト計画**（`src/screen/dashboard/panel/ladder.rs` 末尾の `#[cfg(test)] mod tests` ブロックに追加）:

- **`mid_screen_y` 数値検証**: `visible_rows()` が使う `mid_screen_y` が
  `HEADER_HEIGHT + (h - HEADER_HEIGHT) * 0.5` であることを確認する。
  具体的には `bounds.height = 200.0` のとき `mid_screen_y == 108.0`（= 16 + 184*0.5）
  となることを `#[test]` で assert する（`visible_rows` を呼び出し、
  先頭行の `y` 座標から逆算して検証する）。

  ```rust
  #[cfg(test)]
  mod tests {
      use super::*;
      // ...
      #[test]
      fn mid_screen_y_is_offset_by_header_height() {
          // bounds.height=200, scroll=0, mid_screen_y = 16 + (200-16)*0.5 = 108.0
          // idx=0 行の top_y_screen = mid_screen_y + PriceGrid::top_y(0) - 0 = 108.0
          // この値を visible_rows 戻り値から検証する
          // ...（詳細は実装時に確定）
      }
  }
  ```

- **`build_price_grid()` None パス**: orderbook・trades がすべて空の `Ladder` インスタンスで
  `build_price_grid()` が `None` を返すことを assert する。

  ```rust
  #[test]
  fn build_price_grid_returns_none_when_empty() {
      let ladder = Ladder::new(None, test_ticker_info(), default_step());
      assert!(ladder.build_price_grid().is_none());
  }
  ```

- **ペイン幅極小時のパニック不発**: ペイン幅 `≤ 60px` のとき `column_ranges()` が
  パニックを起こさないことを確認する（`usable_width.max(0.0)` がガード済みであることの回帰テスト）。

- `cargo test --workspace` でこれらがすべて通ること。

## スコープ外

- ヘッダのトグル設定（`Config` への追加）: 常時表示で十分
- ヘッダ行クリックでのソート: Ladder は板表示専用であり不要
- 英語ラベル: 現状日本株向け UI のため日本語を採用

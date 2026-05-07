# 注文（CLMKabuNewOrder）パラメータの定石

マニュアル該当章: [`#CLMKabuNewOrder`](../manual_files/mfds_json_api_ref_text.html#CLMKabuNewOrder)。Python サンプル [`e_api_order_genbutsu_buy_tel.py:460-518`](../samples/e_api_order_genbutsu_buy_tel.py/e_api_order_genbutsu_buy_tel.py#L460) のコメントに No.1〜No.28 の項目解説が揃っている（入出力別、char 長、取り得る値）。

## 入力項目（頻出のみ抜粋）

| 項目 | 意味 | 代表値 |
| :--- | :--- | :--- |
| `sIssueCode` | 銘柄コード | 通常 4 桁 / 優先株 5 桁（例 `6501`, `25935`） |
| `sSizyouC` | 市場 | `00`=東証（現状これのみ） |
| `sBaibaiKubun` | 売買区分 | `1`=売 / `3`=買 / `5`=現渡 / `7`=現引 |
| `sCondition` | 執行条件 | `0`=指定なし / `2`=寄付 / `4`=引け / `6`=不成 |
| `sOrderPrice` | 注文値段 | `*`=指定なし / `0`=成行 / それ以外は指値（呼値単位で丸める — マスタデータ利用方法 `2-12 呼値`） |
| `sOrderSuryou` | 注文数量 | 整数（単元株数の倍数） |
| `sGenkinShinyouKubun` | 現金信用区分 | `0`=現物 / `2`=制度信用新規 6m / `4`=制度信用返済 6m / `6`=一般信用新規 6m / `8`=一般信用返済 6m |
| `sOrderExpireDay` | 注文期日 | `0`=当日 / それ以外は `YYYYMMDD`（10 営業日まで） |
| `sGyakusasiOrderType` | 逆指値注文種別 | `0`=通常 |
| `sGyakusasiZyouken` | 逆指値条件 | `0`=指定なし / 条件値段 |
| `sGyakusasiPrice` | 逆指値値段 | `*`=指定なし / `0`=成行 / それ以外 |
| `sTatebiType` | 建日種類 | `*`=指定なし（現物または新規）/ `1`=個別指定 / `2`=建日順 / `3`=単価益順 / `4`=単価損順 |
| `sZyoutoekiKazeiC` | 譲渡益課税区分 | `1`=特定 / `3`=一般 / `5`=NISA（**ログイン応答を流用**） |
| `sTategyokuZyoutoekiKazeiC` | 建玉譲渡益課税区分 | 現引/現渡時のみ意味を持つ（`*`/`1`/`3`/`5`） |
| `sSecondPassword` | 第二暗証番号 | **省略不可**（ブラウザ版と異なり API 発注では必須） |
| `aCLMKabuHensaiData` | 返済リスト | 個別指定時のみ必須。`sTategyokuNumber` / `sTatebiZyuni` / `sOrderSuryou` の配列 |

## 出力項目（抜粋）

`sOrderNumber`（注文番号、訂正・取消に必要）/ `sEigyouDay`（営業日 YYYYMMDD）/ `sOrderUkewatasiKingaku`（受渡金額）/ `sOrderTesuryou`（手数料）/ `sOrderSyouhizei`（消費税）。注文番号は以降の訂正・取消 API の `sOrderNumber` 引数として必ず保存する。

**信用 6 ヶ月以外（無期限・短期）は `CLMKabuNewOrder` では直接指定できない**（関連マニュアル参照）。

## 訂正・取消の関係

- `CLMKabuCorrectOrder`: `sOrderNumber` を指定し、変更可能なのは `sOrderPrice` / `sCondition` / `sOrderSuryou` / `sOrderExpireDay` など限定項目。新規注文と同じく `sSecondPassword` が必要。
- `CLMKabuCancelOrder`: `sOrderNumber` 単位。
- `CLMKabuCancelOrderAll`: 未約定全件。誤爆に注意。

**参考**: 各発注系サンプルは `samples/e_api_order_*_tel.py/` 配下。現物買=`genbutsu_buy`、信用新規買=`shinyou_buy_shinki`、信用返済（建玉個別指定）=`shinyou_*_hensai_kobetsu` といった命名で、引数の組合せ例がそのまま読める。

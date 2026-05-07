# 立花証券 e支店 API — sCLMID 一覧

マニュアルの章立てに対応。新しい機能を追加する際は、この表から該当 `sCLMID` を選び、マニュアル該当セクション（[manual_files/mfds_json_api_ref_text.html](../manual_files/mfds_json_api_ref_text.html) の `id` 属性）を読んでパラメータを確定させる。

## 認証 I/F — `ComT2`

| sCLMID | 機能 | 接続先 |
| :--- | :--- | :--- |
| `CLMAuthLoginRequest` | ログイン（仮想 URL 取得） | `{BASE_URL}/auth/` |
| `CLMAuthLogoutRequest` | ログアウト | `sUrlRequest` |

## 業務機能（REQUEST I/F）— `ComT3` — 接続先 `sUrlRequest`

| sCLMID | 機能 |
| :--- | :--- |
| `CLMKabuNewOrder` | 株式新規注文（現物/信用、買/売、成行/指値/逆指値） |
| `CLMKabuCorrectOrder` | 株式訂正注文 |
| `CLMKabuCancelOrder` | 株式取消注文 |
| `CLMKabuCancelOrderAll` | 株式一括取消 |
| `CLMGenbutuKabuList` | 現物保有銘柄一覧 |
| `CLMShinyouTategyokuList` | 信用建玉一覧 |
| `CLMZanKaiKanougaku` | 買余力 |
| `CLMZanShinkiKanoIjiritu` | 建余力＆本日維持率 |
| `CLMZanUriKanousuu` | 売却可能数量 |
| `CLMOrderList` | 注文一覧 |
| `CLMOrderListDetail` | 注文約定一覧（詳細） |
| `CLMZanKaiSummary` | 可能額サマリー |
| `CLMZanKaiKanougakuSuii` | 可能額推移 |
| `CLMZanKaiGenbutuKaitukeSyousai` | 現物株式買付可能額詳細 |
| `CLMZanKaiSinyouSinkidateSyousai` | 信用新規建て可能額詳細 |
| `CLMZanRealHosyoukinRitu` | リアル保証金率 |

## マスタ機能 — `ComT4` — 接続先 `sUrlMaster`

| sCLMID | 機能 |
| :--- | :--- |
| `CLMEventDownload` | マスタ一括ダウンロード（ストリーム、約 21MB） |
| `CLMMfdsGetMasterData` | マスタ情報問合取得（個別列指定） |
| `CLMMfdsGetNewsHead` | ニュースヘッダー |
| `CLMMfdsGetNewsBody` | ニュースボディー（**Base64 エンコード**、デコード必須） |
| `CLMMfdsGetIssueDetail` | 銘柄詳細情報 |
| `CLMMfdsGetSyoukinZan` | 証金残 |
| `CLMMfdsGetShinyouZan` | 信用残 |
| `CLMMfdsGetHibuInfo` | 逆日歩 |

## 時価情報機能 — `ComT5` — 接続先 `sUrlPrice`

| sCLMID | 機能 |
| :--- | :--- |
| `CLMMfdsGetMarketPrice` | 時価スナップショット（最大 120 銘柄） |
| `CLMMfdsGetMarketPriceHistory` | 日足履歴（1 銘柄、最大約 20 年分） |

## EVENT I/F — `ComT6` — 接続先 `sUrlEvent` / `sUrlEventWebSocket`

プッシュ型。HTTP はチャンク長期接続（long-polling）、WebSocket 版もあり。詳細は別紙「立花証券・ｅ支店・ＡＰＩ、EVENT I/F 利用方法、データ仕様」（HTML 版 `api_event_if_v4r7.pdf` / Excel 版 `api_event_if.xlsx`、どちらも `manual_files/` には同梱なし）。手元では Python サンプル [`e_api_event_receive_tel.py`](../samples/e_api_event_receive_tel.py/e_api_event_receive_tel.py) / [`e_api_websocket_receive_tel.py`](../samples/e_api_websocket_receive_tel.py/e_api_websocket_receive_tel.py) の冒頭コメントが抜粋リファレンスとして機能する。

# マスタダウンロード（CLMEventDownload）の特殊ルール

`CLMEventDownload` は他の REQUEST と流れが違う:

- ストリーム形式（Python の `urllib3` で `preload_content=False` 相当、または httpx の `stream` API）で全量配信。
- 1 レコードの終端は `}`、**全体の終端はレコード `{"sCLMID":"CLMEventDownloadComplete", ...}` の到着**。Python サンプルは `str_terminate = 'CLMEventDownloadComplete'` を定数化している。
- 接続先は `sUrlMaster`（`sUrlRequest` ではない — [`e_api_get_master_tel.py:578-580`](../samples/e_api_get_master_tel.py/e_api_get_master_tel.py#L578)）。
- `sJsonOfmt` は `"4"` を使う（1 行 1 JSON 形式、ファイル保存・後続パース向け。`"5"` を使うと区切れなくなる）。
- 受信チャンクをバイト列で蓄積し `byte_data[-1:] == b'}'` で 1 レコード分として Shift-JIS デコード → `json.loads`（[`e_api_get_master_tel.py:492-518`](../samples/e_api_get_master_tel.py/e_api_get_master_tel.py#L492)）。
- データ量が大きいため、メモリ展開ではなくストリーム処理を守ること。

## マスタデータ識別子（`sTargetCLMID`）

- `CLMIssueMstKabu` 銘柄マスタ（株）
- `CLMIssueSizyouMstKabu` 銘柄市場マスタ（株）
- `CLMIssueMstSak` 銘柄マスタ（先物）
- `CLMIssueMstOp` 銘柄マスタ（OP）
- `CLMIssueMstOther` 日経平均・為替など
- `CLMOrderErrReason` 取引所エラー理由コード
- `CLMDateZyouhou` 日付情報

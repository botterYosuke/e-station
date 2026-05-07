# EVENT / WebSocket ストリームのパース規約

## 区切り文字

受信データは ASCII 制御文字を区切りとして項目を羅列する:

| 記号 | コード | 意味 |
| :--- | :--- | :--- |
| `^A` | `\x01` | 項目区切り |
| `^B` | `\x02` | 項目名と値の区切り |
| `^C` | `\x03` | 値と値の区切り（複数値時） |
| `LF` | `\x0A` | メッセージ区切り（WebSocket は `^A` 末尾でも区切る） |

形式例: `項目A1^B値B1^A項目A2^B値B21^CB22^CB23^A...`

## キー命名

キーは `<型>_<行番号>_<情報コード>` 形式:

- 例 `p_1_DPP` → 型 `p`（プレーン文字列）・行番号 `1`・情報コード `DPP`（現在値）
- 行番号は `p_gyou_no`（1〜120）と対応
- パース: Python ヘルパー `tachibana_codec.parse_event_frame(data: str) -> list[tuple[str, str]]`

## URL パラメータ（重要な固定値）

EVENT I/F は **REQUEST と違い通常の `key=value&...` 形式**で組み立てる（R2 例外）。サンプルの並び順と値に合わせる:

```
{sUrlEvent}?p_evt_cmd=ST,KP,EC,SS,US,FD
           &p_eno=0            ※イベント通知番号（0=全件、再送時は指定値の次から）
           &p_rid=22           ※株価ボード・アプリ識別値（No.2: e支店・API、時価配信あり）
           &p_board_no=1000    ※固定値（株価ボード機能）
           &p_gyou_no=N[,N,...]    ※行番号（1-120）
           &p_issue_code=NNNN[,NNNN,...]   ※銘柄コード
           &p_mkt_code=NN[,NN,...]         ※市場コード
```

`p_evt_cmd` の種別（マニュアル別紙「EVENT I/F 利用方法」 p3/26 および [`e_api_event_receive_tel.py` l.534-544](../samples/e_api_event_receive_tel.py/e_api_event_receive_tel.py)）:

| コード | 意味 | 通知契機 |
| :--- | :--- | :--- |
| `ST` | エラーステータス | 発生時 |
| `KP` | キープアライブ | 5 秒間通知未送信時 |
| `FD` | 時価情報 | 初回はメモリ内スナップショット（全データ）、以降は変化分のみ |
| `EC` | 注文約定通知 | 初回は当日分の未削除通知を接続毎に再送、以降は発生時 |
| `NS` | ニュース通知 | 初回再送、以降発生時。**重いため必要時のみ** |
| `SS` | システムステータス | 初回再送、以降発生時 |
| `US` | 運用ステータス | 初回再送、以降発生時 |
| `RR` | 画面リフレッシュ | 現時点不使用（指定しても無視） |

## 注意点

- **EVENT URL に `\n` や `\t` を入れない**（制御文字でサーバがエラー応答する）。
- WebSocket 接続は Python 側 `python/engine/exchanges/tachibana_ws.py` に集約する。`websockets.connect(uri, ping_interval=None, ping_timeout=None)` で `websockets` ライブラリの自動 ping を無効化し、**受信ループで ping を受け取ったら手動で pong を返す**（[`e_api_websocket_receive_tel.py:710-723`](../samples/e_api_websocket_receive_tel.py/e_api_websocket_receive_tel.py#L710) の `pong_handler` を参照）。
- `p_errno:"2"` は仮想 URL 無効 → 再ログイン（電話認証から）。
- EVENT 受信データはメッセージ単位で `LF` または `^A` 終端。一塊のチャンクに複数メッセージが含まれるため、受信バッファを蓄積しながら区切り子で分割する必要がある。
- 受信本文も Shift-JIS。REQUEST と同じく UTF-8 前提で読むと銘柄名・ニュース本文が文字化けする。

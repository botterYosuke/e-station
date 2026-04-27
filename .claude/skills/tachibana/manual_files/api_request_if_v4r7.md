| 立花証券・ｅ支店・ＡＰＩ（ｖ４ｒ７）、REQUEST I/F、利用方法、データ仕様 |     |     |     |     |     | Create | 2020.07.15 |     |     |
| ----------------------------------------- | --- | --- | --- | --- | --- | ------ | ---------- | --- | --- |
|                                           |     |     |     |     |     | Update | 2025.05.31 |     |     |
本ＡＰＩの業務系機能のインタフェースについて以下に記す。
※インタフェースについては「立花証券・ｅ支店・ＡＰＩ専用ページ、５．マニュアル、１－２．インタフェース概要」を参照。
※日本語文字コードは ShiftJIS コードである。
・・・本バージョンでの（仕様）変更箇所を示す。
・・・利用不可、廃止など。
１． 接続先
本I/F の利用には本ＡＰＩ・認証機能で取得する「仮想URL（REQUEST、MASTER、PRICE）」（以下仮想URLと呼ぶ）へのアクセスが必要である。
※仮想ＵＲＬについては「立花証券・ｅ支店・ＡＰＩ専用ページ、３．ご利用方法、６．仮想ＵＲＬについて」を参照。
※認証機能については「立花証券・ｅ支店・ＡＰＩ専用ページ、５．マニュアル、１－２．インタフェース概要」を参照。
２． 利用方法
本Ｉ／ＦはＪＳＯＮ形式の文字列を引数に指定することで要求を送信し、応答としてＪＳＯＮ形式の文字列を受信するインタフェースである。
要求、応答共に共通の項目を利用し、要求する機能や該当機能に必要な引数項目を指定する。
（１）共通項目
以下に本Ｉ／Ｆで受け渡しを行う共通項目を一覧に記す。
|     | No 項目  |     | 例   |                                           | 説明  |     |     | 要求  | 応答  |
| --- | ------ | --- | --- | ----------------------------------------- | --- | --- | --- | --- | --- |
|     | 1 p_no | "1" |     | クライアントからの送信通番（1～ 9999999999）、応答は要求時の値を設定。 |     |     |     | 設定  | 設定  |
2 p_sd_date 2020.06.19-13:51:25.122 クライアント、またはｅ支店システムからの送信日時（YYYY.MM.DD-HH:MM:SS.TTT）。 設定 設定
3 p_rv_date 2020.06.19-13:51:25.122 ｅ支店システムでのクライアントからの要求受信日時（YYYY.MM.DD-HH:MM:SS.TTT）。 不要 設定
|     | 4 p_errno | "2"                                            |     | エラー番号、０：正常、０：以外はエラーで p_err にその内容を英語で設定。 |     |     |     | 不要  | 設定  |
| --- | --------- | ---------------------------------------------- | --- | --------------------------------------- | --- | --- | --- | --- | --- |
|     | 5 p_err   | セッションが切断しました。エラー文言、p_errnoが「０：正常」以外（エラー時）のみ設定。 |     |                                         |     |     |     | 不要  | 設定  |
|     | 6 sCLMID  | CLMAuthLoginRequest                            |     | 機能ID、本項目にて要求時の機能を指定する。                  |     |     |     | 設定  | 設定  |
※要求時は No.1,2,6 を設定しそれ以降は該当機能に対する引数項目を列挙する。
　応答時は No.1,2,3,4,5,6 が設定され、p_err＝０（正常）の場合は続いて業務応答項目を羅列する。p_err!=０（エラー）の場合は共通項目のみ設定。
※要求に対する応答（エラー）については No.4 で示すＡＰＩの制御的なエラー以外に業務的なエラー（例：お金がなくて発注できない等）がある。
　業務的なエラーは共通項目以降に続く該当機能の応答項目に設定されるため、その値を参照することで判断を行う。
※No.3 - No.2 がＡＰＩ（ｅ支店サーバ）の処理時間を示す（APACHEの処理時間は含まれない）。
※要求．p_no は認証要求の該当値を初期値とし、それ以降クライアントは要求送信時に＋１（以上）し送信する。
　多重送信対策として前要求.p_no＞＝今要求.p_no の場合はエラー応答(p_errno=6)を返す。
　ただし例外としてマスタ情報ダウンロード要求については左記チェックをしない。
※本番バージョン（ｖ４ｒ１）よりクライアントからの要求．p_sd_dateによる遅延等チェック機能を追加。
1／8 Copyright (C) THE TACHIBANA SECURITIES CO.,LTD. All rights reserved.

　要求．p_sd_date  ＜ ｅ支店・ＡＰＩのサーバ時刻（ntp により同期)－３０秒（ネットワーク経路上での遅延検出）の場合はエラー応答(p_errno=8）を返す。
　上記機能追加により、本ＡＰＩを利用する場合は該当機器の時刻を ntp などで正確に合わせる必要がある。
【p_errno 一覧】
| No p_errno |     |     |     | p_err |     |                | 備考  |     |
| ---------- | --- | --- | --- | ----- | --- | -------------- | --- | --- |
| 1 0        | ""  |     |     |       |     | no problem.    |     |     |
| 2 1        |     |     |     |       |     | board no data. |     |     |
""
| 3 2 | セッションが切断しました。       |     |     |     |     | session inactive.               |     |     |
| --- | ------------------- | --- | --- | --- | --- | ------------------------------- | --- | --- |
| 4 6 | 同または以前の送信通番を検知しました。 |     |     |     |     | p_no is no progress.            |     |     |
| 5 8 |                     |     |     |     |     | p_sd_date is exceed limit time. |     |     |
送信日時の受付制限時間超過を検知しました。
| 6 9  | システム、サービス停止中。 |     |     |     |     | mfds service offline.  |     |     |
| ---- | ------------- | --- | --- | --- | --- | ---------------------- | --- | --- |
| 7 -1 | 引数エラー。        |     |     |     |     | parameter error.       |     |     |
| 8 -2 |               |     |     |     |     | database access error. |     |     |
ただいまシステムが大変混み合っております。しばらく経ってから操作してください。
9 -3 ただいまシステムが大変混み合っております。しばらく経ってから操作してください。 sapsv access error.
| 10 -12 | システム、サービス停止中。 |     |     |     |     | service is offline.    |     |     |
| ------ | ------------- | --- | --- | --- | --- | ---------------------- | --- | --- |
| 11 -62 | システム、情報提供時間外。 |     |     |     |     | sotkchouse is offline. |     |     |
※No.2 はリアルタイム株価ボードアプリケーション用のエラーのため返らない。
（２）オプション項目
以下に本Ｉ／Ｆ要求時のオプション項目を一覧に記す。
| No 項目       |     | 例   |               |     | 説明  |     | 要求  | 応答  |
| ----------- | --- | --- | ------------- | --- | --- | --- | --- | --- |
| 1 sJsonOfmt | "1" |     | 応答データフォーマット指定 |     |     |     | 設定  | -   |
"0":標準（引数項目番号に圧縮し応答）
"1":人が見やすい形（JSON文字列をタブや改行で整形し応答）
"2":{・・・}後改行追加
"4":引数項目番号でなく引数項目名で応答する
※引数項目が未設定の場合は"0"として取り扱う。
※”2”は連続応答送信されるマスタダウンロードのデータ区切り子として利用。
※引数はビットのため例えば"5"を渡すと人が見やすい形＋引数項目名で応答する。
※サンプルプログラムでは上記を設定しても応答のJSON文字列を解析等処理しているため効果はない。（解析等処理結果を表示）
　ブラウザで要求送信＆応答受信時は応答（のJSON文字列）をそのまま表示するため機能する。
2／8 Copyright (C) THE TACHIBANA SECURITIES CO.,LTD. All rights reserved.

（３）業務機能
以下に本Ｉ／Ｆでサポートする機能について一覧に記す。
| No         | 機能名称 | 機能区分 | 機能ＩＤ（sCLMID）        | 仮想URL  | 備考        |     |
| ---------- | ---- | ---- | ------------------- | ------ | --------- | --- |
| 1 ログイン要求電文 |      | 認証   | CLMAuthLoginRequest | 認証用URL |           |     |
|            |      |      |                     |        | 本ＡＰＩ、認証機能 | ※２  |
| 2 ログイン応答電文 |      | 認証   | CLMAuthLoginAck     | -      | 上記応答      | ※２  |
3 ログアウト要求電文 認証 CLMAuthLogoutRequest 仮想URL（REQUEST） 本ＡＰＩ、ログアウト機能 ※２
| 4 ログアウト応答電文 |     | 認証  | CLMAuthLogoutAck | -              |                |     |
| ----------- | --- | --- | ---------------- | -------------- | -------------- | --- |
|             |     |     |                  |                | 上記応答           | ※２  |
| 5 株式新規注文　   |     | 業務  | CLMKabuNewOrder  | 仮想URL（REQUEST） | 本ＡＰＩ、業務（注文系）機能 |     |
6 株式訂正注文　 業務 CLMKabuCorrectOrder 仮想URL（REQUEST） 本ＡＰＩ、業務（注文系）機能
| 7 株式取消注文　　 |     | 業務  | CLMKabuCancelOrder | 仮想URL（REQUEST） |     |     |
| ---------- | --- | --- | ------------------ | -------------- | --- | --- |
本ＡＰＩ、業務（注文系）機能
8 現物保有銘柄一覧 業務 CLMGenbutuKabuList 仮想URL（REQUEST） 本ＡＰＩ、業務（照会系）機能
9 信用建玉一覧 業務 CLMShinyouTategyokuList 仮想URL（REQUEST） 本ＡＰＩ、業務（照会系）機能
| 10 買余力 |     | 業務  | CLMZanKaiKanougaku | 仮想URL（REQUEST） |     |     |
| ------ | --- | --- | ------------------ | -------------- | --- | --- |
本ＡＰＩ、業務（照会系）機能
11 建余力＆本日維持率 業務 CLMZanShinkiKanoIjiritu 仮想URL（REQUEST） 本ＡＰＩ、業務（照会系）機能
| 12 売却可能数量 |     | 業務  | CLMZanUriKanousuu | 仮想URL（REQUEST） | 本ＡＰＩ、業務（照会系）機能 |     |
| --------- | --- | --- | ----------------- | -------------- | -------------- | --- |
| 13 注文一覧   |     | 業務  | CLMOrderList      | 仮想URL（REQUEST） | 本ＡＰＩ、業務（照会系）機能 |     |
14 注文約定一覧（詳細） 業務 CLMOrderListDetail 仮想URL（REQUEST） 本ＡＰＩ、業務（照会系）機能
| 15 可能額サマリー |     | 業務  | CLMZanKaiSummary | 仮想URL（REQUEST） | 本ＡＰＩ、業務（照会系）機能 |     |
| ---------- | --- | --- | ---------------- | -------------- | -------------- | --- |
16 可能額推移 業務 CLMZanKaiKanougakuSuii 仮想URL（REQUEST） 本ＡＰＩ、業務（照会系）機能
17 現物株式買付可能額詳細 業務 CLMZanKaiGenbutuKaitukeSyousai 仮想URL（REQUEST） 本ＡＰＩ、業務（照会系）機能
18 信用新規建て可能額詳細 業務 CLMZanKaiSinyouSinkidateSyousai 仮想URL（REQUEST） 本ＡＰＩ、業務（照会系）機能
19 リアル保証金率 業務 CLMZanRealHosyoukinRitu 仮想URL（REQUEST） 本ＡＰＩ、業務（照会系）機能
20 マスタ情報ダウンロード マスタ CLMEventDownload 仮想URL（MASTER） 本ＡＰＩ、マスタ機能 ※１
| 1 システムステータス     |     | マスタ | CLMSystemStatus          | -   | 状態通知  |     |
| --------------- | --- | --- | ------------------------ | --- | ----- | --- |
| 2 日付情報          |     | マスタ | CLMDateZyouhou           | -   | マスタ通知 |     |
| 3 呼値            |     | マスタ | CLMYobine                | -   | マスタ通知 |     |
| 4 運用ステータス別状態    |     | マスタ | CLMUnyouStatus           | -   | 状態通知  |     |
| 5 運用ステータス（株式）   |     | マスタ | CLMUnyouStatusKabu       | -   | 状態通知  |     |
| 6 運用運用ステータス（派生） |     | マスタ | CLMUnyouStatusHasei      | -   | 状態通知  |     |
| 7 株式銘柄マスタ       |     | マスタ | CLMIssueMstKabu          | -   | マスタ通知 |     |
| 8 株式銘柄市場マスタ     |     | マスタ | CLMIssueSizyouMstKabu    | -   | マスタ通知 |     |
| 9 株式銘柄別・市場別規制   |     | マスタ | CLMIssueSizyouKiseiKabu  | -   | マスタ通知 |     |
| 10 先物銘柄マスタ      |     | マスタ | CLMIssueMstSak           | -   | マスタ通知 |     |
| 11 オプション銘柄マスタ   |     | マスタ | CLMIssueMstOp            | -   | マスタ通知 |     |
| 12 派生銘柄別・市場別規制  |     | マスタ | CLMIssueSizyouKiseiHasei | -   | マスタ通知 |     |
| 13 代用掛目         |     | マスタ | CLMDaiyouKakeme          | -   | マスタ通知 |     |
3／8 Copyright (C) THE TACHIBANA SECURITIES CO.,LTD. All rights reserved.

14 保証金マスタ マスタ CLMHosyoukinMst - マスタ通知
15 取引所エラー等理由コード マスタ CLMOrderErrReason - マスタ通知
16 初期ダウンロード終了通知 マスタ CLMEventDownloadComplete - マスタ情報初期ダウンロード終了
21 マスタ情報問合取得 マスタ CLMMfdsGetMasterData 仮想URL（MASTER） 本ＡＰＩ、マスタ機能
22 ニュースヘッダー問合取得 マスタ CLMMfdsGetNewsHead 仮想URL（MASTER） 本ＡＰＩ、マスタ機能
23 ニュースボディー問合取得 マスタ CLMMfdsGetNewsBody 仮想URL（MASTER） 本ＡＰＩ、マスタ機能
24 銘柄詳細情報問合取得 マスタ CLMMfdsGetIssueDetail 仮想URL（MASTER） 本ＡＰＩ、マスタ機能
25 証金残情報問合取得 マスタ CLMMfdsGetSyoukinZan 仮想URL（MASTER） 本ＡＰＩ、マスタ機能
26 信用残情報問合取得 マスタ CLMMfdsGetShinyouZan 仮想URL（MASTER） 本ＡＰＩ、マスタ機能
27 逆日歩情報問合取得 マスタ CLMMfdsGetHibuInfo 仮想URL（MASTER） 本ＡＰＩ、マスタ機能
28 時価情報問合取得 時価情報 CLMMfdsGetMarketPrice 仮想URL（PRICE） 本ＡＰＩ、時価情報機能
29 蓄積情報問合取得 時価情報 CLMMfdsGetMarketPriceHistory 仮想URL（PRICE） 本ＡＰＩ、時価情報機能
※１、マスタ情報ダウンロード機能とはＡＰＩを利用し注文入力をする際、クライアント側で入力チェックを行うための各種マスタ情報等を
初期ダウンロード、及び日中、ｅ支店システムでのマスタ更新時の更新情報をリアルタイムで通知するためのインタフェース機能である。
初期ダウンロード終了後はマスタ更新のタイミングでリアルタイムに該当更新情報を No.20.1-15 の応答で通知する。
よって、本Ｉ／Ｆでマスタ情報ダウンロード要求を送信した場合、本ＡＰＩの利用終了まで接続し続け更新情報を受信するか
更新情報の受信が必要ない場合は No20-16 を応答受信した時点でクライアント側で切断する。
応答「CLMEventDownloadComplete」で初期データのダウンロード終了を意味する。（その間、各マスタデータを応答する）
応答「CLMEventDownloadComplete」以降は日中にマスタ変更がある場合に該当変更データを応答送信する。
応答「CLMEventDownloadComplete」のみ２．（１）共通項目 No.1,2,3,4,5,6 を設定、それ以外（ダウンロードデータ）は No.2,6 のみ設定。
「
本Ｉ／Ｆの業務処理要求についてはｅ支店システムで必要なチェック処理を行うため、
マスタ情報のダウンロード＆クライアントでの入力チェック等の実装は任意である。
マスタ情報ダウンロード要求を送信すると応答としてNo20.1~No.20.16までが初期データとして連続送信される。
』
※２、業務区分「認証」については要求と応答で機能ＩＤが異なる。
応答の共通項目、p_err != 0（エラー）時は要求時の機能ＩＤを応答に設定するが、p_err=0の場合は応答用機能ＩＤを設定する。
4／8 Copyright (C) THE TACHIBANA SECURITIES CO.,LTD. All rights reserved.

（４）業務機能の資料
業務機能毎の引数項目仕様については「立花証券・ｅ支店・ＡＰＩ専用ページ、５．マニュアル」を参照。
（５）使用例
ログイン要求の場合、認証ＵＲＬに対し以下を送信する。
{
"p_no":"1",
"p_sd_date":"2020.06.19-13:51:25.122",
"sCLMID":"CLMAuthLoginRequest",
"sUserId":"login",
"sPassword":"pswd",
"sJsonOfmt":"5"
}
ログイン応答で以下を受信。
{
"p_no":"1",
"p_sd_date":"2020.07.10-07:58:41.359",
"p_rv_date":"2020.07.10-07:58:41.223",
"p_errno":"0",
"p_err":"",
"sCLMID":"CLMAuthLoginAck",
"sResultCode":"0",
"sResultText":"",
"sZyoutoekiKazeiC":"1",
"sSecondPasswordOmit":"1",
"sLastLoginDate":"20200710075613",
"sKouzaKaisetuDay":"20021112",
.
.
.
"sFxMousikomiFlg":"0",
"sUrlRequest":"https://dns-name/prefix_version/oxoxoxoxoxoxoxoxoxox/",
"sUrlMaster":"https://dns-name/prefix_version/oxoxoxoxoxoxoxoxoxox/",
"sUrlPrice":"https://dns-name/prefix_version/oxoxoxoxoxoxoxoxoxox/",
"sUrlEvent":"https://dns-name/prefix_version/oxoxoxoxoxoxoxox/"
5／8 Copyright (C) THE TACHIBANA SECURITIES CO.,LTD. All rights reserved.

}
※認証等エラーが無い場合は仮想URLを要求毎に新規自動生成し返す。
※応答項目．sUrlRequest が仮想URL（REQUEST）、sUrlMaster が仮想URL（MASTER）、sUrlPrice が仮想URL（PRICE）、
sUrlEvent が仮想URL（EVENT）である。
※JSON 文字列の引数項目名は通信データ量削減のため、サンプルの変換プログラム mfds_json_api_compress_vN[rN].js を利用し
引数項目番号に圧縮変換し顧客側システムと立花証券側システムとの間のデータ送受信を行う。
（本記載例は分かりやすいよう引数項目名で記載）
詳細については別紙「ｅ支店・ＡＰＩ、ブラウザからの利用方法」１１２行目のポイントの記載参照。
なお、変換プログラムのご利用方法はサンプルプログラム参照。
ログイン応答（認証エラー時）。
{
"p_err":"",
"p_errno":"0",
"p_no":"6",
"p_rv_date":"2022.09.13-11:55:06.773",
"p_sd_date":"2022.09.13-11:55:06.791",
"sCLMID":"CLMAuthLoginAck",
"sFurikaeKouzaKubun":"",
"sGaikokuKouzaKubun":"",
"sHikazeiKouzaKubun":"",
"sHogoAdukariKouzaKubun":"",
"sKawaseKouzaKubun":"",
"sKinsyouhouMidokuFlg":"",
"sLastLoginDate":"",
"sMMFKouzaKubun":"",
"sMRFKouzaKubun":"",
"sResultCode":"10031",
"sResultText":"ユーザIDか暗証番号をお間違えです。ご確認の上、再度ご入力下さい。なお、お間違えの回数が・・・
"sSakopKouzaKubun":"",
"sSecondPasswordOmit":"",
"sSinyouKouzaKubun":"",
"sSogoKouzaKubun":"",
"sTokuteiHaitouKouzaKubun":"",
"sTokuteiKanriKouzaKubun":"",
"sTokuteiKouzaKubunGenbutu":"",
6／8 Copyright (C) THE TACHIBANA SECURITIES CO.,LTD. All rights reserved.

"sTokuteiKouzaKubunSinyou":"",
"sTokuteiKouzaKubunTousin":"",
"sTyukokufKouzaKubun":"",
"sUrlEvent":"",
"sUrlMaster":"",
"sUrlPrice":"",
"sUrlRequest":"",
"sZyoutoekiKazeiC":""
}
※業務エラーコード（sResultCode）がエラー「"0" 以外」の場合、業務エラー内容（sResultText）にエラー内容を設定、
仮想URLを含む各業務応答項目は「""」で応答。
認証時（セッション無効等）エラー検知時は以下共通項目のみを応答。
{
"p_no":"1",
"p_sd_date":"2020.07.10-07:55:03.511",
"p_rv_date":"2020.07.10-07:55:03.492",
"p_errno":"2",
"p_err":"セッションが切断しました。",
"sCLMID":"MAuthLoginRequest"
}
※業務処理実行前の制御的なチェックでエラーの場合、sCLMID は要求時値を設定する。
7／8 Copyright (C) THE TACHIBANA SECURITIES CO.,LTD. All rights reserved.

応答サンプル：
{
"p_no":"1",
"p_sd_date":"2020.07.10-07:55:03.511",
"p_rv_date":"2020.07.10-07:55:03.492",
"p_errno":"-3",
"p_err":"ただいまシステムが大変混み合っております。しばらく経ってから操作してください。",
"sCLMID":"CLMAuthLoginRequest"
}
{
"p_no":"1",
"p_sd_date":"2020.07.10-07:55:03.511",
"p_rv_date":"2020.07.10-07:55:03.492",
"p_errno":"2",
"p_err":"セッションが切断しました。",
"sCLMID":"MAuthLoginRequest"
}
{
"p_no":"1",
"p_sd_date":"2020.07.10-07:55:03.511",
"p_rv_date":"2020.07.10-07:55:03.492",
"p_errno":"9",
"p_err":"システム、サービス停止中。",
"sCLMID":"CLMAuthLoginRequest"
}
8／8 Copyright (C) THE TACHIBANA SECURITIES CO.,LTD. All rights reserved.
e_api_order_shinyou_sell_hensai_kobetsu_tel.py


電話認証対応　信用返済売り注文（建玉個別指定）


ご注意！！ ================================


本番環境に接続した場合、実際に注文が出ます。
市場で条件が一致して約定した場合、取り消せません。
十分にご注意いただき、ご利用ください。



===========================================


１）動作テストを実行した環境


APIバージョン： v4r7
python:3.11.2 / os:debian12



２）事前に立花証券ｅ支店に口座開設が必要です。


３）利用時に変数を設定してください。


	# 必要な設定項目
	# 銘柄コード: my_sIssueCode （実際の銘柄コードを入れてください。）
	# 市場: my_sSizyouC （00:東証   現在(2021/07/01)、東証のみ可能。）
	# 執行条件: my_sCondition   （0:指定なし、2:寄付、4:引け、6:不成。指し値は、0:指定なし。）
	# 注文値段: my_sOrderPrice  （*:指定なし、0:成行、上記以外は、注文値段。小数点については、関連資料:「立花証券・e支店・API、REQUEST I/F、マスタデータ利用方法」の「2-12. 呼値」参照。）
	# 注文数量: my_sOrderSuryou
	#
	# 明細指定（aCLMKabuHensaiData）
	# # No1 ---------
	# my_sTatebiZyuni = '1'                     # 28- 2 sTatebiZyuni 建日順位。1,2,3と返済優先順位を付ける。
	# my_sTategyokuNumber = '202508090000007'   # 28- 1 sTategyokuNumber 新規建玉番号（CLMShinyouTategyokuListのsOrderTategyokuNumber）
	# my_meisai_suryou = '100'                  # 28- 3 sOrderSuryou 注文数量。建玉株数のうちこの優先順位に指定する株数。
	# # class_hensai_data.append(class_def_hensai_data())     # No1は、拡張が不要。No2以降必要。
	# class_hensai_data[-1].add_data(my_sTategyokuNumber, my_sTatebiZyuni, my_meisai_suryou)    # パラメーターを格納。
	# -------------
	# # No2 ---------
	# my_sTatebiZyuni = '2'
	# my_sTategyokuNumber = '202508090000005'
	# my_meisai_suryou = '200'
	# class_hensai_data.append(class_def_hensai_data())     # No2以降、必要。
	# class_hensai_data[-1].add_data(my_sTategyokuNumber, my_sTatebiZyuni, my_meisai_suryou)
	# # -------------
	# 
	# 備考:
	#   注文数量（my_kabusuu）と、明細の数量（my_meisai_suryou）の合計が、
	#   一致している必要があります。
	#
	#   必要な明細分を
	#   「# NoXX ---------」から
	#   「# -------------」まで
	#   セットでコピーして追加する。
	#   No1のみリストの拡張（append）の行は不要。



４)実行は、設定ファイルや「e_api_login_tel.py」と同じディレクトリで実行してください。


事前に
「電話認証＋e_api_login_tel.py実行」
で、仮想URL（１日券）を取得しておいてください。



５）実行内容は、以下になります。


信用 売り 返済 注文（建玉個別指定）を発注。
上記に付随して、送信データや受信データを適宜print()文で出力します。



６）取得した文字列に日本語が入った場合、文字コードはshift-jisです。


７）利用時間外に接続した場合、"p_errno":"9"（システム、サービス停止中。）が返されます。


詳しくは「立花証券・ｅ支店・ＡＰＩ、EVENT I/F 利用方法、データ仕様」4ページをご参照ください。

なおデモ環境のご利用時間はデモ環境の案内ページでください。



８）本サンプルプログラムは、事務方の者が休日や空き時間に作成したため、色々足りておりません。ご容赦ください。


９）本プログラムは自由にご使用ください。


１０）このソフトウェアを使用したことによって生じたすべての障害・損害・不具合等に関して、私と私の関係者および私の所属するいかなる団体・組織とも、一切の責任を負いません。各自の責任においてご使用ください。


# -*- coding: utf-8 -*-
# Copyright (c) 2021 Tachibana Securities Co., Ltd. All rights reserved.


# 2021.07.09,   yo.
# 2023.04.14 reviced,   yo.
# 2025.07.27 reviced,   yo.
#
# 立花証券ｅ支店ＡＰＩ利用のサンプルコード
#
# 動作確認
# Python 3.11.2 / debian12
# API v4r7
#
# 機能: 属性取得、指定した項目別のマスターデータを取得
#
#
#
# 利用方法: 
# 事前に「e_api_login_tel.py」を実行して、
# 仮想URL（1日券）等を取得しておいてください。
# 「e_api_login_tel.py」と同じディレクトリで実行してください。
#
#
# == ご注意: ========================================
#   本番環境にに接続した場合、実際に市場に注文が出ます。
#   市場で約定した場合取り消せません。
# ==================================================
#


import urllib3
import datetime
import json
import time




#--- 共通コード ------------------------------------------------------


# request項目を保存するクラス。配列として使う。
class class_req :
 def __init__(self) :
 self.str_key = ''
 self.str_value = ''
 
 def add_data(self, work_key, work_value) :
 self.str_key = func_check_json_dquat(work_key)
 self.str_value = func_check_json_dquat(work_value)




# 口座属性クラス
class class_def_account_property:
 def __init__(self):
 self.sUserId = '' # userid
 self.sPassword = '' # password
 self.sSecondPassword = '' # 第2パスワード
 self.sUrl = '' # 接続先URL
 self.sJsonOfmt = 5 # 返り値の表示形式指定
 
# ログイン属性クラス
class class_def_login_property:
 def __init__(self):
 self.p_no = 0 # 累積p_no
 self.sJsonOfmt = '' # 返り値の表示形式指定
 self.sResultCode = '' # 結果コード
 self.sResultText = '' # 結果テキスト
 self.sZyoutoekiKazeiC = '' # 譲渡益課税区分  1：特定  3：一般  5：NISA
 self.sSecondPasswordOmit = '' # 暗証番号省略有無Ｃ  22.第二パスワード  APIでは第2暗証番号を省略できない。 関連資料:「立花証券・e支店・API、インターフェース概要」の「3-2.ログイン、ログアウト」参照
 self.sLastLoginDate = '' # 最終ログイン日時
 self.sSogoKouzaKubun = '' # 総合口座開設区分  0：未開設  1：開設
 self.sHogoAdukariKouzaKubun = '' # 保護預り口座開設区分  0：未開設  1：開設
 self.sFurikaeKouzaKubun = '' # 振替決済口座開設区分  0：未開設  1：開設
 self.sGaikokuKouzaKubun = '' # 外国口座開設区分  0：未開設  1：開設
 self.sMRFKouzaKubun = '' # ＭＲＦ口座開設区分  0：未開設  1：開設
 self.sTokuteiKouzaKubunGenbutu = '' # 特定口座区分現物  0：一般  1：特定源泉徴収なし  2：特定源泉徴収あり
 self.sTokuteiKouzaKubunSinyou = '' # 特定口座区分信用  0：一般  1：特定源泉徴収なし  2：特定源泉徴収あり
 self.sTokuteiKouzaKubunTousin = '' # 特定口座区分投信  0：一般  1：特定源泉徴収なし  2：特定源泉徴収あり
 self.sTokuteiHaitouKouzaKubun = '' # 配当特定口座区分  0：未開設  1：開設
 self.sTokuteiKanriKouzaKubun = '' # 特定管理口座開設区分  0：未開設  1：開設
 self.sSinyouKouzaKubun = '' # 信用取引口座開設区分  0：未開設  1：開設
 self.sSakopKouzaKubun = '' # 先物ＯＰ口座開設区分  0：未開設  1：開設
 self.sMMFKouzaKubun = '' # ＭＭＦ口座開設区分  0：未開設  1：開設
 self.sTyukokufKouzaKubun = '' # 中国Ｆ口座開設区分  0：未開設  1：開設
 self.sKawaseKouzaKubun = '' # 為替保証金口座開設区分  0：未開設  1：開設
 self.sHikazeiKouzaKubun = '' # 非課税口座開設区分  0：未開設  1：開設  ※ＮＩＳＡ口座の開設有無を示す。
 self.sKinsyouhouMidokuFlg = '' # 金商法交付書面未読フラグ  1：未読（標準Ｗｅｂを起動し書面確認実行必須）  0：既読  ※未読の場合、ｅ支店・ＡＰＩは利用不可のため    仮想ＵＲＬは発行されず""を設定。  ※既読の場合、ｅ支店・ＡＰＩは利用可能となり    仮想ＵＲＬを発行し設定。  
 self.sUrlRequest = '' # 仮想URL（REQUEST)  業務機能    （REQUEST I/F）仮想URL
 self.sUrlMaster = '' # 仮想URL（MASTER)  マスタ機能  （REQUEST I/F）仮想URL
 self.sUrlPrice = '' # 仮想URL（PRICE)  時価情報機能（REQUEST I/F）仮想URL
 self.sUrlEvent = '' # 仮想URL（EVENT)  注文約定通知（EVENT I/F）仮想URL
 self.sUrlEventWebSocket = '' # 仮想URL（EVENT-WebSocket)  注文約定通知（EVENT I/F WebSocket版）仮想URL
 self.sUpdateInformWebDocument = '' # 交付書面更新予定日  標準Ｗｅｂの交付書面更新予定日決定後、該当日付を設定。  【注意】参照
 self.sUpdateInformAPISpecFunction = '' # ｅ支店・ＡＰＩリリース予定日  ｅ支店・ＡＰＩリリース予定日決定後、該当日付を設定。  【注意】参照


 


# 機能: システム時刻を"p_sd_date"の書式の文字列で返す。
# 返値: "p_sd_date"の書式の文字列
# 引数1: システム時刻
# 備考:  "p_sd_date"の書式：YYYY.MM.DD-hh:mm:ss.sss
def func_p_sd_date(int_systime):
 str_psddate = ''
 str_psddate = str_psddate + str(int_systime.year) 
 str_psddate = str_psddate + '.' + ('00' + str(int_systime.month))[-2:]
 str_psddate = str_psddate + '.' + ('00' + str(int_systime.day))[-2:]
 str_psddate = str_psddate + '-' + ('00' + str(int_systime.hour))[-2:]
 str_psddate = str_psddate + ':' + ('00' + str(int_systime.minute))[-2:]
 str_psddate = str_psddate + ':' + ('00' + str(int_systime.second))[-2:]
 str_psddate = str_psddate + '.' + (('000000' + str(int_systime.microsecond))[-6:])[:3]
 return str_psddate




# JSONの値の前後にダブルクオーテーションが無い場合付ける。
def func_check_json_dquat(str_value) :
 if len(str_value) == 0 :
 str_value = '""'
 
 if not str_value[:1] == '"' :
 str_value = '"' + str_value
 
 if not str_value[-1:] == '"' :
 str_value = str_value + '"'
 
 return str_value
 
 
# 受けたテキストの１文字目と最終文字の「"」を削除
# 引数：string
# 返り値：string
def func_strip_dquot(text):
 if len(text) > 0:
 if text[0:1] == '"' :
 text = text[1:]
 
 if len(text) > 0:
 if text[-1] == '\n':
 text = text[0:-1]
 
 if len(text) > 0:
 if text[-1:] == '"':
 text = text[0:-1]
 
 return text
 




# 機能: URLエンコード文字の変換
# 引数1: 文字列
# 返値: URLエンコード文字に変換した文字列
# 
# URLに「#」「+」「/」「:」「=」などの記号を利用した場合エラーとなる場合がある。
# APIへの入力文字列（特にパスワードで記号を利用している場合）で注意が必要。
#   '#' →   '%23'
#   '+' →   '%2B'
#   '/' →   '%2F'
#   ':' →   '%3A'
#   '=' →   '%3D'
def func_replace_urlecnode( str_input ):
 str_encode = ''
 str_replace = ''
 
 for i in range(len(str_input)):
 str_char = str_input[i:i+1]


 if str_char == ' ' :
 str_replace = '%20' #「 」 → 「%20」 半角空白
 elif str_char == '!' :
 str_replace = '%21' #「!」 → 「%21」
 elif str_char == '"' :
 str_replace = '%22' #「"」 → 「%22」
 elif str_char == '#' :
 str_replace = '%23' #「#」 → 「%23」
 elif str_char == '$' :
 str_replace = '%24' #「$」 → 「%24」
 elif str_char == '%' :
 str_replace = '%25' #「%」 → 「%25」
 elif str_char == '&' :
 str_replace = '%26' #「&」 → 「%26」
 elif str_char == "'" :
 str_replace = '%27' #「'」 → 「%27」
 elif str_char == '(' :
 str_replace = '%28' #「(」 → 「%28」
 elif str_char == ')' :
 str_replace = '%29' #「)」 → 「%29」
 elif str_char == '*' :
 str_replace = '%2A' #「*」 → 「%2A」
 elif str_char == '+' :
 str_replace = '%2B' #「+」 → 「%2B」
 elif str_char == ',' :
 str_replace = '%2C' #「,」 → 「%2C」
 elif str_char == '/' :
 str_replace = '%2F' #「/」 → 「%2F」
 elif str_char == ':' :
 str_replace = '%3A' #「:」 → 「%3A」
 elif str_char == ';' :
 str_replace = '%3B' #「;」 → 「%3B」
 elif str_char == '<' :
 str_replace = '%3C' #「<」 → 「%3C」
 elif str_char == '=' :
 str_replace = '%3D' #「=」 → 「%3D」
 elif str_char == '>' :
 str_replace = '%3E' #「>」 → 「%3E」
 elif str_char == '?' :
 str_replace = '%3F' #「?」 → 「%3F」
 elif str_char == '@' :
 str_replace = '%40' #「@」 → 「%40」
 elif str_char == '[' :
 str_replace = '%5B' #「[」 → 「%5B」
 elif str_char == ']' :
 str_replace = '%5D' #「]」 → 「%5D」
 elif str_char == '^' :
 str_replace = '%5E' #「^」 → 「%5E」
 elif str_char == '`' :
 str_replace = '%60' #「`」 → 「%60」
 elif str_char == '{' :
 str_replace = '%7B' #「{」 → 「%7B」
 elif str_char == '|' :
 str_replace = '%7C' #「|」 → 「%7C」
 elif str_char == '}' :
 str_replace = '%7D' #「}」 → 「%7D」
 elif str_char == '~' :
 str_replace = '%7E' #「~」 → 「%7E」
 else :
 str_replace = str_char
 str_encode = str_encode + str_replace 
 return str_encode




# 機能： ファイルから文字情報を読み込み、その文字列を返す。
# 戻り値： 文字列
# 第１引数： ファイル名
# 備考： json形式のファイルを想定。
def func_read_from_file(str_fname):
 str_read = ''
 try:
 with open(str_fname, 'r', encoding = 'utf_8') as fin:
 while True:
 line = fin.readline()
 if not len(line):
 break
 str_read = str_read + line
 return str_read
 except IOError as e:
 print('ファイルを読み込めません!!! ファイル名：',str_fname)
 print(type(e))




# 機能: ファイルに書き込む
# 引数1: 出力ファイル名
# 引数2: 出力するデータ
# 備考:
def func_write_to_file(str_fname_output, str_data):
 try:
 with open(str_fname_output, 'w', encoding = 'utf-8') as fout:
 fout.write(str_data)
 except IOError as e:
 print('ファイルに書き込めません!!!  ファイル名：',str_fname_output)
 print(type(e))




# 機能: class_req型データをjson形式の文字列に変換する。
# 返値: json形式の文字
# 第１引数： class_req型データ
def func_make_json_format(work_class_req):
 work_key = ''
 work_value = ''
 str_json_data = '{\n\t'
 for i in range(len(work_class_req)) :
 work_key = func_strip_dquot(work_class_req[i].str_key)
 if len(work_key) > 0:
 if work_key[:1] == 'a' :
 work_value = work_class_req[i].str_value
 str_json_data = str_json_data + work_class_req[i].str_key \
 + ':' + func_strip_dquot(work_value) \
 + ',\n\t'
 else :
 work_value = func_check_json_dquat(work_class_req[i].str_value)
 str_json_data = str_json_data + func_check_json_dquat(work_class_req[i].str_key) \
 + ':' + work_value \
 + ',\n\t'
 str_json_data = str_json_data[:-3] + '\n}'
 return str_json_data




# 機能： API問合せ文字列を作成し返す。
# 戻り値： api問合せのurl文字列
# 第１引数： ログインは、Trueをセット。それ以外はFalseをセット。
# 第2引数： ログインは、APIのurlをセット。それ以外はログインで返された仮想url（'sUrlRequest'等）の値をセット。
# 第３引数： 要求項目のデータセット。クラスの配列として受取る。
def func_make_url_request(auth_flg, \
 url_target, \
 work_class_req) :
 str_url = url_target
 if auth_flg == True :   # ログインの場合
 str_url = str_url + 'auth/'
 str_url = str_url + '?'
 str_url = str_url + func_make_json_format(work_class_req)
 return str_url




# 機能: API問合せ。通常のrequest,price用。
# 返値: API応答（辞書型）
# 第１引数： URL文字列。
# 備考: APIに接続し、requestの文字列を送信し、応答データを辞書型で返す。
#       master取得は専用の func_api_req_muster を利用する。
def func_api_req(str_url): 
 print('送信文字列＝')
 print(str_url)  # 送信する文字列


 # APIに接続
 http = urllib3.PoolManager()
 req = http.request('GET', str_url)
 print("req.status= ", req.status )


 # 取得したデータを、json.loadsを利用できるようにstr型に変換する。日本語はshift-jis。
 bytes_reqdata = req.data
 str_shiftjis = bytes_reqdata.decode("shift-jis", errors="ignore")


 print('返信文字列＝')
 print(str_shiftjis)


 # JSON形式の文字列を辞書型で取り出す
 json_req = json.loads(str_shiftjis)


 return json_req




# 機能： アカウント情報をファイルから取得する
# 引数1: 口座情報を保存したファイル名
# 引数2: 口座情報（class_def_account_property型）データ
def func_get_acconut_info(fname, class_account_property):
 str_account_info = func_read_from_file(fname)
 # JSON形式の文字列を辞書型で取り出す
 json_account_info = json.loads(str_account_info)


 class_account_property.sUserId = json_account_info.get('sUserId')
 class_account_property.sPassword = json_account_info.get('sPassword')
 class_account_property.sSecondPassword = json_account_info.get('sSecondPassword')
 class_account_property.sUrl = json_account_info.get('sUrl')


 # 返り値の表示形式指定
 class_account_property.sJsonOfmt = json_account_info.get('sJsonOfmt')
 # "5"は "1"（1ビット目ON）と”4”（3ビット目ON）の指定となり
 # ブラウザで見や易い形式」且つ「引数項目名称」で応答を返す値指定




# 機能： ログイン情報をファイルから取得する
# 引数1: ログイン情報を保存したファイル名（fname_login_response = "e_api_login_response.txt"）
# 引数2: ログインデータ型（class_def_login_property型）
def func_get_login_info(str_fname, class_login_property):
 str_login_respons = func_read_from_file(str_fname)
 dic_login_respons = json.loads(str_login_respons)


 class_login_property.sResultCode = dic_login_respons.get('sResultCode')                 # 結果コード
 class_login_property.sResultText = dic_login_respons.get('sResultText')                 # 結果テキスト
 class_login_property.sZyoutoekiKazeiC = dic_login_respons.get('sZyoutoekiKazeiC')       # 譲渡益課税区分  1：特定  3：一般  5：NISA
 class_login_property.sSecondPasswordOmit = dic_login_respons.get('sSecondPasswordOmit')     # 暗証番号省略有無Ｃ
 class_login_property.sLastLoginDate = dic_login_respons.get('sLastLoginDate')               # 最終ログイン日時
 class_login_property.sSogoKouzaKubun = dic_login_respons.get('sSogoKouzaKubun')             # 総合口座開設区分  0：未開設  1：開設
 class_login_property.sHogoAdukariKouzaKubun = dic_login_respons.get('sHogoAdukariKouzaKubun')       # 保護預り口座開設区分  0：未開設  1：開設
 class_login_property.sFurikaeKouzaKubun = dic_login_respons.get('sFurikaeKouzaKubun')               # 振替決済口座開設区分  0：未開設  1：開設
 class_login_property.sGaikokuKouzaKubun = dic_login_respons.get('sGaikokuKouzaKubun')               # 外国口座開設区分  0：未開設  1：開設
 class_login_property.sMRFKouzaKubun = dic_login_respons.get('sMRFKouzaKubun')                       # ＭＲＦ口座開設区分  0：未開設  1：開設
 class_login_property.sTokuteiKouzaKubunGenbutu = dic_login_respons.get('sTokuteiKouzaKubunGenbutu') # 特定口座区分現物  0：一般  1：特定源泉徴収なし  2：特定源泉徴収あり
 class_login_property.sTokuteiKouzaKubunSinyou = dic_login_respons.get('sTokuteiKouzaKubunSinyou')   # 特定口座区分信用  0：一般  1：特定源泉徴収なし  2：特定源泉徴収あり
 class_login_property.sTokuteiKouzaKubunTousin = dic_login_respons.get('sTokuteiKouzaKubunTousin')   # 特定口座区分投信  0：一般  1：特定源泉徴収なし  2：特定源泉徴収あり
 class_login_property.sTokuteiHaitouKouzaKubun = dic_login_respons.get('sTokuteiHaitouKouzaKubun')   # 配当特定口座区分  0：未開設  1：開設
 class_login_property.sTokuteiKanriKouzaKubun = dic_login_respons.get('sTokuteiKanriKouzaKubun')     # 特定管理口座開設区分  0：未開設  1：開設
 class_login_property.sSinyouKouzaKubun = dic_login_respons.get('sSinyouKouzaKubun')         # 信用取引口座開設区分  0：未開設  1：開設
 class_login_property.sSinyouKouzaKubun = dic_login_respons.get('sSinyouKouzaKubun')         # 信用取引口座開設区分  0：未開設  1：開設
 class_login_property.sSakopKouzaKubun = dic_login_respons.get('sSakopKouzaKubun')           # 先物ＯＰ口座開設区分  0：未開設  1：開設
 class_login_property.sMMFKouzaKubun = dic_login_respons.get('sMMFKouzaKubun')               # ＭＭＦ口座開設区分  0：未開設  1：開設
 class_login_property.sTyukokufKouzaKubun = dic_login_respons.get('sTyukokufKouzaKubun')     # 中国Ｆ口座開設区分  0：未開設  1：開設
 class_login_property.sKawaseKouzaKubun = dic_login_respons.get('sKawaseKouzaKubun')         # 為替保証金口座開設区分  0：未開設  1：開設
 class_login_property.sHikazeiKouzaKubun = dic_login_respons.get('sHikazeiKouzaKubun')       # 非課税口座開設区分  0：未開設  1：開設  ※ＮＩＳＡ口座の開設有無を示す。
 class_login_property.sKinsyouhouMidokuFlg = dic_login_respons.get('sKinsyouhouMidokuFlg')   # 金商法交付書面未読フラグ  1：未読（標準Ｗｅｂを起動し書面確認実行必須）  0：既読  ※未読の場合、ｅ支店・ＡＰＩは利用不可のため    仮想ＵＲＬは発行されず""を設定。  ※既読の場合、ｅ支店・ＡＰＩは利用可能となり    仮想ＵＲＬを発行し設定。  
 class_login_property.sUrlRequest = dic_login_respons.get('sUrlRequest')     # 仮想URL（REQUEST)  業務機能    （REQUEST I/F）仮想URL
 class_login_property.sUrlMaster = dic_login_respons.get('sUrlMaster')       # 仮想URL（MASTER)  マスタ機能  （REQUEST I/F）仮想URL
 class_login_property.sUrlPrice = dic_login_respons.get('sUrlPrice')         # 仮想URL（PRICE)  時価情報機能（REQUEST I/F）仮想URL
 class_login_property.sUrlEvent = dic_login_respons.get('sUrlEvent')         # 仮想URL（EVENT)  注文約定通知（EVENT I/F）仮想URL
 class_login_property.sUrlEventWebSocket = dic_login_respons.get('sUrlEventWebSocket')    # 仮想URL（EVENT-WebSocket)  注文約定通知（EVENT I/F WebSocket版）仮想URL
 class_login_property.sUpdateInformWebDocument = dic_login_respons.get('sUpdateInformWebDocument')    # 交付書面更新予定日  標準Ｗｅｂの交付書面更新予定日決定後、該当日付を設定。  【注意】参照
 class_login_property.sUpdateInformAPISpecFunction = dic_login_respons.get('sUpdateInformAPISpecFunction')    # ｅ支店・ＡＰＩリリース予定日  ｅ支店・ＡＰＩリリース予定日決定後、該当日付を設定。  【注意】参照
 


# 機能： p_noをファイルから取得する
# 引数1: p_noを保存したファイル名（fname_info_p_no = "e_api_info_p_no.txt"）
# 引数2: login情報（class_def_login_property型）データ
def func_get_p_no(fname, class_login_property):
 str_p_no_info = func_read_from_file(fname)
 # JSON形式の文字列を辞書型で取り出す
 json_p_no_info = json.loads(str_p_no_info)
 class_login_property.p_no = int(json_p_no_info.get('p_no'))
 
 
# 機能: p_noを保存するためのjson形式のテキストデータを作成します。
# 引数1: p_noを保存するファイル名（fname_info_p_no = "e_api_info_p_no.txt"）
# 引数2: 保存するp_no
# 備考:
def func_save_p_no(str_fname_output, int_p_no):
 # "p_no"を保存する。
 str_info_p_no = '{\n'
 str_info_p_no = str_info_p_no + '\t' + '"p_no":"' + str(int_p_no) + '"\n'
 str_info_p_no = str_info_p_no + '}\n'
 func_write_to_file(str_fname_output, str_info_p_no)
 print('現在の"p_no"を保存しました。 p_no =', int_p_no)            
 print('ファイル名:', str_fname_output)


#--- 以上 共通コード -------------------------------------------------










# 資料
# 'sCLMID':'CLMMfdsGetMasterData' の利用方法
# API専用ページ
# ５．マニュアル 
# １．共通説明
# （３）ブラウザからの利用方法
# 「ｅ支店・ＡＰＩ、ブラウザからの利用方法」
# 
# 「マスタ・時価」シート・・・・マスタ情報問合取得、 時価情報問合取得
# ２－２．各Ｉ／Ｆ説明																				
# （１）マスタ情報問合取得																			
# 【要求】																				
##No	項目			設定値													
##1	sCLMID			CLMMfdsGetMasterData													
##2	sTargetCLMID		取得したいマスタデータ識別子をカンマで区切りで羅列、未指定「””」時は全マスタデータ。													
#        マスタデータ識別子：			※１								
#        CLMIssueMstKabu    	銘柄マスタ_株					
#        CLMIssueSizyouMstKabu	銘柄市場マスタ_株					
#        CLMIssueMstSak	    	銘柄マスタ_先物					
#        CLMIssueMstOp	    	銘柄マスタ_ＯＰ					
#        CLMIssueMstOther   	日経平均等指数、為替、その他上記外の銘柄コードと名称					
#        CLMOrderErrReason  	取引所エラー理由コード					※３
#        CLMDateZyouhou	    	日付情報					
##3	sTargetColumn	    各マスタデータで取得したい項目名をカンマ区切りで羅列													
#       例：													
#        sIssueCode	銘柄コード					
#        sIssueName	銘柄名称					
#        sErrReasonCode	取引所エラー理由
#        sErrReasonText	取引所エラーコード
##※１、（※２を除く）マスタデータ項目仕様等については別紙「立花証券・ｅ支店・ＡＰＩ、REQUEST I/F、マスタデータ利用方法」参照。																				
##※２、マスタデータ※１には無い株価ボード、ｅ支店・ＡＰＩ独自で取り扱う時価情報配信対象の銘柄コード、銘柄名称（それ以外の項目は無い）。																				
#
# マスターデータの内容の解説は、マニュアル「立花証券・ｅ支店・ＡＰＩ、REQUEST I/F、マスタデータ利用方法」参照。




# 機能: 銘柄マスタ_株（CLMIssueMstKabu）用 sTargetColumn作成 
# 引数: 無し
# 返値: 取得フィールド名 string型
def func_make_column_CLMIssueMstKabu():
 str_column = 'sIssueCode'
 str_column = str_column + ',' + 'sIssueName'
 str_column = str_column + ',' + 'sIssueNameRyaku'
 str_column = str_column + ',' + 'sIssueNameKana'
 str_column = str_column + ',' + 'sIssueNameEizi'
 str_column = str_column + ',' + 'sTokuteiF'
 str_column = str_column + ',' + 'sHikazeiC'
 str_column = str_column + ',' + 'sZyouzyouHakkouKabusu'
 str_column = str_column + ',' + 'sKenriotiFlag'
 str_column = str_column + ',' + 'sKenritukiSaisyuDay'
 str_column = str_column + ',' + 'sZyouzyouNyusatuC'
 str_column = str_column + ',' + 'sNyusatuKaizyoDay'
 str_column = str_column + ',' + 'sNyusatuDay'
 str_column = str_column + ',' + 'sBaibaiTani'
 str_column = str_column + ',' + 'sBaibaiTaniYoku'
 str_column = str_column + ',' + 'sBaibaiTeisiC'
 str_column = str_column + ',' + 'sHakkouKaisiDay'
 str_column = str_column + ',' + 'sHakkouSaisyuDay'
 str_column = str_column + ',' + 'sKessanC'
 str_column = str_column + ',' + 'sKessanDay'
 str_column = str_column + ',' + 'sZyouzyouOutouDay'
 str_column = str_column + ',' + 'sNiruiKizituC'
 str_column = str_column + ',' + 'sOogutiKabusu'
 str_column = str_column + ',' + 'sOogutiKingaku'
 str_column = str_column + ',' + 'sBadenpyouOutputYNC'
 str_column = str_column + ',' + 'sHosyoukinDaiyouKakeme'
 str_column = str_column + ',' + 'sDaiyouHyoukaTanka'
 str_column = str_column + ',' + 'sKikoSankaC'
 str_column = str_column + ',' + 'sKarikessaiC'
 str_column = str_column + ',' + 'sYusenSizyou' # 優先市場
 str_column = str_column + ',' + 'sMukigenC'
 str_column = str_column + ',' + 'sGyousyuCode'
 str_column = str_column + ',' + 'sGyousyuName'
 str_column = str_column + ',' + 'sSorC'
 str_column = str_column + ',' + 'sCreateDate'
 str_column = str_column + ',' + 'sUpdateDate'
 str_column = str_column + ',' + 'sUpdateNumber'
 return str_column




# 機能: 銘柄マスタ_株（CLMIssueMstKabu）用 列名を漢字に変換 
# 引数: 列名
# 返値: 漢字名 string型
def func_column_kanji_CLMIssueMstKabu(str_column_name):
 if str_column_name == "sIssueCode" : str_name_kanji = "銘柄コード"
 elif str_column_name == "sIssueName" : str_name_kanji = "銘柄名"
 elif str_column_name == "sIssueNameRyaku" : str_name_kanji = "銘柄名略称"
 elif str_column_name == "sIssueNameKana" : str_name_kanji = "銘柄名（カナ）"
 elif str_column_name == "sIssueNameEizi" : str_name_kanji = "銘柄名（英語表記）"
 elif str_column_name == "sTokuteiF" : str_name_kanji = "特定口座対象Ｃ"
 elif str_column_name == "sHikazeiC" : str_name_kanji = "非課税口座受付可否"
 elif str_column_name == "sZyouzyouHakkouKabusu" : str_name_kanji = "上場発行株数"
 elif str_column_name == "sKenriotiFlag" : str_name_kanji = "権利落ちフラグ"
 elif str_column_name == "sKenritukiSaisyuDay" : str_name_kanji = "権利付最終日"
 elif str_column_name == "sZyouzyouNyusatuC" : str_name_kanji = "上場・入札Ｃ"
 elif str_column_name == "sNyusatuKaizyoDay" : str_name_kanji = "入札解除日"
 elif str_column_name == "sNyusatuDay" : str_name_kanji = "入札日"
 elif str_column_name == "sBaibaiTani" : str_name_kanji = "売買単位"
 elif str_column_name == "sBaibaiTaniYoku" : str_name_kanji = "売買単位(翌営業日)"
 elif str_column_name == "sBaibaiTeisiC" : str_name_kanji = "売買停止Ｃ"
 elif str_column_name == "sHakkouKaisiDay" : str_name_kanji = "発行開始日"
 elif str_column_name == "sHakkouSaisyuDay" : str_name_kanji = "発行最終日"
 elif str_column_name == "sKessanC" : str_name_kanji = "決算Ｃ"
 elif str_column_name == "sKessanDay" : str_name_kanji = "決算日"
 elif str_column_name == "sZyouzyouOutouDay" : str_name_kanji = "上場応答日"
 elif str_column_name == "sNiruiKizituC" : str_name_kanji = "二類期日Ｃ"
 elif str_column_name == "sOogutiKabusu" : str_name_kanji = "大口株数"
 elif str_column_name == "sOogutiKingaku" : str_name_kanji = "大口金額"
 elif str_column_name == "sBadenpyouOutputYNC" : str_name_kanji = "場伝票出力有無Ｃ"
 elif str_column_name == "sHosyoukinDaiyouKakeme" : str_name_kanji = "保証金代用掛目"
 elif str_column_name == "sDaiyouHyoukaTanka" : str_name_kanji = "代用証券評価単価"
 elif str_column_name == "sKikoSankaC" : str_name_kanji = "機構参加Ｃ"
 elif str_column_name == "sKarikessaiC" : str_name_kanji = "仮決済Ｃ"
 elif str_column_name == "sYusenSizyou" : str_name_kanji = "優先市場"
 elif str_column_name == "sMukigenC" : str_name_kanji = "無期限対象Ｃ"
 elif str_column_name == "sGyousyuCode" : str_name_kanji = "業種コード"
 elif str_column_name == "sGyousyuName" : str_name_kanji = "業種コード名"
 elif str_column_name == "sSorC" : str_name_kanji = "ＳＯＲ対象銘柄Ｃ"
 elif str_column_name == "sCreateDate" : str_name_kanji = "新規作成日時"
 elif str_column_name == "sUpdateDate" : str_name_kanji = "最終更新日時"
 elif str_column_name == "sUpdateNumber" : str_name_kanji = "最終更新通番"
 else :
 str_name_kanji = str_column_name
 return str_name_kanji




def func_make_column_CLMIssueSizyouMstKabu():
 str_column = 'sIssueCode'
 str_column = str_column + ',' + 'sZyouzyouSizyou'
 str_column = str_column + ',' + 'sSystemC'
 str_column = str_column + ',' + 'sNehabaMin'
 str_column = str_column + ',' + 'sNehabaMax'
 str_column = str_column + ',' + 'sIssueKubunC'
 str_column = str_column + ',' + 'sNehabaSizyouC'
 str_column = str_column + ',' + 'sSinyouC'
 str_column = str_column + ',' + 'sSinkiZyouzyouDay'
 str_column = str_column + ',' + 'sNehabaKigenDay'
 str_column = str_column + ',' + 'sNehabaKiseiC'
 str_column = str_column + ',' + 'sNehabaKiseiTi'
 str_column = str_column + ',' + 'sNehabaCheckKahiC'
 str_column = str_column + ',' + 'sIssueBubetuC'
 str_column = str_column + ',' + 'sZenzituOwarine'
 str_column = str_column + ',' + 'sNehabaSansyutuSizyouC'
 str_column = str_column + ',' + 'sIssueKisei1C'
 str_column = str_column + ',' + 'sIssueKisei2C'
 str_column = str_column + ',' + 'sZyouzyouKubun'
 str_column = str_column + ',' + 'sZyouzyouHaisiDay'
 str_column = str_column + ',' + 'sSizyoubetuBaibaiTani'
 str_column = str_column + ',' + 'sSizyoubetuBaibaiTaniYoku'
 str_column = str_column + ',' + 'sYobineTaniNumber'
 str_column = str_column + ',' + 'sYobineTaniNumberYoku'
 str_column = str_column + ',' + 'sZyouhouSource'
 str_column = str_column + ',' + 'sZyouhouCode'
 str_column = str_column + ',' + 'sKouboPrice'
 str_column = str_column + ',' + 'sCreateDate'
 str_column = str_column + ',' + 'sUpdateDate'
 str_column = str_column + ',' + 'sUpdateNumber'
 return str_column




# 機能: 銘柄市場マスタ_株（CLMIssueSizyouMstKabu）用 列名を漢字に変換 
# 引数: 列名
# 返値: 漢字名 string型
def func_column_kanji_CLMIssueSizyouMstKabu(str_column_name):
 if str_column_name == "sIssueCode" : str_name_kanji = "銘柄コード"
 elif str_column_name == "sZyouzyouSizyou" : str_name_kanji = "上場市場"
 elif str_column_name == "sSystemC" : str_name_kanji = "システムＣ"
 elif str_column_name == "sNehabaMin" : str_name_kanji = "値幅下限"
 elif str_column_name == "sNehabaMax" : str_name_kanji = "値幅上限"
 elif str_column_name == "sIssueKubunC" : str_name_kanji = "銘柄区分Ｃ"
 elif str_column_name == "sNehabaSizyouC" : str_name_kanji = "値幅市場Ｃ"
 elif str_column_name == "sSinyouC" : str_name_kanji = "信用Ｃ"
 elif str_column_name == "sSinkiZyouzyouDay" : str_name_kanji = "新規上場日"
 elif str_column_name == "sNehabaKigenDay" : str_name_kanji = "値幅期限日"
 elif str_column_name == "sNehabaKiseiC" : str_name_kanji = "値幅規制Ｃ"
 elif str_column_name == "sNehabaKiseiTi" : str_name_kanji = "値幅規制値"
 elif str_column_name == "sNehabaCheckKahiC" : str_name_kanji = "値幅チェック可否Ｃ"
 elif str_column_name == "sIssueBubetuC" : str_name_kanji = "銘柄部別Ｃ"
 elif str_column_name == "sZenzituOwarine" : str_name_kanji = "前日終値"
 elif str_column_name == "sNehabaSansyutuSizyouC" : str_name_kanji = "値幅算出市場Ｃ"
 elif str_column_name == "sIssueKisei1C" : str_name_kanji = "銘柄規制１Ｃ"
 elif str_column_name == "sIssueKisei2C" : str_name_kanji = "銘柄規制２Ｃ"
 elif str_column_name == "sZyouzyouKubun" : str_name_kanji = "上場区分"
 elif str_column_name == "sZyouzyouHaisiDay" : str_name_kanji = "上場廃止日"
 elif str_column_name == "sSizyoubetuBaibaiTani" : str_name_kanji = "市場別売買単位"
 elif str_column_name == "sSizyoubetuBaibaiTaniYoku" : str_name_kanji = "市場別売買単位(翌営業日)"
 elif str_column_name == "sYobineTaniNumber" : str_name_kanji = "呼値の単位番号"
 elif str_column_name == "sYobineTaniNumberYoku" : str_name_kanji = "呼値の単位番号(翌営業日)"
 elif str_column_name == "sZyouhouSource" : str_name_kanji = "情報系ソース"
 elif str_column_name == "sZyouhouCode" : str_name_kanji = "情報系コード"
 elif str_column_name == "sKouboPrice" : str_name_kanji = "公募価格"
 elif str_column_name == "sCreateDate" : str_name_kanji = "新規作成日時"
 elif str_column_name == "sUpdateDate" : str_name_kanji = "最終更新日時"
 elif str_column_name == "sUpdateNumber" : str_name_kanji = "最終更新通番"
 else :
 str_name_kanji = str_column_name
 return str_name_kanji






def func_make_column_CLMIssueMstSak():
 str_column = 'sIssueCode'
 str_column = str_column + ',' + 'sIssueName'
 str_column = str_column + ',' + 'sIssueNameEizi'
 str_column = str_column + ',' + 'sSakOpSyouhin'
 str_column = str_column + ',' + 'sGensisanKubun'
 str_column = str_column + ',' + 'sGensisanCode'
 str_column = str_column + ',' + 'sGengetu'
 str_column = str_column + ',' + 'sZyouzyouSizyou'
 str_column = str_column + ',' + 'sTorihikiStartDay'
 str_column = str_column + ',' + 'sLastBaibaiDay'
 str_column = str_column + ',' + 'sTaniSuryou'
 str_column = str_column + ',' + 'sYobineTaniNumber'
 str_column = str_column + ',' + 'sZyouhouSource'
 str_column = str_column + ',' + 'sZyouhouCode'
 str_column = str_column + ',' + 'sNehabaMin'
 str_column = str_column + ',' + 'sNehabaMax'
 str_column = str_column + ',' + 'sIssueKisei1C'
 str_column = str_column + ',' + 'sBaibaiTeisiC'
 str_column = str_column + ',' + 'sZenzituOwarine'
 str_column = str_column + ',' + 'sBaDenpyouOutputUmuC'
 str_column = str_column + ',' + 'sCreateDate'
 str_column = str_column + ',' + 'sUpdateDate'
 str_column = str_column + ',' + 'sUpdateNumber'
 return str_column




# 機能: 先物（CLMIssueMstSak）用 列名を漢字に変換 
# 引数: 列名
# 返値: 漢字名 string型
def func_column_kanji_CLMIssueMstSak(str_column_name):
 if str_column_name == "sIssueCode" : str_name_kanji = "銘柄コード"
 elif str_column_name == "sIssueName" : str_name_kanji = "銘柄名"
 elif str_column_name == "sIssueNameEizi" : str_name_kanji = "銘柄名（英語表記）"
 elif str_column_name == "sSakOpSyouhin" : str_name_kanji = "先物OP商品"
 elif str_column_name == "sGensisanKubun" : str_name_kanji = "原資産区分"
 elif str_column_name == "sGensisanCode" : str_name_kanji = "原資産コード"
 elif str_column_name == "sGengetu" : str_name_kanji = "限月"
 elif str_column_name == "sZyouzyouSizyou" : str_name_kanji = "上場市場"
 elif str_column_name == "sTorihikiStartDay" : str_name_kanji = "取引開始日"
 elif str_column_name == "sLastBaibaiDay" : str_name_kanji = "最終売買日"
 elif str_column_name == "sTaniSuryou" : str_name_kanji = "単位数量"
 elif str_column_name == "sYobineTaniNumber" : str_name_kanji = "呼値の単位番号"
 elif str_column_name == "sZyouhouSource" : str_name_kanji = "情報系ソース"
 elif str_column_name == "sZyouhouCode" : str_name_kanji = "情報系コード"
 elif str_column_name == "sNehabaMin" : str_name_kanji = "値幅下限"
 elif str_column_name == "sNehabaMax" : str_name_kanji = "値幅上限"
 elif str_column_name == "sIssueKisei1C" : str_name_kanji = "銘柄規制１Ｃ"
 elif str_column_name == "sBaibaiTeisiC" : str_name_kanji = "売買停止Ｃ"
 elif str_column_name == "sZenzituOwarine" : str_name_kanji = "前日終値"
 elif str_column_name == "sBaDenpyouOutputUmuC" : str_name_kanji = "場伝票出力有無Ｃ"
 elif str_column_name == "sCreateDate" : str_name_kanji = "新規作成日時"
 elif str_column_name == "sUpdateDate" : str_name_kanji = "最終更新日時"
 elif str_column_name == "sUpdateNumber" : str_name_kanji = "最終更新通番"
 else :
 str_name_kanji = str_column_name
 return str_name_kanji








def func_make_column_CLMIssueMstOp():
 str_column = 'sIssueCode' # 銘柄コード
 str_column = str_column + ',' + 'sIssueName' # 銘柄名
 str_column = str_column + ',' + 'sIssueNameEizi'
 str_column = str_column + ',' + 'sSakOpSyouhin'
 str_column = str_column + ',' + 'sGensisanKubun' # 原資産区分
 str_column = str_column + ',' + 'sGensisanCode' # 原資産コード
 str_column = str_column + ',' + 'sGengetu' # 限月
 str_column = str_column + ',' + 'sZyouzyouSizyou' # 上場市場
 str_column = str_column + ',' + 'sKousiPrice' # 行使価格
 str_column = str_column + ',' + 'sPutCall' # プット・コール
 str_column = str_column + ',' + 'sTorihikiStartDay' # 取引開始日
 str_column = str_column + ',' + 'sLastBaibaiDay' # 直近売買日
 str_column = str_column + ',' + 'sKenrikousiLastDay' # 直近権利行使日
 str_column = str_column + ',' + 'sTaniSuryou' # 単位数量
 str_column = str_column + ',' + 'sYobineTaniNumber' # 呼値単位数
 str_column = str_column + ',' + 'sZyouhouSource' # 情報ソース
 str_column = str_column + ',' + 'sZyouhouCode' # 情報コード
 str_column = str_column + ',' + 'sNehabaMin' # 値幅_最小
 str_column = str_column + ',' + 'sNehabaMax' # 値幅_最大
 str_column = str_column + ',' + 'sIssueKisei1C'
 str_column = str_column + ',' + 'sZenzituOwarine' # 前日終値
 str_column = str_column + ',' + 'sZenzituRironPrice' # 前日理論価格
 str_column = str_column + ',' + 'sBaDenpyouOutputUmuC' # 場伝票出力有無
 str_column = str_column + ',' + 'sCreateDate' # 作成日
 str_column = str_column + ',' + 'sUpdateDate' # 更新日
 str_column = str_column + ',' + 'sUpdateNumber' # 更新番号
 str_column = str_column + ',' + 'sATMFlag'
 return str_column


# 機能: OP（CLMIssueMstOp）用 列名を漢字に変換 
# 引数: 列名
# 返値: 漢字名 string型
def func_column_kanji_CLMIssueMstOp(str_column_name):
 if str_column_name == "sIssueCode" : str_name_kanji = "銘柄コード"
 elif str_column_name == "sIssueName" : str_name_kanji = "銘柄名"
 elif str_column_name == "sIssueNameEizi" : str_name_kanji = "銘柄名（英語表記）"
 elif str_column_name == "sSakOpSyouhin" : str_name_kanji = "先物OP商品"
 elif str_column_name == "sGensisanKubun" : str_name_kanji = "原資産区分"
 elif str_column_name == "sGensisanCode" : str_name_kanji = "原資産コード"
 elif str_column_name == "sGengetu" : str_name_kanji = "限月"
 elif str_column_name == "sZyouzyouSizyou" : str_name_kanji = "上場市場"
 elif str_column_name == "sKousiPrice" : str_name_kanji = "行使価格"
 elif str_column_name == "sPutCall" : str_name_kanji = "プット・コール"
 elif str_column_name == "sTorihikiStartDay" : str_name_kanji = "取引開始日"
 elif str_column_name == "sLastBaibaiDay" : str_name_kanji = "最終売買日"
 elif str_column_name == "sKenrikousiLastDay" : str_name_kanji = "権利行使最終日"
 elif str_column_name == "sTaniSuryou" : str_name_kanji = "単位数量"
 elif str_column_name == "sYobineTaniNumber" : str_name_kanji = "呼値の単位番号"
 elif str_column_name == "sZyouhouSource" : str_name_kanji = "情報系ソース"
 elif str_column_name == "sZyouhouCode" : str_name_kanji = "情報系コード"
 elif str_column_name == "sNehabaMin" : str_name_kanji = "値幅下限"
 elif str_column_name == "sNehabaMax" : str_name_kanji = "値幅上限"
 elif str_column_name == "sIssueKisei1C" : str_name_kanji = "銘柄規制１Ｃ"
 elif str_column_name == "sZenzituOwarine" : str_name_kanji = "前日終値"
 elif str_column_name == "sZenzituRironPrice" : str_name_kanji = "前日理論価格"
 elif str_column_name == "sBaDenpyouOutputUmuC" : str_name_kanji = "場伝票出力有無Ｃ"
 elif str_column_name == "sCreateDate" : str_name_kanji = "新規作成日時"
 elif str_column_name == "sUpdateDate" : str_name_kanji = "最終更新日時"
 elif str_column_name == "sUpdateNumber" : str_name_kanji = "最終更新通番"
 elif str_column_name == "sATMFlag" : str_name_kanji = "アット・ザ・マネーF"
 else :
 str_name_kanji = str_column_name
 return str_name_kanji








# 指数・為替
def func_make_column_CLMIssueMstOther():
 str_column = 'sIssueCode'
 str_column = str_column + ',' + 'sIssueName'
 return str_column




# 機能: 指数・為替（CLMIssueMstOther）用 列名を漢字に変換 
# 引数: 列名
# 返値: 漢字名 string型
def func_column_kanji_CLMIssueMstOther(str_column_name):
 if str_column_name == "sIssueCode" : str_name_kanji = "銘柄コード"
 elif str_column_name == "sIssueName" : str_name_kanji = "銘柄名"
 else :
 str_name_kanji = str_column_name
 return str_name_kanji








def func_make_column_CLMDaiyouKakeme():
 str_column = 'sSystemKouzaKubun'
 str_column = str_column + ',' + 'sIssueCode'
 str_column = str_column + ',' + 'sTekiyouDay'
 str_column = str_column + ',' + 'sHosyokinDaiyoKakeme'
 str_column = str_column + ',' + 'sDeleteDay'
 str_column = str_column + ',' + 'sCreateDate'
 str_column = str_column + ',' + 'sUpdateNumber'
 str_column = str_column + ',' + 'sUpdateDate'
 return str_column


# 機能: 代用掛目（CLMDaiyouKakeme）用 列名を漢字に変換 
# 引数: 列名
# 返値: 漢字名 string型
def func_column_kanji_CLMDaiyouKakeme(str_column_name):
 if str_column_name == "sSystemKouzaKubun" : str_name_kanji = "システム口座区分"
 elif str_column_name == "sIssueCode" : str_name_kanji = "銘柄コード"
 elif str_column_name == "sTekiyouDay" : str_name_kanji = "適用日"
 elif str_column_name == "sHosyokinDaiyoKakeme" : str_name_kanji = "保証金代用掛目"
 elif str_column_name == "sDeleteDay" : str_name_kanji = "削除日"
 elif str_column_name == "sCreateDate" : str_name_kanji = "作成日"
 elif str_column_name == "sUpdateNumber" : str_name_kanji = "更新番号"
 elif str_column_name == "sUpdateDate" : str_name_kanji = "更新日"
 else :
 str_name_kanji = str_column_name
 return str_name_kanji








def func_make_column_CLMHosyoukinMst():
 str_column = 'sSystemKouzaKubun'
 str_column = str_column + ',' + 'sIssueCode'
 str_column = str_column + ',' + 'sZyouzyouSizyou'
 str_column = str_column + ',' + 'sHenkouDay'
 str_column = str_column + ',' + 'sDaiyoHosyokinRitu'
 str_column = str_column + ',' + 'sGenkinHosyokinRitu'
 str_column = str_column + ',' + 'sCreateDate'
 str_column = str_column + ',' + 'sUpdateNumber'
 str_column = str_column + ',' + 'sUpdateDate'
 return str_column


# 機能: 保証金（CLMHosyoukinMst）用 列名を漢字に変換 
# 引数: 列名
# 返値: 漢字名 string型
def func_column_kanji_CLMHosyoukinMst(str_column_name):
 if str_column_name == "sSystemKouzaKubun" : str_name_kanji = "システム口座区分"
 elif str_column_name == "sIssueCode" : str_name_kanji = "銘柄コード"
 elif str_column_name == "sZyouzyouSizyou" : str_name_kanji = "上場市場"
 elif str_column_name == "sHenkouDay" : str_name_kanji = "変更日"
 elif str_column_name == "sDaiyoHosyokinRitu" : str_name_kanji = "代用保証金率"
 elif str_column_name == "sGenkinHosyokinRitu" : str_name_kanji = "現金保証金率"
 elif str_column_name == "sCreateDate" : str_name_kanji = "作成日"
 elif str_column_name == "sUpdateNumber" : str_name_kanji = "更新番号"
 elif str_column_name == "sUpdateDate" : str_name_kanji = "更新日"
 else :
 str_name_kanji = str_column_name
 return str_name_kanji






def func_make_column_CLMDateZyouhou():
 str_column = 'sDayKey'
 str_column = str_column + ',' + 'sMaeEigyouDay_1'
 str_column = str_column + ',' + 'sMaeEigyouDay_2'
 str_column = str_column + ',' + 'sMaeEigyouDay_3'
 str_column = str_column + ',' + 'sTheDay'
 str_column = str_column + ',' + 'sYokuEigyouDay_1'
 str_column = str_column + ',' + 'sYokuEigyouDay_2'
 str_column = str_column + ',' + 'sYokuEigyouDay_3'
 str_column = str_column + ',' + 'sYokuEigyouDay_4'
 str_column = str_column + ',' + 'sYokuEigyouDay_5'
 str_column = str_column + ',' + 'sYokuEigyouDay_6'
 str_column = str_column + ',' + 'sYokuEigyouDay_7'
 str_column = str_column + ',' + 'sYokuEigyouDay_8'
 str_column = str_column + ',' + 'sYokuEigyouDay_9'
 str_column = str_column + ',' + 'sYokuEigyouDay_10'
 str_column = str_column + ',' + 'sKabuUkewatasiDay'
 str_column = str_column + ',' + 'sKabuKariUkewatasiDay'
 str_column = str_column + ',' + 'sBondUkewatasiDay'
 return str_column


# 機能: 日付情報（CLMDateZyouhou）用 列名を漢字に変換 
# 引数: 列名
# 返値: 漢字名 string型
def func_column_kanji_CLMDateZyouhou(str_column_name):
 if str_column_name == "sDayKey" : str_name_kanji = "日付ＫＥＹ"
 elif str_column_name == "sMaeEigyouDay_1" : str_name_kanji = "３営業日前"
 elif str_column_name == "sMaeEigyouDay_2" : str_name_kanji = "２営業日前"
 elif str_column_name == "sMaeEigyouDay_3" : str_name_kanji = "１営業日前"
 elif str_column_name == "sTheDay" : str_name_kanji = "当日日付"
 elif str_column_name == "sYokuEigyouDay_1" : str_name_kanji = "翌１営業日"
 elif str_column_name == "sYokuEigyouDay_2" : str_name_kanji = "翌２営業日"
 elif str_column_name == "sYokuEigyouDay_3" : str_name_kanji = "翌３営業日"
 elif str_column_name == "sYokuEigyouDay_4" : str_name_kanji = "翌４営業日"
 elif str_column_name == "sYokuEigyouDay_5" : str_name_kanji = "翌５営業日"
 elif str_column_name == "sYokuEigyouDay_6" : str_name_kanji = "翌６営業日"
 elif str_column_name == "sYokuEigyouDay_7" : str_name_kanji = "翌７営業日"
 elif str_column_name == "sYokuEigyouDay_8" : str_name_kanji = "翌８営業日"
 elif str_column_name == "sYokuEigyouDay_9" : str_name_kanji = "翌９営業日"
 elif str_column_name == "sYokuEigyouDay_10" : str_name_kanji = "翌１０営業日"
 elif str_column_name == "sKabuUkewatasiDay" : str_name_kanji = "株式受渡日"
 elif str_column_name == "sKabuKariUkewatasiDay" : str_name_kanji = "株式仮決受渡日"
 elif str_column_name == "sBondUkewatasiDay" : str_name_kanji = "債券受渡日"
 else :
 str_name_kanji = str_column_name
 return str_name_kanji






def func_make_column_CLMOrderErrReason():
 str_column = 'sErrReasonCode'
 str_column = str_column + ',' + 'sErrReasonText'
 return str_column


# 機能: エラー理由（CLMOrderErrReason）用 列名を漢字に変換 
# 引数: 列名
# 返値: 漢字名 string型
def func_column_kanji_CLMOrderErrReason(str_column_name):
 if str_column_name == "sErrReasonCode" : str_name_kanji = "エラーコード"
 elif str_column_name == "sErrReasonText" : str_name_kanji = "エラーメッセージ"
 else :
 str_name_kanji = str_column_name
 return str_name_kanji






def func_make_column_CLMSystemStatus():
 str_column = 'sSystemStatusKey'
 str_column = str_column + ',' + 'sLoginKyokaKubun'
 str_column = str_column + ',' + 'sSystemStatus'
 str_column = str_column + ',' + 'sCreateTime'
 str_column = str_column + ',' + 'sUpdateTime'
 str_column = str_column + ',' + 'sUpdateNumber'
 str_column = str_column + ',' + 'sDeleteFlag'
 str_column = str_column + ',' + 'sDeleteTime'
 return str_column


# 機能: システムステイタス（CLMSystemStatus）用 列名を漢字に変換 
# 引数: 列名
# 返値: 漢字名 string型
def func_column_kanji_CLMSystemStatus(str_column_name):
 if str_column_name == "sSystemStatusKey" : str_name_kanji = "システムステイタスキー"
 elif str_column_name == "sLoginKyokaKubun" : str_name_kanji = "ログイン許可区分"
 elif str_column_name == "sSystemStatus" : str_name_kanji = "システムステイタスキー"
 elif str_column_name == "sCreateTime" : str_name_kanji = "作成日時"
 elif str_column_name == "sUpdateTime" : str_name_kanji = "更新日時"
 elif str_column_name == "sUpdateNumber" : str_name_kanji = "更新番号"
 elif str_column_name == "sDeleteFlag" : str_name_kanji = "削除フラグ"
 elif str_column_name == "sDeleteTime" : str_name_kanji = "削除日時"
 else :
 str_name_kanji = str_column_name
 return str_name_kanji






def func_make_column_CLMYobine():
 str_column = 'sYobineTaniNumber'
 str_column = str_column + ',' + 'sTekiyouDay'
 str_column = str_column + ',' + 'sKizunPrice_1'
 str_column = str_column + ',' + 'sKizunPrice_2'
 str_column = str_column + ',' + 'sKizunPrice_3'
 str_column = str_column + ',' + 'sKizunPrice_4'
 str_column = str_column + ',' + 'sKizunPrice_5'
 str_column = str_column + ',' + 'sKizunPrice_6'
 str_column = str_column + ',' + 'sKizunPrice_7'
 str_column = str_column + ',' + 'sKizunPrice_8'
 str_column = str_column + ',' + 'sKizunPrice_9'
 str_column = str_column + ',' + 'sKizunPrice_10'
 str_column = str_column + ',' + 'sKizunPrice_11'
 str_column = str_column + ',' + 'sKizunPrice_12'
 str_column = str_column + ',' + 'sKizunPrice_13'
 str_column = str_column + ',' + 'sKizunPrice_14'
 str_column = str_column + ',' + 'sKizunPrice_15'
 str_column = str_column + ',' + 'sKizunPrice_16'
 str_column = str_column + ',' + 'sKizunPrice_17'
 str_column = str_column + ',' + 'sKizunPrice_18'
 str_column = str_column + ',' + 'sKizunPrice_19'
 str_column = str_column + ',' + 'sKizunPrice_20'
 str_column = str_column + ',' + 'sYobineTanka_1'
 str_column = str_column + ',' + 'sYobineTanka_2'
 str_column = str_column + ',' + 'sYobineTanka_3'
 str_column = str_column + ',' + 'sYobineTanka_4'
 str_column = str_column + ',' + 'sYobineTanka_5'
 str_column = str_column + ',' + 'sYobineTanka_6'
 str_column = str_column + ',' + 'sYobineTanka_7'
 str_column = str_column + ',' + 'sYobineTanka_8'
 str_column = str_column + ',' + 'sYobineTanka_9'
 str_column = str_column + ',' + 'sYobineTanka_10'
 str_column = str_column + ',' + 'sYobineTanka_11'
 str_column = str_column + ',' + 'sYobineTanka_12'
 str_column = str_column + ',' + 'sYobineTanka_13'
 str_column = str_column + ',' + 'sYobineTanka_14'
 str_column = str_column + ',' + 'sYobineTanka_15'
 str_column = str_column + ',' + 'sYobineTanka_16'
 str_column = str_column + ',' + 'sYobineTanka_17'
 str_column = str_column + ',' + 'sYobineTanka_18'
 str_column = str_column + ',' + 'sYobineTanka_19'
 str_column = str_column + ',' + 'sYobineTanka_20'
 str_column = str_column + ',' + 'sDecimal_1'
 str_column = str_column + ',' + 'sDecimal_2'
 str_column = str_column + ',' + 'sDecimal_3'
 str_column = str_column + ',' + 'sDecimal_4'
 str_column = str_column + ',' + 'sDecimal_5'
 str_column = str_column + ',' + 'sDecimal_6'
 str_column = str_column + ',' + 'sDecimal_7'
 str_column = str_column + ',' + 'sDecimal_8'
 str_column = str_column + ',' + 'sDecimal_9'
 str_column = str_column + ',' + 'sDecimal_10'
 str_column = str_column + ',' + 'sDecimal_11'
 str_column = str_column + ',' + 'sDecimal_12'
 str_column = str_column + ',' + 'sDecimal_13'
 str_column = str_column + ',' + 'sDecimal_14'
 str_column = str_column + ',' + 'sDecimal_15'
 str_column = str_column + ',' + 'sDecimal_16'
 str_column = str_column + ',' + 'sDecimal_17'
 str_column = str_column + ',' + 'sDecimal_18'
 str_column = str_column + ',' + 'sDecimal_19'
 str_column = str_column + ',' + 'sDecimal_20'
 str_column = str_column + ',' + 'sCreateDate'
 str_column = str_column + ',' + 'sUpdateDate'
 return str_column




# 機能: 呼値（CLMYobine）用 列名を漢字に変換 
# 引数: 列名
# 返値: 漢字名 string型
def func_column_kanji_CLMYobine(str_column_name):
 if str_column_name == "sYobineTaniNumber" : str_name_kanji = "呼値単位番号"
 elif str_column_name == "sTekiyouDay" : str_name_kanji = "適用日"
 elif str_column_name == "sKizunPrice_1" : str_name_kanji = "基準値段_1"
 elif str_column_name == "sKizunPrice_2" : str_name_kanji = "基準値段_2"
 elif str_column_name == "sKizunPrice_3" : str_name_kanji = "基準値段_3"
 elif str_column_name == "sKizunPrice_4" : str_name_kanji = "基準値段_4"
 elif str_column_name == "sKizunPrice_5" : str_name_kanji = "基準値段_5"
 elif str_column_name == "sKizunPrice_6" : str_name_kanji = "基準値段_6"
 elif str_column_name == "sKizunPrice_7" : str_name_kanji = "基準値段_7"
 elif str_column_name == "sKizunPrice_8" : str_name_kanji = "基準値段_8"
 elif str_column_name == "sKizunPrice_9" : str_name_kanji = "基準値段_9"
 elif str_column_name == "sKizunPrice_10" : str_name_kanji = "基準値段_10"
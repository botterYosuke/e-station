# -*- coding: utf-8 -*-
# Copyright (c) 2021 Tachibana Securities Co., Ltd. All rights reserved.


# 2021.07.08,   yo.
# 2022.10.25 reviced,   yo.
# 2025.07.27 reviced,   yo.
#
# 立花証券ｅ支店ＡＰＩ利用のサンプルコード
#
# 動作確認
# Python 3.11.2 / debian12
# API v4r7
#
# 機能: 注文訂正 を行ないます。
#
#
# 利用方法: 
# 事前に「e_api_login_tel.py」を実行して、
# 仮想URL（1日券）等を取得しておいてください。
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
 print('Can not Write!!!')
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
 
 
# 機能: ファイルに書き込む
# 引数1: 出力ファイル名
# 引数2: 出力するデータ
# 備考:
def func_write_to_file(str_fname_output, str_data):
 try:
 with open(str_fname_output, 'w', encoding = 'utf-8') as fout:
 fout.write(str_data)
 except IOError as e:
 print('Can not Write!!!')
 print(type(e))




# 機能: p_noを保存するためのjson形式のテキストデータを作成します。
# 引数1: p_noを保存するファイル名（fname_info_p_no = "e_api_info_p_no.txt"）
# 引数2: 保存するp_no
# 備考:
def func_save_p_no(str_fname_output, int_p_no):
 req_item = [class_req()]
 str_key = '"p_no"'
 str_value = func_check_json_dquat(str(int_p_no))
 #req_item.append(class_req())
 req_item[-1].add_data(str_key, str_value)


 str_json_p_no = func_make_json_format(req_item)
 func_write_to_file(str_fname_output, str_json_p_no)
 print('現在の"p_no"を保存しました。 p_no =', int_p_no)            
 print('ファイル名:', str_fname_output)


#--- 以上 共通コード -------------------------------------------------












# 参考資料（必ず最新の資料を参照してください。）
#マニュアル
#「立花証券・ｅ支店・ＡＰＩ（v4r2）、REQUEST I/F、機能毎引数項目仕様」
# (api_request_if_clumn_v4r2.pdf)
# p6/46 No.6 CLMKabuCorrectOrder を参照してください。
#
#  6 CLMKabuCorrectOrder
#  1	sCLMID	メッセージＩＤ	char*	I/O	CLMKabuCorrectOrder
#  2	sResultCode	結果コード	char[9]	O	業務処理．エラーコード 0：正常。5桁数字：「結果テキスト」に対応するエラーコード
#  3	sResultText	結果テキスト	char[512]	O	ShiftJis。「結果コード」に対応するテキスト
#  4	sOrderNumber	注文番号	char[8]	I/O	新規注文、応答の該当項目
#  5	sEigyouDay	営業日	char[8]	I/O	営業日（YYYYMMDD）
#  6	sCondition	執行条件	char[1]	I	*：変更なし、 0：指定なし、2：寄付、4：引け、6：不成
#  7	sOrderPrice	注文値段	char[14]    I	*：変更なし、 0：成行に変更、訂正注文値段：指値を変更
#  8	sOrderSuryou	注文数量	char[13]    I	*：変更なし、訂正数量：数量を変更（増株不可）。※訂正数量には、内出来を含んだ数量を指定
#  9	sOrderExpireDay	注文期日	char[8]	    I	*：変更なし、 0：当日、変更注文期日日(YYYYMMDD)[10営業日迄]
# 10	sGyakusasiZyouken   逆指値条件   char[14]	    I	*：変更なし、 0：成行に変更、逆指値注文値段：逆指値注文値段を変更
# 11	sGyakusasiPrice	逆指値注文値段     char[14]    I	*：変更なし、 0：成行に変更、逆指値注文値段：逆指値注文値段を変更
# 12	sSecondPassword	第二パスワード     char[48]    I	第二暗証番号（APIでは省略不可）、''：第二暗証番号省略時、関連資料：「立花証券・ｅ支店・ＡＰＩ、インターフェース概要」の「３－２．ログイン、ログアウト」参照
# 13	sOrderUkewatasiKingaku	注文受渡金額	char[16]	O	注文受渡金額
# 14	sOrderTesuryou	注文手数料	char[16]	O	注文手数料
# 15	sOrderSyouhizei	注文消費税	char[16]	O	注文消費税
# 16	sOrderDate	注文日時	char[14]	O	注文日時（YYYYMMDDHHMMSS）








# --------------------------
# 機能: 注文訂正
# 返値: API応答（辞書型）
# 引数1： p_no
# 引数2： 注文番号
# 引数3： 営業日（yyyymmdd）
# 引数4： 執行条件 （*：変更なし、 0：指定なし、2：寄付、4：引け、6：不成）
# 引数5： 注文値段 （訂正後の値段。*：変更なし、 0：成行に変更、訂正注文値段：指値を変更）
# 引数6： 注文数量 （訂正後の株数。内出来を含んだ数量を指定。*：変更なし、訂正数量：数量を変更（増株不可）。）
# 引数7： class_login_property（request通番）, 口座属性クラス
# 備考:
#       
def func_correct_order(int_p_no,
 str_sOrderNumber,
 str_sEigyouDay,
 str_sCondition,
 str_sOrderPrice,
 str_sOrderSuryou,
 class_login_property):


 req_item = [class_req()]
 str_p_sd_date = func_p_sd_date(datetime.datetime.now())     # システム時刻を所定の書式で取得


 str_key = '"p_no"'
 str_value = func_check_json_dquat(str(int_p_no))
 #req_item.append(class_req())
 req_item[-1].add_data(str_key, str_value)


 str_key = '"p_sd_date"'
 str_value = str_p_sd_date
 req_item.append(class_req())
 req_item[-1].add_data(str_key, str_value)


 # コマンド
 str_key = '"sCLMID"'
 str_value = 'CLMKabuCorrectOrder' # 注文訂正。
 req_item.append(class_req())
 req_item[-1].add_data(str_key, str_value)
 
 # 注文番号　省略不可
 str_key = '"sOrderNumber"'
 str_value = str_sOrderNumber # 注文番号は、注文約定一覧で取得できる。新規注文、応答の該当項目。
 req_item.append(class_req())
 req_item[-1].add_data(str_key, str_value)
 
 # 営業日　省略不可
 str_key = '"sEigyouDay"'
 str_value = str_sEigyouDay # yyyymmdd 営業日
 req_item.append(class_req())
 req_item[-1].add_data(str_key, str_value)


 # 執行条件
 str_key = '"sCondition"'
 str_value = str_sCondition # 執行条件 *：変更なし、 0：指定なし、2：寄付、4：引け、6：不成
 req_item.append(class_req())
 req_item[-1].add_data(str_key, str_value)


 # 注文値段
 str_key = '"sOrderPrice"'
 str_value = str_sOrderPrice # 注文値段  *：変更なし、 0：成行に変更、訂正注文値段：指値を変更
 req_item.append(class_req())
 req_item[-1].add_data(str_key, str_value)


 # 注文数量
 str_key = '"sOrderSuryou"'
 str_value = str_sOrderSuryou # 注文数量  *：変更なし、訂正数量：数量を変更（増株不可）。※訂正数量には、内出来を含んだ数量を指定
 req_item.append(class_req())
 req_item[-1].add_data(str_key, str_value)


 # -------------
 # このサンプルでは、以下の項目を変更無しで固定とした。
 # 注文期日
 str_key = '"sOrderExpireDay"'
 str_value = '*' # 注文期日  *：変更なし、 0：当日、変更注文期日日(YYYYMMDD)[10営業日迄]
 req_item.append(class_req())
 req_item[-1].add_data(str_key, str_value)


 # 逆指値条件
 str_key = '"sGyakusasiZyouken"'
 str_value = '*' # 逆指値条件  *：変更なし、 0：成行に変更、逆指値注文値段：逆指値注文値段を変更
 req_item.append(class_req())
 req_item[-1].add_data(str_key, str_value)


 # 逆指値注文値段
 str_key = '"sGyakusasiPrice"'
 str_value = '*' # 逆指値注文値段  *：変更なし、 0：成行に変更、逆指値注文値段：逆指値注文値段を変更
 req_item.append(class_req())
 req_item[-1].add_data(str_key, str_value)
 # -------------


 # 第二パスワード
 str_key = '"sSecondPassword"'
 str_value = class_login_property.sSecondPassword # 第二暗証番号（APIでは省略不可）
 req_item.append(class_req())
 req_item[-1].add_data(str_key, str_value)


 # 返り値の表示形式指定
 str_key = '"sJsonOfmt"'
 str_value = class_login_property.sJsonOfmt # "5"は "1"（ビット目ＯＮ）と”4”（ビット目ＯＮ）の指定となり「ブラウザで見や易い形式」且つ「引数項目名称」で応答を返す値指定
 req_item.append(class_req())
 req_item[-1].add_data(str_key, str_value)


 # URL文字列の作成
 str_url = func_make_url_request(False, \
 class_login_property.sUrlRequest, \
 req_item)


 json_return = func_api_req(str_url)


 return json_return






 
# ======================================================================================================
# ==== プログラム始点 =================================================================================
# ======================================================================================================
# 必要な設定項目
# 注文受付番号: my_sOrderNumber（発注時に返される'sOrderNumber'）標準web画面の「照会／訂正／取消」画面の「状態」から開く「注文詳細」で確認可能。
# 営業日: my_sEigyouDay（yyyymmdd 実行時の営業日）
# 執行条件: my_sCondition  （*：変更なし、 0：指定なし、2：寄付、4：引け、6：不成）
# 訂正値段: my_sOrderPrice （ *：変更なし、 0：成行に変更、訂正注文値段：指値を変更）
# 訂正数量: my_sOrderSuryou （ *：変更なし、訂正数量：数量を変更（増株不可）。）
 # ※訂正数量には、内出来を含んだ数量を指定


if __name__ == "__main__":


 # --- 利用時に変数を設定してください -------------------------------------------------------
 # コマンド用パラメーター -------------------    
 my_sOrderNumber = '12345678' # 注文番号　省略不可 # 注文番号は、注文約定一覧で取得できる。新規注文、応答の該当項目。
 my_sEigyouDay = 'yyyymmdd' # 営業日　省略不可   yyyymmdd
 my_sCondition = '*' # 執行条件  *：変更なし、 0：指定なし、2：寄付、4：引け、6：不成
 my_sOrderPrice = '*' # 注文値段  注文値段  *：変更なし、 0：成行に変更、訂正注文値段：指値を変更
 my_sOrderSuryou = '*' # 注文数量  注文数量  *：変更なし、訂正数量：数量を変更（増株不可）。
 # ※訂正数量には、内出来を含んだ数量を指定
 
 # 訂正例 ----------------------
 # 値上げ: 100円 -> 110円
 ##my_sOrderNumber = '12345678'      
 ##my_sEigyouDay = '20221025'  
 ##my_sCondition = '*'
 ##my_sOrderPrice = '110'
 ##my_sOrderSuryou = '*'


 # 指値から成行に訂正
 ##my_sOrderNumber = '12345678'      
 ##my_sEigyouDay = '20221025'  
 ##my_sCondition = '*'
 ##my_sOrderPrice = '0'
 ##my_sOrderSuryou = '*'


 # 指値100円から指値100円不出来引け成に訂正
 ##my_sOrderNumber = '12345678'      
 ##my_sEigyouDay = '20221025'  
 ##my_sCondition = '6'
 ##my_sOrderPrice = '*'
 ##my_sOrderSuryou = '*'


 # 株数削減: 500株 -> 200株
 ##my_sOrderNumber = '12345678'      
 ##my_sEigyouDay = '20221025'  
 ##my_sCondition = '*'
 ##my_sOrderPrice = '*'
 ##my_sOrderSuryou = '200'


 # 価格と株数削減を同時に指定
 # 値上げ: 100円 -> 110円
 # 株数削減: 500株 -> 200株
 ##my_sOrderNumber = '12345678'      
 ##my_sEigyouDay = '20221025'  
 ##my_sCondition = '*'
 ##my_sOrderPrice = '110'
 ##my_sOrderSuryou = '200'


 # --- 以上設定項目 -------------------------------------------------------------------------


 # --- ファイル名等を設定 ------------------------------------------------------------------
 fname_account_info = "e_api_account_info.txt"
 fname_login_response = "e_api_login_response.txt"
 fname_info_p_no = "e_api_info_p_no.txt"
 # --- 以上ファイル名設定 -------------------------------------------------------------------------


 my_account_property = class_def_account_property()
 my_login_property = class_def_login_property()
 
 # 口座情報をファイルから読み込む。
 func_get_acconut_info(fname_account_info, my_account_property)
 
 # ログイン応答を保存した「e_api_login_response.txt」から、仮想URLと課税flgを取得
 func_get_login_info(fname_login_response, my_login_property)


 
 my_login_property.sJsonOfmt = my_account_property.sJsonOfmt # 返り値の表示形式指定
 my_login_property.sSecondPassword = func_replace_urlecnode(my_account_property.sSecondPassword)        # 22.第二パスワード  APIでは第2暗証番号を省略できない。 関連資料:「立花証券・e支店・API、インターフェース概要」の「3-2.ログイン、ログアウト」参照
 
 # 現在（前回利用した）のp_noをファイルから取得する
 func_get_p_no(fname_info_p_no, my_login_property)
 my_login_property.p_no = my_login_property.p_no + 1
 
 print()
 print('-- 注文訂正 -------------------------------------------------------------')
 dic_return = func_correct_order(my_login_property.p_no,
 my_sOrderNumber,
 my_sEigyouDay,
 my_sCondition,
 my_sOrderPrice,
 my_sOrderSuryou,
 my_login_property)
 # 送信項目、戻り値の解説は、マニュアル「立花証券・ｅ支店・ＡＰＩ（ｖ〇）、REQUEST I/F、機能毎引数項目仕様」
 # p6/46 No.6 CLMKabuCorrectOrder を参照してください。
 
 if dic_return.get('p_errno') != '-2' and dic_return.get('p_errno') != '2':
 print('結果コード:\t', dic_return.get('sResultCode'))
 print('結果テキスト:\t', dic_return.get('sResultText'))
 print('注文番号:\t', dic_return.get('sOrderNumber'))
 print('営業日:\t', dic_return.get('sEigyouDay'))
 print('注文受渡金額:\t', dic_return.get('sOrderUkewatasiKingaku'))
 print('注文手数料:\t', dic_return.get('sOrderTesuryou'))
 print('注文消費税:\t', dic_return.get('sOrderSyouhizei'))
 print('注文日時:\t', dic_return.get('sOrderDate'))
 print()
 
 elif dic_return.get('p_errno') == '-2' :
 print("パラメーターの設定に誤りが有ります。")


 # 仮想URLが無効になっている場合
 # if dic_return.get('p_errno') == '2':
 else:
 print()
 print('p_errno', dic_return.get('p_errno'))
 print('p_err', dic_return.get('p_err'))
 print()    
 print("仮想URLが有効ではありません。")
 print("電話認証 + e_api_login_tel.py実行")
 print("を再度行い、新しく仮想URL（1日券）を取得してください。")
 
 print()    
 print()    
 # "p_no"を保存する。
 func_save_p_no(fname_info_p_no, my_login_property.p_no)
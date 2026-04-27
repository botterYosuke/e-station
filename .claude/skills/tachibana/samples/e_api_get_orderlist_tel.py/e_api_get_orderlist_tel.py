# -*- coding: utf-8 -*-
# Copyright (c) 2021 Tachibana Securities Co., Ltd. All rights reserved.


# 2021.07.08,   yo.
# 2022.10.20 reviced,   yo.
# 2025.07.27 reviced,   yo.
#
# 立花証券ｅ支店ＡＰＩ利用のサンプルコード
#
# 動作確認
# Python 3.11.2 / debian12
# API v4r7
#
# 機能: 注文約定一覧取得
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








# 参考資料（必ず最新の資料を参照してください。）
#マニュアル
#「立花証券・ｅ支店・ＡＰＩ（v4r2）、REQUEST I/F、機能毎引数項目仕様」
# (api_request_if_clumn_v4r2.pdf)
# p14/46 No.13 CLMOrderList を参照してください。
#
# 13 CLMOrderList
#  1	sCLMID	メッセージＩＤ	char*	I/O	"CLMOrderList"
#  2	sIssueCode	銘柄コード	char[12]	I/O	銘柄コード（6501 等）
#  3	sSikkouDay	注文執行予定日	char[8]	I/O	YYYYMMDD  CLMKabuCorrectOrder、CLMKabuCancelOrder、CLMOrderListDetail におけるsEigyouDayと同値
#  4	sOrderSyoukaiStatus	注文照会状態	char[1]	I/O	値無し：指定なし。 1：未約定、2：全部約定、3：一部約定、4：訂正取消(可能な注文）、5：未約定 + 一部約定
#  5	sResultCode	結果コード	char[9]	O	０：ＯＫ、０以外：CLMMsgTable.sMsgIdで検索しテキストを表示。 0～999999999、左詰め、マイナスの場合なし
#  6	sResultText	結果テキスト	char[512]	O	ShiftJis
#  7	sWarningCode	警告コード	char[9]	O	０：ＯＫ、０以外：CLMMsgTable.sMsgIdで検索しテキストを表示。 0～999999999、左詰め、マイナスの場合なし
#  8	sWarningText	警告テキスト	char[512]	O	ShiftJis
#  9	aOrderList	注文リスト （※項目数に増減がある場合は、右記のカラム数も変更すること）	char[17]	O	以下レコードを配列で設定
# 10-1	sOrderWarningCode	警告コード	char[9]	O	０：ＯＫ、０以外：CLMMsgTable.sMsgIdで検索しテキストを表示。0～999999999、左詰め、マイナスの場合なし
# 11-2	sOrderWarningText	警告テキスト	char[512]	O	ShiftJis
# 12-3	sOrderOrderNumber	注文番号	char[8]	O	0～99999999、左詰め、マイナスの場合なし
# 13-4	sOrderIssueCode	銘柄コード	char[12]	O	-
# 14-5	sOrderSizyouC	市場	char[2]	O	00：東証
# 15-6	sOrderZyoutoekiKazeiC	譲渡益課税区分	char[1]	O	1：特定、3：一般、5：NISA
# 16-7	sGenkinSinyouKubun	現金信用区分	char[1]	O	0：現物、2：新規(制度信用6ヶ月)、4：返済(制度信用6ヶ月)、6：新規(一般信用6ヶ月)、8：返済(一般信用6ヶ月)
# 17-8	sOrderBensaiKubun	弁済区分	char[2]	O	00：なし、26：制度信用6ヶ月、29：制度信用無期限、36：一般信用6ヶ月、39：一般信用無期限
# 18-9	sOrderBaibaiKubun	売買区分	char[1]	O	1：売、3：買、5：現渡、7：現引
# 19-10	sOrderOrderSuryou	注文株数	char[13]	O	照会機能仕様書 ２－７．（３）、（１）一覧 No.12。 0～9999999999999、左詰め、マイナスの場合なし
# 20-11	sOrderCurrentSuryou	有効株数	char[13]	O	0～9999999999999、左詰め、マイナスの場合なし
# 21-12	sOrderOrderPrice	注文単価	char[14]	O	0.0000～999999999.9999、左詰め、マイナスの場合なし、小数点以下桁数切詰
# 22-13	sOrderCondition	執行条件	char[1]	O	0：指定なし、2：寄付、4：引け、6：不成
# 23-14	sOrderOrderPriceKubun	注文値段区分	char[1]	O	△：未使用、 1：成行、2：指値、3：親注文より高い、4：親注文より低い
# 24-15	sOrderGyakusasiOrderType	逆指値注文種別	char[1]	O	0：通常、1：逆指値、2：通常＋逆指値
# 25-16	sOrderGyakusasiZyouken	逆指値条件	char[14]	O	0.0000～999999999.9999、左詰め、マイナスの場合なし、小数点以下桁数切詰
# 26-17	sOrderGyakusasiKubun	逆指値値段区分	char[1]	O	△：未使用、 0：成行、1：指値
# 27-18	sOrderGyakusasiPrice	逆指値値段	char[14]	O	0.0000～999999999.9999、左詰め、マイナスの場合なし、小数点以下桁数切詰
# 28-19	sOrderTriggerType	トリガータイプ	char[1]	O	0：未トリガー, 1：自動, 2：手動発注, 3：手動失効。 初期状態は「0」で、トリガー発火後は「1/2/3」のどれかに遷移する
# 29-20	sOrderTatebiType	建日種類	char[1]	O	△：指定なし、 1：個別指定、2：建日順、3：単価益順、4：単価損順
# 30-21	sOrderZougen	リバース増減値	char[14]	O	項目は残すが使用しない
# 31-22	sOrderYakuzyouSuryo	成立株数	char[13]	O	0～9999999999999、左詰め、マイナスの場合なし
# 32-23	sOrderYakuzyouPrice	成立単価	char[14]	O	照会機能仕様書 ２－７．（３）、（１）一覧 No.16。 0.0000～999999999.9999、左詰め、マイナスの場合なし、小数点以下桁数切詰
# 33-24	sOrderUtidekiKbn	内出来区分	char[1]	O	△：約定分割以外、 2：約定分割
# 34-25	sOrderSikkouDay	執行日	char[8]	O	YYYYMMDD
# 35-26	sOrderStatusCode	状態コード	char[2]	O	
 #[逆指値]、[通常+逆指値]注文時以外の状態
 #0：受付未済
 #1：未約定
 #2：受付エラー
 #3：訂正中
 #4：訂正完了
 #5：訂正失敗
 #6：取消中
 #7：取消完了
 #8：取消失敗
 #9：一部約定
 #10：全部約定
 #11：一部失効
 #12：全部失効
 #13：発注待ち
 #14：無効
 #15：切替注文
 #16：切替完了
 #17：切替注文失敗
 #19：繰越失効
 #20：一部障害処理
 #21：障害処理
 #[逆指値]、[通常+逆指値]注文時の状態
 #15：逆指注文(切替中)
 #16：逆指注文(未約定)
 #17：逆指注文(失敗)
 #50：発注中 
# 36-27	sOrderStatus	状態	char[20]	O	
# 37-28	sOrderYakuzyouStatus	約定ステータス	char[2]	O	0：未約定、1：一部約定、2：全部約定、3：約定中
# 38-29	sOrderOrderDateTime	注文日付	char[14]	O	YYYYMMDDHHMMSS,00000000000000
# 39-30	sOrderOrderExpireDay	有効期限	char[8]	O	YYYYMMDD,00000000
# 40-31	sOrderKurikosiOrderFlg	繰越注文フラグ	char[1]	O	0：当日注文、1：繰越注文、2：無効
# 41-32	sOrderCorrectCancelKahiFlg	訂正取消可否フラグ	char[1]	O	0：可(取消、訂正)、1：否、2：一部可(取消のみ)
# 42-33	sGaisanDaikin	概算代金	char[16]	O	-999999999999999～9999999999999999、左詰め、マイナスの場合あり








# --------------------------
# 機能: 注文約定一覧の取得
# 返値: API応答（辞書型）
# 引数1： p_no
# 引数2： class_login_property（request通番）, 口座属性クラス
# 備考:
#       銘柄コードは省略可。''：指定なし の場合、一覧全体を取得する。
def func_get_orderlist(int_p_no, str_sOrderIssueCode, class_login_property):


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


 str_key = '"sCLMID"'
 str_value = 'CLMOrderList' # 注文約定一覧を指定。
 req_item.append(class_req())
 req_item[-1].add_data(str_key, str_value)


 str_key = '"sIssueCode"'
 str_value = str_sOrderIssueCode # 銘柄コード     ''：指定なし の場合、一覧全体を取得する。
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


if __name__ == "__main__":


 # --- 利用時に変数を設定してください -------------------------------------------------------
 # コマンド用パラメーター -------------------    
 my_sOrderIssueCode = '' # 銘柄コード     ''：指定なし の場合、一覧全体を取得する。


 # --- 以上設定項目 -------------------------------------------------------------------------


 # --- ファイル名等を設定（実行ファイルと同じディレクトリ） ---------------------------------------
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
 print('-- 注文約定一覧の取得 -------------------------------------------------------------')
 dic_return = func_get_orderlist(my_login_property.p_no, my_sOrderIssueCode, my_login_property)
 # 送信項目、戻り値の解説は、マニュアル「立花証券・ｅ支店・ＡＰＩ（ｖ〇）、REQUEST I/F、機能毎引数項目仕様」
 # p14/46 No.13 CLMOrderList を参照してください。
 if dic_return is not None:
 print("結果コード= ", dic_return.get("sResultCode"))           # 5
 print("結果テキスト= ", dic_return.get("sResultText"))  # 6
 dic_aOrderList = dic_return.get("aOrderList")
 if dic_aOrderList is not None:
 print('注文リスト= aOrderList')
 print('件数:', len(dic_aOrderList))
 print()
 
 # 'aOrderList'の返値の処理。
 # データ形式は、"aOrderList":[{...},{...}, ... ,{...}]
 for i in range(len(dic_aOrderList)):
 print('No.', i+1, '---------------')
 print('警告コード:\t', dic_aOrderList[i].get('sOrderWarningCode'))
 print('警告テキスト:\t', dic_aOrderList[i].get('sOrderWarningText'))
 print('注文番号:\t', dic_aOrderList[i].get('sOrderOrderNumber'))
 print('銘柄コード:\t', dic_aOrderList[i].get('sOrderIssueCode'))
 print('市場:\t', dic_aOrderList[i].get('sOrderSizyouC'))
 print('譲渡益課税区分:\t', dic_aOrderList[i].get('sOrderZyoutoekiKazeiC'))
 print('現金信用区分:\t', dic_aOrderList[i].get('sGenkinSinyouKubun'))
 print('弁済区分:\t', dic_aOrderList[i].get('sOrderBensaiKubun'))
 print('売買区分:\t', dic_aOrderList[i].get('sOrderBaibaiKubun'))
 print('注文株数:\t', dic_aOrderList[i].get('sOrderOrderSuryou'))
 print('有効株数:\t', dic_aOrderList[i].get('sOrderCurrentSuryou'))
 print('注文単価:\t', dic_aOrderList[i].get('sOrderOrderPrice'))
 print('執行条件:\t', dic_aOrderList[i].get('sOrderCondition'))
 print('注文値段区分:\t', dic_aOrderList[i].get('sOrderOrderPriceKubun'))
 print('逆指値注文種別:\t', dic_aOrderList[i].get('sOrderGyakusasiOrderType'))
 print('逆指値条件:\t', dic_aOrderList[i].get('sOrderGyakusasiZyouken'))
 print('逆指値値段区分:\t', dic_aOrderList[i].get('sOrderGyakusasiKubun'))
 print('逆指値値段:\t', dic_aOrderList[i].get('sOrderGyakusasiPrice'))
 print('トリガータイプ:\t', dic_aOrderList[i].get('sOrderTriggerType'))
 print('建日種類:\t', dic_aOrderList[i].get('sOrderTatebiType'))
 print('リバース増減値:\t', dic_aOrderList[i].get('sOrderZougen'))
 print('成立株数:\t', dic_aOrderList[i].get('sOrderYakuzyouSuryo'))
 print('成立単価:\t', dic_aOrderList[i].get('sOrderYakuzyouPrice'))
 print('内出来区分:\t', dic_aOrderList[i].get('sOrderUtidekiKbn'))
 print('執行日:\t', dic_aOrderList[i].get('sOrderSikkouDay'))
 print('状態:\t', dic_aOrderList[i].get('sOrderStatus'))
 print('約定ステータス:\t', dic_aOrderList[i].get('sOrderYakuzyouStatus'))
 print('注文日付:\t', dic_aOrderList[i].get('sOrderOrderDateTime'))
 print('有効期限:\t', dic_aOrderList[i].get('sOrderOrderExpireDay'))
 print('繰越注文フラグ:\t', dic_aOrderList[i].get('sOrderKurikosiOrderFlg'))
 print('訂正取消可否フラグ:\t', dic_aOrderList[i].get('sOrderCorrectCancelKahiFlg'))
 print('概算代金:\t', dic_aOrderList[i].get('sGaisanDaikin'))
 print()
 
 
 print()
 print('p_errno', dic_return.get('p_errno'))
 print('p_err', dic_return.get('p_err'))
 # 仮想URLが無効になっている場合
 if dic_return.get('p_errno') == '2':
 print()    
 print("仮想URLが有効ではありません。")
 print("電話認証 + e_api_login_tel.py実行")
 print("を再度行い、新しく仮想URL（1日券）を取得してください。")


 print()    
 print()    
 # "p_no"を保存する。
 func_save_p_no(fname_info_p_no, my_login_property.p_no)
# -*- coding: utf-8 -*-


# Copyright (c) 2022 Tachibana Securities Co., Ltd. All rights reserved.
# 2022/04/08, yo.


# mfds_json_api_compress_v4r2.js をpython用に作成した
# compress_v4r2_js.pyを使ってみるテストコード。
# 
# 利用の際はcompress_v4r2_jsを同一ディレクトリに置きインポートすること。




import compress_v4r2_js


# if文形式
print('if文形式------')
str_name = 'CLMAuthLoginRequest'
print(str_name)
int_code = compress_v4r2_js.func_name2code(str_name)
print(int_code)
print(compress_v4r2_js.func_code2name(int_code))
print()


# 辞書形式
print('辞書形式------')
dic_name_to_code = compress_v4r2_js.func_make_dic_name_to_code()
dic_code_to_name = compress_v4r2_js.func_make_dic_code_to_name()


str_name = 'CLMYobine'
print(str_name)
print(dic_name_to_code[str_name])


int_code = dic_name_to_code[str_name]
print(dic_code_to_name[int_code])
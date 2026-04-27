#!
#! Product:
#! 	mfds (realtime stock price board application modules).
#! 
#! Copyright:
#! 	Copyright (C) 2000 GOODLOTS CO.,LTD. ALL RIGHT RESERVED.
#! 
#! License:
#!	Released under the MIT license
#!	https://www.e-shiten.jp/e_api/LICENSE.txt
#!
#! Revision:
#!	$Id: e_api_sample_v4r8.py 3586 2025-08-26 23:22:19Z yoshi $
#! 
#! Function:
#!	MFDS, json-api (for v4r8) python sample module (v2.0-000).
#!
#! Create:
#!	2025.07.16
#!	Y.Yoshizawa
#!
#! Modify:
#!	some optimize update.
#!	2025.07.23
#!	Y.Yoshizawa
#!
#!	add proc_print_event_if_data ().
#!	2025.08.18
#!	Y.Yoshizawa
#!
#!	change server i/f logic to class.
#!	support for v4r8 and switch http GET or POST.
#!	2025.09.27
#!	Y.Yoshizawa
#!
#! Note:
#!	1. warrning
#!		this is e_api python sample program. 
#!		feel free to use it at your own risk and without support.
#!
#!	2. operating environment
#!		(1) Python 3.13.5 on CentOS Linux release 7.3.1611 (Core)
#!		(2) Python 3.8.12 on FreeBSD 13.0-RELEASE-p14 (GENERIC)
#!		(3) Python 3.13.5 on Window 11 Pro 24H2
#!		unknown whether it will work in other environments.
#!
#!	3. case on windows, manual install of import modules (urllib3, requests, websockets etc).
#!		run cmd.exe, ex: % pip install urllib3
#!
import	os
import	json
import	datetime
import	urllib3
import	urllib.parse


#!
#!	define global parameters.
#!
gp_url_auth		= "https://demo-kabuka.e-shiten.jp/e_api_v4r8/auth/"
gp_usid			= "username"
gp_pswd			= "password"


#!
#!	define function control flag.
#!
gp_proc_request_if	= 0	#! use request i/f sample get market price.
gp_proc_event_if	= 0	#! use event i/f specify issue_code's and columns.
gp_proc_event_ws	= 0	#! use event i/f websocket.


#!
#!	define e_api request i/f class.
#!
class	e_api_req_if:
	def	__init__(self):
		self._p_no			= 0
		self._p_sUrlRequest		= ''
		self._p_sUrlMaster		= ''
		self._p_sUrlPrice		= ''
		self._p_sUrlEvent		= ''
		self._p_sUrlEventWebSocket	= ''
		#!
		#!	define control flags.
		#!
		self._p_debug_print_http_req	= 0	#! print http request url.
		self._p_debug_print_http_rst	= 0	#! print http responce code (ex:200).
		self._p_debug_print_http_ans	= 0	#! print http responce data.
		self._p_debug_print_e_api_ans	= 0	#! print e_api answer (ex:p_errno).
		self._p_debug_print_e_api_url	= 0	#! print e_api url etc.
		self._p_debug_http_post		= 1	#! request to http-post or http-get.


	#!
	#!	generate access url.
	#!
	def	get_url_request (self):
		return (self._p_sUrlRequest)

	def	get_url_master (self):
		return (self._p_sUrlMaster)

	def	get_url_price (self):
		return (self._p_sUrlPrice)

	def	get_url_event (self):
		return (self._p_sUrlEvent)

	def	get_url_event_websocket (self):
		return (self._p_sUrlEventWebSocket)


	#!
	#!	get p_sd_date.
	#!
	def	_get_sd_date (self):
		p_dt	= datetime.datetime.now ()
		return	f"{p_dt.year}.{p_dt.month:02d}.{p_dt.day:02d}-{p_dt.hour:02d}:{p_dt.minute:02d}:{p_dt.second:02d}.000"


	#!
	#!	get YYYYMMDD.
	#!
	def	_get_yyyymmdd (self):
		p_dt	= datetime.datetime.now ()
		return	f"{p_dt.year}{p_dt.month:02d}{p_dt.day:02d}"


	#!
	#!	set column and value.
	#!
	def	_gen_col_val (self, pi_ary, pi_col, pi_prm):
		pi_ary.append (cs_token ())
		pi_ary[len (pi_ary) - 1].set (pi_col, pi_prm)
		return (pi_ary)


	#!
	#!	set e_api p_no parameter.
	#!
	def	_gen_no (self, pi_ary):
		self._p_no	+= 1
		pi_ary	= self._gen_col_val (pi_ary, "p_no",	str (self._p_no))
		#!
		#!	save last p_no and URLs for next use.
		#!
		self._file_save ()
		return (pi_ary)


	#!
	#!	set e_api p_sd_date parameter.
	#!
	def	_gen_sd_date (self, pi_ary):
		pi_ary	= self._gen_col_val (pi_ary, "p_sd_date",	self._get_sd_date ())
		return (pi_ary)


	#!
	#!	set e_api header parameters.
	#!
	def	_gen_header (self):
		p_ary	= []
		p_ary	= self._gen_no (p_ary)
		p_ary	= self._gen_sd_date (p_ary)
		p_ary	= self._gen_col_val (p_ary,	"sJsonOfmt",	"5")
		return (p_ary)


	#!
	#!	generate url parameter from token arrays.
	#!
	def	_gen_prm (self, pi_ary):
		p_prm = '{';
		for p_rec in pi_ary:
			p_prm	= p_prm + '"' + p_rec.p_key + '":"' + p_rec.p_val + '",'
		p_prm	= p_prm[:-1] + '}';
		return (p_prm)


	#!
	#!	request to e_api server.
	#!
	def	req_server (self, pi_url, pi_prm):
		if self._p_debug_print_http_req == 1:
			print	("REQ:[")
			print	(pi_url)
			print	(pi_prm)
			print	("]")

		p_http	= urllib3.PoolManager ()

		try:
			if self._p_debug_http_post == 1:
				p_resp	= p_http.request (
					'POST',
					pi_url,
					body=pi_prm,
					headers={'Content-Type': 'application/json'}
				)
			else:
				p_resp	= p_http.request (
					'GET',
					(pi_url + '?' + pi_prm)
				)

		except	Exception as p_exception:
			print	(f'exception catched:[{p_exception}]')
			exit (-1)

		if self._p_debug_print_http_rst == 1:
			print	("RST:[")
			print	(p_resp.status)
			print	("]")

		if p_resp.status == 200:
			p_ans	= p_resp.data.decode ('shift-jis')
			if self._p_debug_print_http_ans == 1:
				print	("ANS:[")
				print	(p_ans[:-1])
				print	("]")
			return (json.loads (p_ans))
		else:
			print	("error, http responce:[" + str (p_resp.status) + "]")
			exit (-1)


	#!
	#!	check e_api server answer.
	#!	return:
	#!		0: success
	#!		-1: e_api request error.
	#!		-2: e_api application error.
	#!
	def	ans_check (self, pi_ans):
		p_errno		= self.ans_get_val (pi_ans, 'p_errno',		"unknown")
		p_err		= self.ans_get_val (pi_ans, 'p_err',		"unknown")
		p_sResultCode	= self.ans_get_val (pi_ans, 'sResultCode',	"0")
		p_sResultText	= self.ans_get_val (pi_ans, 'sResultText',	"")

		if self._p_debug_print_e_api_ans:
			print	("p_errno:	[" + p_errno		+ "]")
			print	("p_err  :	[" + p_err		+ "]")
			print	("sResultCode:	[" + p_sResultCode	+ "]")
			print	("sResultText:	[" + p_sResultText	+ "]")

		if p_errno != "0":
			print	("error, e_api request error.")
			print	("p_errno:	[" + p_errno		+ "]")
			print	("p_err  :	[" + p_err		+ "]")
			return (-1)
		elif p_sResultCode != "0":
			print	("error, e_api application error.")
			print	("sResultCode:	[" + p_sResultCode	+ "]")
			print	("sResultText:	[" + p_sResultText	+ "]")
			return (-2)
		else:
			return (0)


	def	ans_get_val (self, pi_ans, pi_key, pi_default):
		if pi_key in pi_ans:
			return (pi_ans.get (pi_key))
		else:
			return (pi_default)


	#!
	#!	file load.
	#!
	def	_file_load (self):
		p_fna	= self._get_yyyymmdd () + "_e_api_sample.txt"
		try:
			p_fp				= open (p_fna, mode='r')
			p_rec				= p_fp.readlines ()
			p_fp.close ()
			self._p_no			= int (p_rec[0][:-1]);
			self._p_sUrlRequest		= p_rec[1][:-1];
			self._p_sUrlMaster		= p_rec[2][:-1];
			self._p_sUrlPrice		= p_rec[3][:-1];
			self._p_sUrlEvent		= p_rec[4][:-1];
			self._p_sUrlEventWebSocket	= p_rec[5][:-1];

			if (self._p_sUrlRequest == ""):
				return (-1)
			else:
				return (0)

		except	FileNotFoundError:
			self._p_no			= 0
			self._p_sUrlRequest		= ""
			self._p_sUrlMaster		= ""
			self._p_sUrlPrice		= ""
			self._p_sUrlEvent		= ""
			self._p_sUrlEventWebSocket	= ""
			return (-1)


	#!
	#!	file save.
	#!
	def	_file_save (self):
		p_fna	= self._get_yyyymmdd () + "_e_api_sample.txt"
		p_fp	= open (p_fna, mode='w')
		p_fp.write (str (self._p_no))
		p_fp.write ("\n")
		p_fp.write (self._p_sUrlRequest)
		p_fp.write ("\n")
		p_fp.write (self._p_sUrlMaster)
		p_fp.write ("\n")
		p_fp.write (self._p_sUrlPrice)
		p_fp.write ("\n")
		p_fp.write (self._p_sUrlEvent)
		p_fp.write ("\n")
		p_fp.write (self._p_sUrlEventWebSocket)
		p_fp.write ("\n")
		p_fp.close ()
		return (0)


	#!
	#!	set e_api::CLMAuthLoginRequest parameters.
	#!
	def	req_login (self, pi_url, pi_usid, pi_pswd):
		p_ary	= self._gen_header ()
		p_ary	= self._gen_col_val (p_ary,	"sCLMID",	"CLMAuthLoginRequest")
		p_ary	= self._gen_col_val (p_ary,	"sUserId",	pi_usid)
		p_ary	= self._gen_col_val (p_ary,	"sPassword",	pi_pswd)
		p_prm	= self._gen_prm (p_ary)
		p_ans	= self.req_server (pi_url, p_prm)
		return (p_ans)


	#!
	#!	set e_api::CLMMfdsGetMarketPrice parameters.
	#!
	def	req_market_price (self, pi_issue, pi_column):
		p_ary	= self._gen_header ()
		p_ary	= self._gen_col_val (p_ary,	"sCLMID",		"CLMMfdsGetMarketPrice")
		p_ary	= self._gen_col_val (p_ary,	"sTargetIssueCode",	pi_issue)
		p_ary	= self._gen_col_val (p_ary,	"sTargetColumn",	pi_column)
		p_prm	= self._gen_prm (p_ary)
		p_ans	= self.req_server (self.get_url_price (), p_prm)
		return (p_ans)


	#!
	#!	some value get from file or login server answer.
	#!
	def	req_or_file_login (self, pi_url, pi_usid, pi_pswd):
		#!
		#!	exit today url file?
		#!
		if self._file_load () != 0:
			#!
			#!	if not saved then call e_api login request.
			#!
			p_ans	= self.req_login (pi_url, pi_usid, pi_pswd)
			p_sts	= self.ans_check (p_ans)
			if p_sts != 0:
				return (p_sts)

			self._p_sUrlRequest		= p_ans.get ('sUrlRequest')
			self._p_sUrlMaster		= p_ans.get ('sUrlMaster')
			self._p_sUrlPrice		= p_ans.get ('sUrlPrice')
			self._p_sUrlEvent		= p_ans.get ('sUrlEvent')
			self._p_sUrlEventWebSocket	= p_ans.get ('sUrlEventWebSocket')

			self._file_save ()

		#!
		#!	output some load (file or server) values.
		#!
		if self._p_debug_print_e_api_url:
			print ("p_no			:[" + str (self._p_no)		+ "]")
			print ("sUrlRequest		:[" + self._p_sUrlRequest		+ "]")
			print ("sUrlMaster		:[" + self._p_sUrlMaster		+ "]")
			print ("sUrlPrice		:[" + self._p_sUrlPrice		+ "]")
			print ("sUrlEvent		:[" + self._p_sUrlEvent		+ "]")
			print ("sUrlEventWebSocket	:[" + self._p_sUrlEventWebSocket	+ "]")

		return (0)


#!
#!	define token structure (key,val) class.
#!
class	cs_token:
	def	__init__(self):
		self.p_key	= ''
		self.p_val	= ''
	def	set (self, pi_key, pi_val):
		self.p_key	= pi_key
		self.p_val	= pi_val


#!
#!	define e_api event i/f (http) class.
#!
class	e_api_evt_if:
	def	__init__(self, pi_api):
		self.p_api	= pi_api

	#!
	#!	request to e_api server.
	#!
	def	evt_server (self, pi_prm, pi_cbp):
		import	requests
		p_url	= self.p_api.get_url_event () + "?" + pi_prm
		try:
			p_ss	= requests.session ()
			p_res = p_ss.get (p_url, stream=True)
			for p_rec in p_res.iter_lines ():
				pi_cbp (p_rec.decode ('ascii'))

		except	KeyboardInterrupt:
			print	("info, Ctrl^C interrupt.")

		except	Exception as p_exception:
			print   (f'error, exception catched:[{p_exception}]')


#!
#!	define e_api event i/f (websocket) class.
#!
class	e_api_evt_ws:
	def	__init__(self, pi_api):
		self.p_api	= pi_api

	#!
	#!	request to e_api server.
	#!
	def	evt_server (self, pi_prm, pi_cbp):
		import	asyncio
		import	websockets

		async	def	proc_event_websocket (pi_url):
			async with websockets.connect (pi_url, ping_interval=86400, ping_timeout=10) as websocket:
				while True:
					pi_cbp (await websocket.recv ())

		p_url	= self.p_api.get_url_event_websocket () + "?" + pi_prm
		try:
			asyncio.run (proc_event_websocket (p_url))

		except	KeyboardInterrupt:
			print	("info, Ctrl^C interrupt.")

		except	websockets.exceptions.ConnectionClosedOK:
			print	("warn, connection closed.")

		except	Exception as p_exception:
			print   (f'error, exception catched:[{p_exception}]')


#!
#!	parse and print event i/f receive data.
#!
def	proc_print_event_if_data (pi_data):
	print	("receive-data-all:[" + pi_data + "]")
	pa_rec	= pi_data.split ('\x01')
	for p_rec in pa_rec:
		if (p_rec):
			pa_colval = p_rec.split ('\x02')
			print ("receive-data-parse, col:[" + pa_colval[0] + "], val:[" + pa_colval[1] + "]")
	print	("####")
	return (0)


#!
#!	main program.
#!
if __name__ == "__main__":
	#!
	#!	create e_api class object.
	#!
	p_api	= e_api_req_if ()

	#!
	#!	url-encode symbols in password.
	#!
	gp_pswd	=  urllib.parse.quote (gp_pswd)

	#!
	#!	auth i/f.
	#!
	p_sts	= p_api.req_or_file_login (gp_url_auth, gp_usid, gp_pswd)
	if p_sts != 0:
		exit (p_sts)

	#!
	#!	request i/f.
	#!
	if gp_proc_request_if:
		#!
		#!	get market price.
		#!
		p_ans	= p_api.req_market_price ("101,6501,7201", "pDPP,pDV")
		p_sts	= p_api.ans_check (p_ans)
		if p_sts != 0:
			exit (p_sts)
		else:
			print	(p_ans);

	#!
	#!	event i/f http.
	#!
	p_prm	= "p_rid=22&p_board_no=1000&p_gyou_no=1,2,3&p_issue_code=6501,7203,8411&p_mkt_code=00,00,00&p_eno=0&p_evt_cmd=ST,KP,FD"
	if gp_proc_event_if:
		p_evt	= e_api_evt_if (p_api)
		p_evt.evt_server (p_prm, proc_print_event_if_data)

	#!
	#!	event i/f websocket.
	#!
	if gp_proc_event_ws:
		p_evt	= e_api_evt_ws (p_api)
		p_evt.evt_server (p_prm, proc_print_event_if_data)

	#!
	#!	success complete.
	#!
	exit (0)

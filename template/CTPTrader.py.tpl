# -*- coding: utf-8 -*-
import os
import zmq
import json
import uuid
import tempfile
import subprocess
from CTPStruct import *
from message import *
from time import sleep
from datetime import datetime,timedelta


def packageReqInfo(apiName,data):
	'''
	获取一个默认的调用结构
	'''
	reqInfo = {}
	reqInfo['RequestMethod'] = apiName
	parameters = {}
	reqInfo['Parameters'] = parameters
	parameters['Data'] = data
	return reqInfo

# 定义通用的出错返回数据
InvalidRequestFormat = [-2000,u'参数表单类型不正确',[]]
ResponseTimeOut = [-2001,u'请求超时未响应',[]]
InvalidMessageFormat = [-2002,u'接收到异常消息格式',[]]
def FailToInsertOrder(statusMsg):
	return [-2003,u'报单失败:%s' % statusMsg,[]]

#def mallocIpcAddress():
#	return 'ipc://%s/%s' % (tempfile.gettempdir(),uuid.uuid1())


def mallocIpcAddress():
	return 'ipc://%s' % tempfile.mktemp(suffix='.ipc',prefix='tmp_')



class TraderChannel :
	'''
	Trader通讯管道类,该类通过和CTPConverter的Trader进程通讯,对外实现python语言封装的CTP接口,
	在设计上该类既支持同步接口也支持异步接口,但是目前暂时先实现同步接口.
	'''

	def __testChannel(self):
		'''
		检查ctp交易通道是否运行正常，该方法在构造函数内调用如果失败，构造函数会抛出异常
		成功返回True，失败返回False
		'''
		data = CThostFtdcQryTradingAccountField()
		result = self.QryTradingAccount(data)
		return result[0] == 0


	def __delTraderProcess(self):
		'''
		清除trader转换器进程
		'''

		if hasattr(self, 'traderProcess'):
			self.traderProcess.kill()
			self.traderProcess.wait()
			del self.traderProcess


	def __init__(self,frontAddress,brokerID,userID,password,
		queryInterval=1,timeout=10,converterQueryInterval=None):
		'''
		初始化过程:
		1.创建ctp转换器进程
		2.创建和ctp通讯进程的通讯管道
		3.测试ctp连接是否正常
		如果ctp连接测试失败，将抛出异常阻止对象的创建
		参数:
		frontAddress   ctp服务器地址
		brokerID   代理商编号
		userID   用户编号
		password   密码
		queryInterval  查询间隔时间(单位:秒)
		timeout 等待响应时间(单位:秒)
		converterQueryInterval 转换器的流量控制时间间隔(单位:秒),如果为None默认取queryInterval
		'''
		# 创建临时工作目录
		self.workdir = tempfile.mkdtemp()

		# 设置上次查询时间
		self.queryInterval = queryInterval
		# NOTE:虽然这里之前没有ctp query请求,仍然要预留等待时间,是由于启动转化器进程是需要时
		# 间的,转化器此时还无法响应请求,而初始化过程马上就发出一个查询请,以测试通道是否通畅,
		# 该请求会在zmq队列中排队,排队时间也是计时的.而ctp流量控制计算的是发向服务器的时间,
		# 是不是送到zmq消息队列的时间.所以这里要考虑ctp trader转换器的时间这里暂定为1秒
		traderProcessStartupTime = 1.5
		self.lastQueryTime = datetime.now() - timedelta(seconds=queryInterval)
		self.lastQueryTime +=  timedelta(seconds=traderProcessStartupTime)

		self.queryIntervalMillisecond = int(queryInterval * 1000)
		if converterQueryInterval == None :
			converterQueryInterval = queryInterval
		self.converterQueryIntervalMillisecond = int(converterQueryInterval * 1000)

		# 为ctp转换器分配通讯管道地址
		self.requestPipe = mallocIpcAddress()
		self.pushbackPipe = mallocIpcAddress()
		self.publishPipe = mallocIpcAddress()

		# 构造调用命令
		commandLine = ['trader',
		'--FrontAddress',frontAddress,
		'--BrokerID',brokerID,
		'--UserID',userID,
		'--Password', password,
		'--RequestPipe', self.requestPipe,
		'--PushbackPipe', self.pushbackPipe,
		'--PublishPipe', self.publishPipe,
		'--loyalty',
		'--queryInterval',str(self.converterQueryIntervalMillisecond)
		]

		# 创建转换器子进程
		fileOutput = os.path.join(self.workdir,'trader.log')
		traderStdout = open(fileOutput, 'w')
		#self.traderProcess = subprocess.Popen(commandLine,stdout=traderStdout)
		self.traderProcess = subprocess.Popen(commandLine,stdout=traderStdout,cwd=self.workdir)

		# 创建zmq通讯环境
		context = zmq.Context()
		self.context = context
		self.timeoutMillisecond = 1000 * timeout

		# 创建请求通讯通道
		request = context.socket(zmq.DEALER)
		request.connect(self.requestPipe)
		request.setsockopt(zmq.LINGER,0)
		self.request = request

		# 创建接收广播消息的管道
		publish = context.socket(zmq.SUB)
		publish.connect(self.publishPipe)
		publish.setsockopt_string(zmq.SUBSCRIBE,u'')
		self.publish = publish

		# 检查ctp通道是否建立，如果失败抛出异常
		if not self.__testChannel():
			self.__delTraderProcess()
			raise Exception('无法建立ctp连接,具体错误请查看ctp转换器的日志信息')
			#raise Exception('''can't not connect to ctp server.''')


	def __enter__(self):
		''' 让TraderChannel可以使用with语句 '''
		#print '__enter__():被调用'
		return self


	def __exit__(self, type, value, tb):
		''' 让TraderChannel可以使用with语句 '''
		#print '__exit__():被调用',type,value,tb
		pass


	def __del__(self):
		'''
		对象移出过程
		1.结束ctp转换器进程
		'''
		self.__delTraderProcess()



	def getQueryWaitTime(self):
		'''
		获取查询需要等待的时间
		'''
		needWait = self.queryInterval - (datetime.now() - self.lastQueryTime).total_seconds()
		if needWait > 0 :
			return needWait
		else:
			return 0


	def queryWait(self):
		'''
		查询前的等待,如果需要的话
		'''
		sleep(self.getQueryWaitTime())


	{# 交易结果确认方法 #}
	{% set method = reqMethodDict['ReqSettlementInfoConfirm'] %}
	{% include 'ReqMethod.tpl' %}


	{% set method = reqMethodDict['ReqOrderInsert'] %}
	{% set parameter = method['parameters'][0]  %}
	def {{ method['name'][3:]}}(self,data):
		'''
		{{ method['remark'][3:] }}
		data 调用api需要填写参数表单,类型为{{parameter['raw_type']}},具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		注意:同步调用没有metaData参数,因为没有意义
		'''
		if not isinstance(data,{{parameter['raw_type']}}):
			return InvalidRequestFormat

		request = self.request
		publish = self.publish
		timeoutMillisecond = self.timeoutMillisecond

		{% set returnApiName = 'OnRtnOrder' %}
		{% set resultApiName = 'OnRtnTrade' %}
		{% set errReturnApiName = 'OnErrRtnOrderInsert' %}
		requestApiName = 'Req{{method['name'][3:]}}'
		responseApiName = 'OnRsp{{method['name'][3:]}}'
		returnApiName = '{{returnApiName}}'
		resultApiName = '{{resultApiName}}'
		errReturnApiName = '{{errReturnApiName}}'

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(request)

		# 等待RequestID响应
		poller = zmq.Poller()
		poller.register(request, zmq.POLLIN)
		sockets = dict(poller.poll(timeoutMillisecond))
		if not (request in sockets) :
			return ResponseTimeOut
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(request)


		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat


		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],[]

		while True:
			poller = zmq.Poller()
			poller.register(request, zmq.POLLIN)
			poller.register(publish, zmq.POLLIN)
			sockets = dict(poller.poll(timeoutMillisecond))

			# 判断是否超时
			if request not in sockets and publish not in sockets :
				return ResponseTimeOut

			if request in sockets:
				# 此时如果接受到Response消息说明参数错误，ctp接口立即返回了
				# 从request通讯管道读取返回信息
				responseMessage = ResponseMessage()
				responseMessage.recv(request)

				# 检查接收消息格式是否正确
				c1 = responseMessage.header == 'RESPONSE'
				c2 = responseMessage.requestID == requestIDMessage.requestID
				c3 = responseMessage.apiName in responseApiName
				if not (c1 and c2 and c3) :
					return InvalidMessageFormat

				# 读取返回信息并检查
				respInfo = json.loads(responseMessage.respInfo)
				errorID = respInfo['Parameters']['RspInfo']['ErrorID']
				errorMsg = respInfo['Parameters']['RspInfo']['ErrorMsg']

				# 这里应该收到的是一个错误信息
				if errorID == 0 :
					return InvalidMessageFormat

				# 这里还会收到一条OnRtnError消息,将尝试接受它
				poller = zmq.Poller()
				poller.register(publish, zmq.POLLIN)
				sockets = dict(poller.poll(timeoutMillisecond))
				if publish not in sockets:
					return ResponseTimeOut

				# 接受消息避免扰乱下一次调用
				publishMessage = PublishMessage()
				publishMessage.recv(publish)
				c1 =  publishMessage.header == 'PUBLISH'
				c2 =  publishMessage.apiName == errReturnApiName
				if not ( c1 and c2 ):
					return InvalidMessageFormat

				# 将出错信息返回调用者
				return errorID,errorMsg,[]

			if publish in sockets:
				# 如果接受到Publish消息说明已经收到了订单提交的信息
				publishMessage = PublishMessage()
				publishMessage.recv(publish)
				c1 =  publishMessage.header == 'PUBLISH'
				c2 =  publishMessage.apiName == returnApiName
				if not ( c1 and c2 ):
					return InvalidMessageFormat

				# 读取返回信息
				respInfo = json.loads(publishMessage.respInfo)
				responseDataDict = respInfo['Parameters']['Data']
				orderSubmitStatus = responseDataDict['OrderSubmitStatus']
				orderStatus = responseDataDict['OrderStatus']
				statusMsg = responseDataDict['StatusMsg']

				# OrderSubmitStatus = '0' 订单已提交
				# OrderStatus = 'a' 未知状态
				# 如果只是收到订单处理回报,但订单状态没有变化,应该继续等待下一条回报信息,直
				# 到订单状态发生了变化
				if orderSubmitStatus != '0' or  orderStatus != 'a':
					break

		# 订单状态已经变化,说明系统已经处理完毕,检查处理结果
		# 如果出错返回出错信息
		c1 = orderSubmitStatus == '0'   #已经提交
		c2 = orderStatus == '0'   # 全部成交
		if not ( c1 and c2 ) :
			return FailToInsertOrder(statusMsg)

		# 到了这里说明订单已经提交成功了,读取成交记录信息
		poller = zmq.Poller()
		poller.register(publish, zmq.POLLIN)
		sockets = dict(poller.poll(timeoutMillisecond))
		if publish not in sockets:
			return ResponseTimeOut

		publishMessage = PublishMessage()
		publishMessage.recv(publish)
		c1 = publishMessage.header == 'PUBLISH'
		c2 = publishMessage.apiName == resultApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 读取返回数据并返回
		respInfo = json.loads(publishMessage.respInfo)
		responseDataDict = respInfo['Parameters']['Data']
		{% set resultApiMethod = onRtnMethodDict[resultApiName] %}
		{% set responseDataType = resultApiMethod['parameters'][0]['raw_type']%}
		responseData = {{responseDataType}}(**responseDataDict)
		return 0,u'',[responseData]


{# 所有查询api的实现 #}
{% for method in reqMethodDict.itervalues() %}
{% if method['name'][3:6] == 'Qry' or method['name'][3:8] == 'Query' %}
	{% include 'ReqMethod.tpl' %}
{% endif %}
{% endfor %}

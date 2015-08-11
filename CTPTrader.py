# -*- coding: utf-8 -*-
import os
import zmq
import json
import uuid
import tempfile
import subprocess
from CTPStruct import *
from time import sleep
from datetime import datetime,timedelta
from ErrorResult import *

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

#def mallocIpcAddress():
#	return 'ipc://%s/%s' % (tempfile.gettempdir(),uuid.uuid1())
def mallocIpcAddress():
	return 'ipc://%s' % tempfile.mktemp(suffix='.ipc',prefix='tmp_')

class Trader :
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
        self.responsePipe = mallocIpcAddress()
        self.pushbackPipe = mallocIpcAddress()
        self.publishPipe = mallocIpcAddress()

		# 构造调用命令
        commandLine = ['trader',
        '--FrontAddress',frontAddress,
		'--BrokerID',brokerID,
		'--UserID',userID,
		'--Password', password,
		'--RequestPipe', self.requestPipe,
        '--ResponsePipe',self.responsePipe,
		'--PushbackPipe', self.pushbackPipe,
		'--PublishPipe', self.publishPipe,
		'--loyalty',
		'--queryInterval',str(self.converterQueryIntervalMillisecond)
		]

		# 创建转换器子进程
        fileOutput = os.path.join(self.workdir,'trader.log')
        traderStdout = open(fileOutput, 'w')
        #self.traderProcess = subprocess.Popen(commandLine,stdout=traderStdout)
        print 'self.workdir =',self.workdir
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

        # 创建请求通讯通道
        response = context.socket(zmq.DEALER)
        response.connect(self.responsePipe)
        response.setsockopt(zmq.LINGER,0)
        self.response = response

        # 创建接收广播消息的管道
        publish = context.socket(zmq.SUB)
        publish.connect(self.publishPipe)
        publish.setsockopt_string(zmq.SUBSCRIBE,u'')
        self.publish = publish

        # 检查ctp通道是否建立，如果失败抛出异常
        # if not self.__testChannel():
            # self.__delTraderProcess()
            # raise Exception('无法建立ctp连接,具体错误请查看ctp转换器的日志信息')
            # #raise Exception('''can't not connect to ctp server.''')


    def __enter__(self):
        ''' 让Trader可以使用with语句 '''
        #print '__enter__():被调用'
        return self


    def __exit__(self, type, value, tb):
        ''' 让Trader可以使用with语句 '''
        #print '__exit__():被调用',type,value,tb
        pass


    def __del__(self):
        '''
        对象移出过程
        1.结束ctp转换器进程
        '''
        self.__delTraderProcess()




    	
	def QryTradingAccount(self,data):
		'''
		请求查询资金账户
		data 调用api需要填写参数表单,类型为CThostFtdcQryTradingAccountField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQryTradingAccountField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QryCFMMCTradingAccountKey(self,data):
		'''
		请求查询保证金监管系统经纪公司资金账户密钥
		data 调用api需要填写参数表单,类型为CThostFtdcQryCFMMCTradingAccountKeyField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQryCFMMCTradingAccountKeyField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def UserPasswordUpdate(self,data):
		'''
		用户口令更新请求
		data 调用api需要填写参数表单,类型为CThostFtdcUserPasswordUpdateField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcUserPasswordUpdateField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QryTradingNotice(self,data):
		'''
		请求查询交易通知
		data 调用api需要填写参数表单,类型为CThostFtdcQryTradingNoticeField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQryTradingNoticeField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QryTrade(self,data):
		'''
		请求查询成交
		data 调用api需要填写参数表单,类型为CThostFtdcQryTradeField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQryTradeField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QueryMaxOrderVolume(self,data):
		'''
		查询最大报单数量请求
		data 调用api需要填写参数表单,类型为CThostFtdcQueryMaxOrderVolumeField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQueryMaxOrderVolumeField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def SettlementInfoConfirm(self,data):
		'''
		投资者结算结果确认
		data 调用api需要填写参数表单,类型为CThostFtdcSettlementInfoConfirmField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcSettlementInfoConfirmField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QryInvestorPosition(self,data):
		'''
		请求查询投资者持仓
		data 调用api需要填写参数表单,类型为CThostFtdcQryInvestorPositionField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQryInvestorPositionField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QryBrokerTradingAlgos(self,data):
		'''
		请求查询经纪公司交易算法
		data 调用api需要填写参数表单,类型为CThostFtdcQryBrokerTradingAlgosField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQryBrokerTradingAlgosField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QryOrder(self,data):
		'''
		请求查询报单
		data 调用api需要填写参数表单,类型为CThostFtdcQryOrderField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQryOrderField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QryExchange(self,data):
		'''
		请求查询交易所
		data 调用api需要填写参数表单,类型为CThostFtdcQryExchangeField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQryExchangeField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def UserLogin(self,data):
		'''
		用户登录请求
		data 调用api需要填写参数表单,类型为CThostFtdcReqUserLoginField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcReqUserLoginField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def FromFutureToBankByFuture(self,data):
		'''
		期货发起期货资金转银行请求
		data 调用api需要填写参数表单,类型为CThostFtdcReqTransferField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcReqTransferField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QryExchangeRate(self,data):
		'''
		请求查询汇率
		data 调用api需要填写参数表单,类型为CThostFtdcQryExchangeRateField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQryExchangeRateField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QryInvestorPositionDetail(self,data):
		'''
		请求查询投资者持仓明细
		data 调用api需要填写参数表单,类型为CThostFtdcQryInvestorPositionDetailField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQryInvestorPositionDetailField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QrySettlementInfoConfirm(self,data):
		'''
		请求查询结算信息确认
		data 调用api需要填写参数表单,类型为CThostFtdcQrySettlementInfoConfirmField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQrySettlementInfoConfirmField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QryBrokerTradingParams(self,data):
		'''
		请求查询经纪公司交易参数
		data 调用api需要填写参数表单,类型为CThostFtdcQryBrokerTradingParamsField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQryBrokerTradingParamsField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QueryCFMMCTradingAccountToken(self,data):
		'''
		请求查询监控中心用户令牌
		data 调用api需要填写参数表单,类型为CThostFtdcQueryCFMMCTradingAccountTokenField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQueryCFMMCTradingAccountTokenField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QryNotice(self,data):
		'''
		请求查询客户通知
		data 调用api需要填写参数表单,类型为CThostFtdcQryNoticeField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQryNoticeField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def FromBankToFutureByFuture(self,data):
		'''
		期货发起银行资金转期货请求
		data 调用api需要填写参数表单,类型为CThostFtdcReqTransferField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcReqTransferField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def ParkedOrderInsert(self,data):
		'''
		预埋单录入请求
		data 调用api需要填写参数表单,类型为CThostFtdcParkedOrderField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcParkedOrderField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QryInvestorPositionCombineDetail(self,data):
		'''
		请求查询投资者持仓明细
		data 调用api需要填写参数表单,类型为CThostFtdcQryInvestorPositionCombineDetailField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQryInvestorPositionCombineDetailField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def OrderInsert(self,data):
		'''
		报单录入请求
		data 调用api需要填写参数表单,类型为CThostFtdcInputOrderField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcInputOrderField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QrySecAgentACIDMap(self,data):
		'''
		请求查询二级代理操作员银期权限
		data 调用api需要填写参数表单,类型为CThostFtdcQrySecAgentACIDMapField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQrySecAgentACIDMapField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def ParkedOrderAction(self,data):
		'''
		预埋撤单录入请求
		data 调用api需要填写参数表单,类型为CThostFtdcParkedOrderActionField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcParkedOrderActionField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QueryBankAccountMoneyByFuture(self,data):
		'''
		期货发起查询银行余额请求
		data 调用api需要填写参数表单,类型为CThostFtdcReqQueryAccountField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcReqQueryAccountField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QryParkedOrderAction(self,data):
		'''
		请求查询预埋撤单
		data 调用api需要填写参数表单,类型为CThostFtdcQryParkedOrderActionField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQryParkedOrderActionField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def Authenticate(self,data):
		'''
		客户端认证请求
		data 调用api需要填写参数表单,类型为CThostFtdcReqAuthenticateField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcReqAuthenticateField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QryExchangeMarginRate(self,data):
		'''
		请求查询交易所保证金率
		data 调用api需要填写参数表单,类型为CThostFtdcQryExchangeMarginRateField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQryExchangeMarginRateField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def TradingAccountPasswordUpdate(self,data):
		'''
		资金账户口令更新请求
		data 调用api需要填写参数表单,类型为CThostFtdcTradingAccountPasswordUpdateField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcTradingAccountPasswordUpdateField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def UserLogout(self,data):
		'''
		登出请求
		data 调用api需要填写参数表单,类型为CThostFtdcUserLogoutField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcUserLogoutField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QryInstrument(self,data):
		'''
		请求查询合约
		data 调用api需要填写参数表单,类型为CThostFtdcQryInstrumentField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQryInstrumentField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def OrderAction(self,data):
		'''
		报单操作请求
		data 调用api需要填写参数表单,类型为CThostFtdcInputOrderActionField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcInputOrderActionField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QryInstrumentCommissionRate(self,data):
		'''
		请求查询合约手续费率
		data 调用api需要填写参数表单,类型为CThostFtdcQryInstrumentCommissionRateField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQryInstrumentCommissionRateField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QryInstrumentMarginRate(self,data):
		'''
		请求查询合约保证金率
		data 调用api需要填写参数表单,类型为CThostFtdcQryInstrumentMarginRateField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQryInstrumentMarginRateField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QryInvestor(self,data):
		'''
		请求查询投资者
		data 调用api需要填写参数表单,类型为CThostFtdcQryInvestorField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQryInvestorField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QryExchangeMarginRateAdjust(self,data):
		'''
		请求查询交易所调整保证金率
		data 调用api需要填写参数表单,类型为CThostFtdcQryExchangeMarginRateAdjustField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQryExchangeMarginRateAdjustField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QryInvestorProductGroupMargin(self,data):
		'''
		请求查询投资者品种/跨品种保证金
		data 调用api需要填写参数表单,类型为CThostFtdcQryInvestorProductGroupMarginField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQryInvestorProductGroupMarginField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QryEWarrantOffset(self,data):
		'''
		请求查询仓单折抵信息
		data 调用api需要填写参数表单,类型为CThostFtdcQryEWarrantOffsetField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQryEWarrantOffsetField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QryDepthMarketData(self,data):
		'''
		请求查询行情
		data 调用api需要填写参数表单,类型为CThostFtdcQryDepthMarketDataField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQryDepthMarketDataField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QryTransferBank(self,data):
		'''
		请求查询转帐银行
		data 调用api需要填写参数表单,类型为CThostFtdcQryTransferBankField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQryTransferBankField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def RemoveParkedOrderAction(self,data):
		'''
		请求删除预埋撤单
		data 调用api需要填写参数表单,类型为CThostFtdcRemoveParkedOrderActionField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcRemoveParkedOrderActionField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QryProduct(self,data):
		'''
		请求查询产品
		data 调用api需要填写参数表单,类型为CThostFtdcQryProductField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQryProductField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QryTradingCode(self,data):
		'''
		请求查询交易编码
		data 调用api需要填写参数表单,类型为CThostFtdcQryTradingCodeField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQryTradingCodeField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QrySettlementInfo(self,data):
		'''
		请求查询投资者结算结果
		data 调用api需要填写参数表单,类型为CThostFtdcQrySettlementInfoField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQrySettlementInfoField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QryAccountregister(self,data):
		'''
		请求查询银期签约关系
		data 调用api需要填写参数表单,类型为CThostFtdcQryAccountregisterField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQryAccountregisterField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QryParkedOrder(self,data):
		'''
		请求查询预埋单
		data 调用api需要填写参数表单,类型为CThostFtdcQryParkedOrderField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQryParkedOrderField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QryTransferSerial(self,data):
		'''
		请求查询转帐流水
		data 调用api需要填写参数表单,类型为CThostFtdcQryTransferSerialField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQryTransferSerialField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def QryContractBank(self,data):
		'''
		请求查询签约银行
		data 调用api需要填写参数表单,类型为CThostFtdcQryContractBankField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcQryContractBankField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID


    	
	def RemoveParkedOrder(self,data):
		'''
		请求删除预埋单
		data 调用api需要填写参数表单,类型为CThostFtdcRemoveParkedOrderField,具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		'''

        # 检查表单数据的类型是否正确
		if not isinstance(data,CThostFtdcRemoveParkedOrderField):
			return InvalidRequestFormat

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		# 发送到服务器
		requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)
		sockets = dict(poller.poll(self.timeoutMillisecond))
		if not (self.request in sockets) :
			return ResponseTimeOut

		# 从request通讯管道读取返回信息
		requestIDMessage = RequestIDMessage()
		requestIDMessage.recv(self.request)

		# 检查接收的消息格式
		c1 = requestIDMessage.header == 'REQUESTID'
		c2 = requestIDMessage.apiName == requestApiName
		if not ( c1 and c2 ):
			return InvalidMessageFormat

		# 如果没有收到RequestID,返回转换器的出错信息
		if not (int(requestIDMessage.requestID) > 0):
			errorInfo = json.loads(requestIDMessage.errorInfo)
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

		# 返回成功
		return 0,'',requestIDMessage.requestID



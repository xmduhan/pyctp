# -*- coding: utf-8 -*-
import os
import zmq
import json
import uuid
import tempfile
import subprocess
import threading
from CTPStruct import *
from time import sleep
from datetime import datetime,timedelta
from ErrorResult import *
import json
from message import *

def packageReqInfo(apiName,data):
	"""
	获取一个默认的调用结构
	"""
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
    """
    Trader通讯管道类,该类通过和CTPConverter的Trader进程通讯,对外实现python语言封装的CTP接口,
    """

    def __testChannel(self):
        """
        检查ctp交易通道是否运行正常，该方法在构造函数内调用如果失败，构造函数会抛出异常
        成功返回True，失败返回False
        """
        data = CThostFtdcQryTradingAccountField()
        result = self.QryTradingAccount(data)
        return result[0] == 0


    def __delTraderProcess(self):
        """
        清除trader转换器进程
        """

        if hasattr(self, 'traderProcess'):
            self.traderProcess.kill()
            self.traderProcess.wait()
            del self.traderProcess


    def __init__(self,frontAddress,brokerID,userID,password,
        timeout=10,converterQueryInterval=1):
        """
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
        converterQueryInterval 转换器的流量控制时间间隔(单位:秒),默认为1
        """
        # 创建临时工作目录
        self.workdir = tempfile.mkdtemp()

        # 为ctp转换器分配通讯管道地址
        self.requestPipe = mallocIpcAddress()
        self.responsePipe = mallocIpcAddress()
        self.pushbackPipe = mallocIpcAddress()
        self.publishPipe = mallocIpcAddress()
        self.threadControlPipe = mallocIpcAddress()
        identity = str(uuid.uuid1())

        # 设置等待超时时间
        self.timeoutMillisecond = 1000 * timeout

        # 设置转化器查询请求间隔
        self.converterQueryIntervalMillisecond = converterQueryInterval * 1000

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
        self.traderProcess = subprocess.Popen(commandLine,stdout=traderStdout,cwd=self.workdir)

		# 创建zmq通讯环境
        context = zmq.Context()
        self.context = context

		# 创建请求通讯通道
        request = context.socket(zmq.DEALER)
        request.setsockopt(zmq.IDENTITY,identity)
        request.connect(self.requestPipe)
        request.setsockopt(zmq.LINGER,0)
        self.request = request

        # 创建请求通讯通道
        response = context.socket(zmq.DEALER)
        response.setsockopt(zmq.IDENTITY,identity)
        response.connect(self.responsePipe)
        response.setsockopt(zmq.LINGER,0)
        self.response = response

        # 创建接收广播消息的管道
        publish = context.socket(zmq.SUB)
        publish.connect(self.publishPipe)
        publish.setsockopt_string(zmq.SUBSCRIBE,u'')
        self.publish = publish

        # 创建线程控制通讯管道
        threadRequest = context.socket(zmq.DEALER)
        threadRequest.connect(self.threadControlPipe)
        threadRequest.setsockopt(zmq.LINGER,0)
        self.threadRequest = threadRequest
        threadResponse = context.socket(zmq.ROUTER)
        threadResponse.bind(self.threadControlPipe)
        self.threadResponse = threadResponse

        # 回调数据链
        self._callbackDict = {}
        self._callbackUuidDict = {}
        self._callbackLock = threading.RLock()

        # 启动工作线程
        thread = threading.Thread(target=self._threadFunction)
        thread.daemon = True
        thread.start()


    def __enter__(self):
        """ 让Trader可以使用with语句 """
        #print '__enter__():被调用'
        return self


    def __exit__(self, type, value, tb):
        """ 让Trader可以使用with语句 """
        #print '__exit__():被调用',type,value,tb
        pass


    def __del__(self):
        """
        对象移除过程
        1.结束ctp转换器进程
        """
        #print '__del__():被调用'
        self.__delTraderProcess()


    def bind(self,callbackName,funcToCall):
        """
        绑定回调函数
        参数:
        callbackName  回调函数名称，具体可用项在pyctp.callback模块中定义
        funcToCall  需要绑定的回调函数，可以是函数也可以是实例方法
        回调方法必须定义成以下结构:
        def funcToCall(**kargs)
        返回值:
        如果绑定成功方法返回一个bindId,这个id可以用于解除绑定(unbind)时使用
        """
        self._callbackLock.acquire()
        try:
            callbackUuid = uuid.uuid1()
            self._callbackUuidDict[callbackUuid] = {
                'callbackName':callbackName,
                'funcToCall' : funcToCall
            }
            if callbackName in self._callbackDict.keys():
                self._callbackDict[callbackName].append(callbackUuid)
            else:
                self._callbackDict[callbackName] = [callbackUuid]
            return callbackUuid
        finally:
            self._callbackLock.release()


    def unbind(self,bindId):
        """
        解除回调函数的绑定
        参数:
        bindId 绑定回调函数时的返回值
        返回值:
        成功返回True，失败(或没有找到绑定项)返回False
        """
        self._callbackLock.acquire()
        try:
            if bindId not in self._callbackUuidDict.keys():
                return False
            callbackName = self._callbackUuidDict[bindId]['callbackName']
            self._callbackDict[callbackName].remove(bindId)
            self._callbackUuidDict.pop(bindId)
            return True
        finally:
            self._callbackLock.release()


    def _callback(self,callbackName,args):
        """
        根据回调链调用已经绑定的所有回调函数，该函数主要提供给监听简称使用
        参数:
        callbackName  回调函数名称
        args 用于传递给回调函数的参数(字典结构)
        返回值:
        无
        """
        self._callbackLock.acquire()
        try:
            if callbackName not in self._callbackDict.keys():
                return
            for callbackUuid in self._callbackDict[callbackName]:
                funcToCall = self._callbackUuidDict[callbackUuid]['funcToCall']
                try:
                    funcToCall(**args)
                except Exception as e:
                    print e
        finally:
            self._callbackLock.release()


    def _sendToThread(self,messageList):
        """
        向线程发送一条命令消息
        参数:
        messageList  要发送的消息列表
        返回值:无
        """
        self.threadRequest.send_multipart(messageList)


    def _recvFromThread(self,timeout=1000):
        """
        读取监听线程的返回消息
        参数:
        timeout  如果没有消息等待的时间(单位:毫秒)
        返回值:
        如果无法收到消息返回None,否则返回消息列表
        """
        poller = zmq.Poller()
        poller.register(self.threadRequest, zmq.POLLIN)
        sockets = dict(poller.poll(timeout))
        if self.threadRequest not in sockets:
            return None
        return self.threadRequest.recv_multipart()



    def _threadFunction(self):
        """
        监听线程的方法
        """
        while True:
            try:
                # 等待消息
                poller = zmq.Poller()
                poller.register(self.response, zmq.POLLIN)
                poller.register(self.publish, zmq.POLLIN)
                poller.register(self.threadResponse, zmq.POLLIN)
                sockets = dict(poller.poll())

                if self.threadResponse in sockets:
                    # 接收到来自进程的命令
                    messageList = self.threadResponse.recv_multipart()
                    if messageList[1] == 'exit':
                        return
                    if messageList[1] == 'echo':
                        if len(messageList) >= 3:
                            toSendBack = messageList[2]
                        else:
                            toSendBack = ''
                        self.threadResponse.send_multipart([messageList[0],toSendBack])
                    continue

                # 循环读取消息进程回调处理
                for socket in sockets:
                    # 读取消息
                    messageList = socket.recv_multipart()
                    # 根据不同的消息类型提取回调名称和参数信息
                    if messageList[0] == 'RESPONSE':
                        apiName, respInfoJson = messageList[2:4]

                    elif messageList[0] == 'PUBLISH':
                        apiName, respInfoJson = messageList[1:3]
                    else:
                        print u'接收到1条未知消息...'
                        continue

                    respInfo = json.loads(respInfoJson)
                    parameters = respInfo['Parameters']

                    # 调用对应的回调函数
                    self._callback(apiName,parameters)
            except Exception as e:
                print e

        print u'监听线程退出...'


    def ReqQryTradingAccount(self,data):
        """
        请求查询资金账户
        data 调用api需要填写参数表单,类型为CThostFtdcQryTradingAccountField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQryTradingAccount'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQryCFMMCTradingAccountKey(self,data):
        """
        请求查询保证金监管系统经纪公司资金账户密钥
        data 调用api需要填写参数表单,类型为CThostFtdcQryCFMMCTradingAccountKeyField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQryCFMMCTradingAccountKey'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqUserPasswordUpdate(self,data):
        """
        用户口令更新请求
        data 调用api需要填写参数表单,类型为CThostFtdcUserPasswordUpdateField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqUserPasswordUpdate'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQryTradingNotice(self,data):
        """
        请求查询交易通知
        data 调用api需要填写参数表单,类型为CThostFtdcQryTradingNoticeField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQryTradingNotice'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQryTrade(self,data):
        """
        请求查询成交
        data 调用api需要填写参数表单,类型为CThostFtdcQryTradeField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQryTrade'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQueryMaxOrderVolume(self,data):
        """
        查询最大报单数量请求
        data 调用api需要填写参数表单,类型为CThostFtdcQueryMaxOrderVolumeField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQueryMaxOrderVolume'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqSettlementInfoConfirm(self,data):
        """
        投资者结算结果确认
        data 调用api需要填写参数表单,类型为CThostFtdcSettlementInfoConfirmField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqSettlementInfoConfirm'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQryInvestorPosition(self,data):
        """
        请求查询投资者持仓
        data 调用api需要填写参数表单,类型为CThostFtdcQryInvestorPositionField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQryInvestorPosition'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQryBrokerTradingAlgos(self,data):
        """
        请求查询经纪公司交易算法
        data 调用api需要填写参数表单,类型为CThostFtdcQryBrokerTradingAlgosField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQryBrokerTradingAlgos'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQryOrder(self,data):
        """
        请求查询报单
        data 调用api需要填写参数表单,类型为CThostFtdcQryOrderField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQryOrder'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQryExchange(self,data):
        """
        请求查询交易所
        data 调用api需要填写参数表单,类型为CThostFtdcQryExchangeField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQryExchange'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqUserLogin(self,data):
        """
        用户登录请求
        data 调用api需要填写参数表单,类型为CThostFtdcReqUserLoginField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqUserLogin'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqFromFutureToBankByFuture(self,data):
        """
        期货发起期货资金转银行请求
        data 调用api需要填写参数表单,类型为CThostFtdcReqTransferField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqFromFutureToBankByFuture'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQryExchangeRate(self,data):
        """
        请求查询汇率
        data 调用api需要填写参数表单,类型为CThostFtdcQryExchangeRateField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQryExchangeRate'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQryInvestorPositionDetail(self,data):
        """
        请求查询投资者持仓明细
        data 调用api需要填写参数表单,类型为CThostFtdcQryInvestorPositionDetailField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQryInvestorPositionDetail'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQrySettlementInfoConfirm(self,data):
        """
        请求查询结算信息确认
        data 调用api需要填写参数表单,类型为CThostFtdcQrySettlementInfoConfirmField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQrySettlementInfoConfirm'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQryBrokerTradingParams(self,data):
        """
        请求查询经纪公司交易参数
        data 调用api需要填写参数表单,类型为CThostFtdcQryBrokerTradingParamsField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQryBrokerTradingParams'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQueryCFMMCTradingAccountToken(self,data):
        """
        请求查询监控中心用户令牌
        data 调用api需要填写参数表单,类型为CThostFtdcQueryCFMMCTradingAccountTokenField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQueryCFMMCTradingAccountToken'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQryNotice(self,data):
        """
        请求查询客户通知
        data 调用api需要填写参数表单,类型为CThostFtdcQryNoticeField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQryNotice'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqFromBankToFutureByFuture(self,data):
        """
        期货发起银行资金转期货请求
        data 调用api需要填写参数表单,类型为CThostFtdcReqTransferField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqFromBankToFutureByFuture'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqParkedOrderInsert(self,data):
        """
        预埋单录入请求
        data 调用api需要填写参数表单,类型为CThostFtdcParkedOrderField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqParkedOrderInsert'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQryInvestorPositionCombineDetail(self,data):
        """
        请求查询投资者持仓明细
        data 调用api需要填写参数表单,类型为CThostFtdcQryInvestorPositionCombineDetailField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQryInvestorPositionCombineDetail'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqOrderInsert(self,data):
        """
        报单录入请求
        data 调用api需要填写参数表单,类型为CThostFtdcInputOrderField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqOrderInsert'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQrySecAgentACIDMap(self,data):
        """
        请求查询二级代理操作员银期权限
        data 调用api需要填写参数表单,类型为CThostFtdcQrySecAgentACIDMapField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQrySecAgentACIDMap'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqParkedOrderAction(self,data):
        """
        预埋撤单录入请求
        data 调用api需要填写参数表单,类型为CThostFtdcParkedOrderActionField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqParkedOrderAction'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQueryBankAccountMoneyByFuture(self,data):
        """
        期货发起查询银行余额请求
        data 调用api需要填写参数表单,类型为CThostFtdcReqQueryAccountField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQueryBankAccountMoneyByFuture'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQryParkedOrderAction(self,data):
        """
        请求查询预埋撤单
        data 调用api需要填写参数表单,类型为CThostFtdcQryParkedOrderActionField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQryParkedOrderAction'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqAuthenticate(self,data):
        """
        客户端认证请求
        data 调用api需要填写参数表单,类型为CThostFtdcReqAuthenticateField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqAuthenticate'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQryExchangeMarginRate(self,data):
        """
        请求查询交易所保证金率
        data 调用api需要填写参数表单,类型为CThostFtdcQryExchangeMarginRateField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQryExchangeMarginRate'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqTradingAccountPasswordUpdate(self,data):
        """
        资金账户口令更新请求
        data 调用api需要填写参数表单,类型为CThostFtdcTradingAccountPasswordUpdateField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqTradingAccountPasswordUpdate'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqUserLogout(self,data):
        """
        登出请求
        data 调用api需要填写参数表单,类型为CThostFtdcUserLogoutField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqUserLogout'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQryInstrument(self,data):
        """
        请求查询合约
        data 调用api需要填写参数表单,类型为CThostFtdcQryInstrumentField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQryInstrument'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqOrderAction(self,data):
        """
        报单操作请求
        data 调用api需要填写参数表单,类型为CThostFtdcInputOrderActionField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqOrderAction'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQryInstrumentCommissionRate(self,data):
        """
        请求查询合约手续费率
        data 调用api需要填写参数表单,类型为CThostFtdcQryInstrumentCommissionRateField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQryInstrumentCommissionRate'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQryInstrumentMarginRate(self,data):
        """
        请求查询合约保证金率
        data 调用api需要填写参数表单,类型为CThostFtdcQryInstrumentMarginRateField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQryInstrumentMarginRate'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQryInvestor(self,data):
        """
        请求查询投资者
        data 调用api需要填写参数表单,类型为CThostFtdcQryInvestorField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQryInvestor'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQryExchangeMarginRateAdjust(self,data):
        """
        请求查询交易所调整保证金率
        data 调用api需要填写参数表单,类型为CThostFtdcQryExchangeMarginRateAdjustField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQryExchangeMarginRateAdjust'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQryInvestorProductGroupMargin(self,data):
        """
        请求查询投资者品种/跨品种保证金
        data 调用api需要填写参数表单,类型为CThostFtdcQryInvestorProductGroupMarginField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQryInvestorProductGroupMargin'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQryEWarrantOffset(self,data):
        """
        请求查询仓单折抵信息
        data 调用api需要填写参数表单,类型为CThostFtdcQryEWarrantOffsetField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQryEWarrantOffset'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQryDepthMarketData(self,data):
        """
        请求查询行情
        data 调用api需要填写参数表单,类型为CThostFtdcQryDepthMarketDataField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQryDepthMarketData'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQryTransferBank(self,data):
        """
        请求查询转帐银行
        data 调用api需要填写参数表单,类型为CThostFtdcQryTransferBankField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQryTransferBank'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqRemoveParkedOrderAction(self,data):
        """
        请求删除预埋撤单
        data 调用api需要填写参数表单,类型为CThostFtdcRemoveParkedOrderActionField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqRemoveParkedOrderAction'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQryProduct(self,data):
        """
        请求查询产品
        data 调用api需要填写参数表单,类型为CThostFtdcQryProductField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQryProduct'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQryTradingCode(self,data):
        """
        请求查询交易编码
        data 调用api需要填写参数表单,类型为CThostFtdcQryTradingCodeField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQryTradingCode'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQrySettlementInfo(self,data):
        """
        请求查询投资者结算结果
        data 调用api需要填写参数表单,类型为CThostFtdcQrySettlementInfoField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQrySettlementInfo'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQryAccountregister(self,data):
        """
        请求查询银期签约关系
        data 调用api需要填写参数表单,类型为CThostFtdcQryAccountregisterField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQryAccountregister'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQryParkedOrder(self,data):
        """
        请求查询预埋单
        data 调用api需要填写参数表单,类型为CThostFtdcQryParkedOrderField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQryParkedOrder'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQryTransferSerial(self,data):
        """
        请求查询转帐流水
        data 调用api需要填写参数表单,类型为CThostFtdcQryTransferSerialField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQryTransferSerial'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqQryContractBank(self,data):
        """
        请求查询签约银行
        data 调用api需要填写参数表单,类型为CThostFtdcQryContractBankField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqQryContractBank'

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
        return 0,'',int(requestIDMessage.requestID)


    def ReqRemoveParkedOrder(self,data):
        """
        请求删除预埋单
        data 调用api需要填写参数表单,类型为CThostFtdcRemoveParkedOrderField,具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = 'ReqRemoveParkedOrder'

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
        return 0,'',int(requestIDMessage.requestID)


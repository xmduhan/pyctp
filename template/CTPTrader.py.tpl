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
                    if messageList[1] == 'hello':
                        self.threadResponse.send_multipart([messageList[0],'hello'])
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

{# 生成所有api的实现 -#}
{%- for method in reqMethodDict.itervalues() -%}
{% include 'ReqMethod.py.tpl' %}
{% endfor -%}

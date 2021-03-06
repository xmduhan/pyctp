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
import CTPCallback as callback
import psutil
from CTPUtil import CallbackManager,mallocIpcAddress,packageReqInfo


context = zmq.Context()


class TraderWorker:
    """
    Trader的监听线程
    """

    def __init__(self, traderConverter, callbackManager):
        """
        构造函数
        """
        # 绑定协议转换器和回调管理器实例
        self.__traderConverter = traderConverter
        self.__callbackManager = callbackManager

        # 分配通讯管道
        self.controlPipe = mallocIpcAddress()

        # 创建线程控制通讯管道
        global context
        request = context.socket(zmq.DEALER)
        request.connect(self.controlPipe)
        request.setsockopt(zmq.LINGER,0)
        self.request = request
        response = context.socket(zmq.ROUTER)
        response.bind(self.controlPipe)
        self.response = response

        # 创建线程并启动
        thread = threading.Thread(target=self.__threadFunction)
        thread.daemon = True
        thread.start()
        self.__thread = thread


    def send(self,messageList):
        """
        向线程发送一条命令消息
        参数:
        messageList  要发送的消息列表
        返回值:无
        """
        self.request.send_multipart(messageList)


    def recv(self,timeout=1000):
        """
        读取监听线程的返回消息
        参数:
        timeout  如果没有消息等待的时间(单位:毫秒)
        返回值:
        如果无法收到消息返回None,否则返回消息列表
        """
        poller = zmq.Poller()
        poller.register(self.request, zmq.POLLIN)
        sockets = dict(poller.poll(timeout))
        if self.request not in sockets:
            return None
        return self.request.recv_multipart()


    def echo(self,message):
        """
        发送一个消息等待返回,用于测试线程已经处于监听状态
        """
        # 等待监听线程启动
        self.send(['echo',message])
        return self.recv()[0]


    def exit(self):
        """
        通知工作进程需要退出,以便激活垃圾回释放资源
        """
        self.send(['exit'])


    def __threadFunction(self):
        """
        监听线程的方法
        """
        while True:
            try:
                # 等待消息
                poller = zmq.Poller()
                poller.register(self.__traderConverter.response, zmq.POLLIN)
                poller.register(self.__traderConverter.publish, zmq.POLLIN)
                poller.register(self.response, zmq.POLLIN)
                sockets = dict(poller.poll())

                if self.response in sockets:
                    # 接收到来自进程的命令
                    messageList = self.response.recv_multipart()
                    if messageList[1] == 'exit':
                        return
                    if messageList[1] == 'echo':
                        if len(messageList) >= 3:
                            toSendBack = messageList[2]
                        else:
                            toSendBack = ''
                        self.response.send_multipart([messageList[0],toSendBack])
                    continue

                # 处理response管道中的数据
                # TODO: 这里优先处理response,只要response队列中有数据,就不出里publish
                # 这主要是由于OnRtnTrade返回信息中没有带RequestID,导致这个消息通过
                # publish管道去返回，这就可能造成OnRtnOrder和OnRtnTrade消息可能出现顺序
                # 颠倒的问题，当然这不是彻底的解决方法,最好是能在转换器中将OnRtnTrade和
                # 其原始调用的RequestID关联起来，并通过让OnRtnTrade通过response管道返回,
                # 这样才能保证消息的绝对序列化
                if self.__traderConverter.response in sockets:
                    socket = self.__traderConverter.response
                    # 读取消息
                    messageList = socket.recv_multipart()
                    # 根据不同的消息类型提取回调名称和参数信息
                    if messageList[0] == 'RESPONSE':
                        apiName, respInfoJson = messageList[2:4]
                    else:
                        print u'接收到1条未知消息...'
                        continue
                    respInfo = json.loads(respInfoJson)
                    parameters = respInfo['Parameters']
                    # 调用对应的回调函数
                    self.__callbackManager.callback(apiName,parameters)
                    continue

                if self.__traderConverter.publish in sockets:
                    socket = self.__traderConverter.publish
                    # 读取消息
                    messageList = socket.recv_multipart()
                    # 根据不同的消息类型提取回调名称和参数信息
                    if messageList[0] == 'PUBLISH':
                        apiName, respInfoJson = messageList[1:3]
                    else:
                        print u'接收到1条未知消息...'
                        continue
                    respInfo = json.loads(respInfoJson)
                    parameters = respInfo['Parameters']
                    # 调用对应的回调函数
                    self.__callbackManager.callback(apiName,parameters)
                    continue

            except Exception as e:
                print e

        print u'监听线程退出...'



class TraderConverter:
    """
    转换器进程对象封装,负责创建转换器的进程和进程的管理和回收
    """
    def __init__(self,frontAddress,brokerID,userID,password,timeout=10,converterQueryInterval=1):
        """
        构造函数
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
        self.__process = subprocess.Popen(commandLine,stdout=traderStdout,cwd=self.workdir)

		# 创建请求通讯通道
        global context
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


    def __del__(self):
        """
        对象移除过程
        1.结束ctp转换器进程
        """
        self.__clean__()


    def __clean__(self):
        """
        清除trader转换器进程
        """
        attrName = '_%s__%s' % (self.__class__.__name__, 'process')
        if attrName in self.__dict__.keys():
            self.__process.kill()
            self.__process.wait()
            del self.__process


    def getPid(self):
        """
        获取转化器进程的pid
        """
        return self.__process.pid


class Trader :
    """
    Trader通讯管道类,该类通过和CTPConverter的Trader进程通讯,对外实现python语言封装的CTP接口,
    """

    def __testChannel(self):
        """
        检查ctp交易通道是否运行正常，该方法在构造函数内调用如果失败，构造函数会抛出异常
        成功返回True，失败返回False
        """

        flag = []
        def OnRspQryTradingAccount(**kwargs):
            flag.append(1)

        bindId = self.bind(callback.OnRspQryTradingAccount,OnRspQryTradingAccount)
        try:
            data = CThostFtdcQryTradingAccountField()
            error = 0
            while True:
                result = self.ReqQryTradingAccount(data)
                if result[0] != 0:
                    return False
                i = 0
                while len(flag) == 0:
                    sleep(0.01)
                    i += 1
                    if i > 100:
                        break
                if len(flag) > 0:
                    return True
                error += 1
                if error > 3:
                    return False
        finally:
            self.unbind(bindId)


    def __init__(self,frontAddress,brokerID,userID,password,timeout=10,converterQueryInterval=1):
        """
        初始化过程:
        1.创建转换器进程对象
        2.创建回到管理器
        3.创建监听线程
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

        # 创建转换器进程
        self.__traderConverter = TraderConverter(
            frontAddress,brokerID,userID,password,timeout,converterQueryInterval
        )

        # 设置等待超时时间
        self.timeoutMillisecond = 1000 * timeout

        # 创建回调链管理器
        self.__callbackManager = CallbackManager()

        # 启动工作线程
        self.__traderWorker = TraderWorker(self.__traderConverter,self.__callbackManager)
        if self.__traderWorker.echo('ready') != 'ready':
            self.__clean__()
            raise Exception(u'监听线程无法正常启动...')

        # 接口可用性测试如果失败阻止对象创建成功
        if not self.__testChannel():
            self.__clean__()
            raise Exception(u'无法建立ctp连接,请查看ctp转换器的日志')

    def getConverterWorkdir(self):
        """
        读取转化器的工作目录
        主要方便在调试的时候可以查看转换器的日志
        """
        return self.__traderConverter.workdir

    def __enter__(self):
        """ 让Trader可以使用with语句 """
        return self


    def __clean__(self):
        """
        资源释放处理
        """
        #if hasattr(self, '__traderWorker'):
        attrName = '_%s__%s' % (self.__class__.__name__, 'traderWorker')
        if attrName in self.__dict__.keys():
            self.__traderWorker.exit()
            del self.__traderWorker

    def __exit__(self, type, value, tb):
        """ 让Trader可以使用with语句 """
        pass


    def __del__(self):
        """
        对象移除过程
        1.结束ctp转换器进程
        """
        self.__clean__()


    def bind(self,callbackName,funcToCall):
        """转调回调链管理器"""
        return self.__callbackManager.bind(callbackName,funcToCall)


    def unbind(self,bindId):
        """转调回调管理器"""
        return self.__callbackManager.unbind(bindId)


    def getConverterPid(self):
        """
        获取转换器的进程标识
        """
        return self.__traderConverter.getPid()


{# 生成所有api的实现 -#}
{%- for method in reqMethodDict.itervalues() -%}
{% include 'ReqMethod.py.tpl' %}
{% endfor -%}

#!/usr/bin/env python
# encoding: utf-8
import os
import zmq
import tempfile
import threading
import uuid
import json
import subprocess
from CTPStruct import *
from CTPUtil import CallbackManager,mallocIpcAddress,packageReqInfo
from message import *
from datetime import datetime
from dateutil.relativedelta import relativedelta
import CTPCallback as callback
from time import sleep
from ErrorResult import *

context = zmq.Context()


class MdWorker:
    """
    Md 工作线程
    """
    def __init__(self,mdConverter,callbackManager):
        """
        """
        # 绑定协议管理器和回调管理器实例
        self.__mdConverter = mdConverter
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
                poller.register(self.__mdConverter.publish, zmq.POLLIN)
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

                # 循环读取消息进程回调处理
                for socket in sockets:
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

            except Exception as e:
                print e

        print u'监听线程退出...'


class MdConverter:
    """
    md转换器封装
    """

    def __init__(self,frontAddress,brokerID,userID,password):
        """
        1.创建ctp转换器进程
        2.创建和ctp通讯进程的通讯管道
        3.测试ctp连接是否正常
        参数:
        frontAddress   ctp服务器地址
        brokerID   代理商Id
        userID   用户Id
        password   密码
        """
        # 创建临时工作目录
        self.workdir = tempfile.mkdtemp()

        # 为ctp md转换器分配通讯管道地址
        self.requestPipe = mallocIpcAddress()
        self.pushbackPipe = mallocIpcAddress()
        self.publishPipe = mallocIpcAddress()

        # 构造调用命令
        commandLine = ['md',
        '--FrontAddress',frontAddress,
        '--BrokerID',brokerID,
        '--UserID',userID,
        '--Password', password,
        '--RequestPipe', self.requestPipe,
        '--PushbackPipe', self.pushbackPipe,
        '--PublishPipe', self.publishPipe,
        '--loyalty'
        ]

        # 创建转换器子进程
        fileOutput = os.path.join(self.workdir,'md.log')
        mdStdout = open(fileOutput, 'w')
        self.__process = subprocess.Popen(commandLine,stdout=mdStdout,cwd=self.workdir)

        # 创建接受行情数据的管道
        global context
        self.context = context

        # 创建请求通讯管道
        request = context.socket(zmq.REQ)
        request.connect(self.requestPipe)
        request.setsockopt(zmq.LINGER,0)
        self.request = request

        #创建订阅管道
        publish = context.socket(zmq.SUB)
        publish.connect(self.publishPipe)
        publish.setsockopt(zmq.SUBSCRIBE, '');
        self.publish = publish


    def __del__(self):
        """
        对象移除前处理,要清除转换器进程
        """
        self.__clean__()


    def __clean__(self):
        """
        清楚md转换器进程
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


class Md:
    """
    Md通讯管道类,该类通过和CTPConverter的Md(行情)进程通讯,实线行情数据的传送
    """

    def __testChannel(self):
        """
        检查和ctp md 进程是否连通
        这里假设了在没有订阅的情况下调用取消订阅也能成功
        """
        flag = []

        def OnRspUnSubMarketData(**kwargs):
            flag.append(1)

        def getDefaultInstrumentID(months=1):
            return datetime.strftime(datetime.now() + relativedelta(months=months),"IF%y%m")

        bindId = self.bind(callback.OnRspUnSubMarketData,OnRspUnSubMarketData)
        try:
            data = [getDefaultInstrumentID()]
            error = 0
            while True:
                result = self.UnSubscribeMarketData(data)
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



    def __init__(self,frontAddress,brokerID,userID,password):
        """
        1.创建ctp转换器进程
        2.创建和ctp通讯进程的通讯管道
        3.测试ctp连接是否正常
        参数:
        frontAddress   ctp服务器地址
        brokerID   代理商Id
        userID   用户Id
        password   密码
        """
        # 创建md转换器
        self.__mdConverter = MdConverter(
            frontAddress,brokerID,userID,password
        )

        # 创建回调链管理器
        self.__callbackManager = CallbackManager()

        # 创建工作线程
        self.__mdWorker = MdWorker(self.__mdConverter,self.__callbackManager)
        if self.__mdWorker.echo('ready') != 'ready':
            self.__clean__()
            raise Exception(u'监听线程无法正常启动...')

        # 接口可用性测试如果失败阻止对象创建成功
        if not self.__testChannel():
            self.__clean__()
            raise Exception(u'无法建立ctp连接,请查看ctp转换器的日志')


    def __clean__(self):
        """
        资源释放处理
        """
        attrName = '_%s__%s' % (self.__class__.__name__, 'mdWorker')
        if attrName in self.__dict__.keys():
            self.__mdWorker.exit()
            del self.__mdWorker


    def __enter__(self):
        """ 让Md可以使用with语句 """
        return self


    def __exit__(self, type, value, tb):
        """ 让Md可以使用with语句 """
        pass


    def __del__(self):
        """
        对象移除过程,结束md转换器进程
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
        return self.__mdConverter.getPid()


    def requestMethod(self,requestApiName,instrumentIDList):
        """
        通用请求方法,提供给SubscribeMarketData和UnSubscribeMarketData调用
        """
        timeout = 3000
        request = self.__mdConverter.request

        # 准备调用参数
        reqInfo = packageReqInfo(requestApiName,instrumentIDList)

        # 发送消息
        requestMessage = MdRequestMessage()
        requestMessage.header = 'REQUEST'
        requestMessage.apiName = requestApiName
        requestMessage.reqInfo = json.dumps(reqInfo)
        requestMessage.send(request)

        # 等待转换器响应
        poller = zmq.Poller()
        poller.register(request, zmq.POLLIN)
        sockets = dict(poller.poll(timeout))
        if not request in sockets:
            return ResponseTimeOut[:-1]

        # 读取转换器响应,并返回处理结果
        responseMessage = MdResponseMessage()
        responseMessage.recv(request)
        errorInfo = json.loads(responseMessage.errorInfo)
        return errorInfo['ErrorID'], errorInfo['ErrorMsg']


    def SubscribeMarketData(self,instrumentIDList):
        """
        订阅行情
        """
        requestApiName = 'SubscribeMarketData'
        return self.requestMethod(requestApiName,instrumentIDList)


    def UnSubscribeMarketData(self,instrumentIDList):
        """
        取消行情订阅
        """
        requestApiName = 'UnSubscribeMarketData'
        return self.requestMethod(requestApiName,instrumentIDList)



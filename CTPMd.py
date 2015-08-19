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
                poller.register(self.__mdConverter.response, zmq.POLLIN)
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
    """
    pass


class Md:
	"""
	Md通讯管道类,该类通过和CTPConverter的Md(行情)进程通讯,实线行情数据的传送
	"""



	def __testChannel(self):
		"""
		检查和ctp md 进程是否连通
		在md进程启动后会先发送一个空消息,提供测试通路使用
		"""
		# 由于zmq publisher需要等待客户端连接，这里等待相应时间才能接受到消息
		timeout = 2000
		reader = self.reader
		poller = zmq.Poller()
		poller.register(reader, zmq.POLLIN)
		sockets = dict(poller.poll(timeout))
		if reader in sockets :
			result = reader.recv_multipart()
			if len(result) == 1 and result[0] == "":
				return True
			else:
				self.__delTraderProcess()
				raise Exception(u'接收到不正确的消息格式')
		else:
			return False



	def __delTraderProcess(self):
		"""
		清除trader转换器进程
		"""
		if hasattr(self, 'mdProcess'):
			self.mdProcess.kill()
			self.mdProcess.wait()
			del self.mdProcess



	def __init__(self,frontAddress,brokerID,userID,password,instrumentIDList):
		"""
		1.创建ctp转换器进程
		2.创建和ctp通讯进程的通讯管道
		3.测试ctp连接是否正常
		参数:
		frontAddress   ctp服务器地址
		brokerID   代理商Id
		userID   用户Id
		password   密码
		instrumentIDList   需要订阅的品种的Id列表
		"""
		# 创建临时工作目录
		self.workdir = tempfile.mkdtemp()

		# 为ctp md转换器分配通讯管道地址
		self.pushbackPipe = mallocIpcAddress()
		self.publishPipe = mallocIpcAddress()

		# 生成md命令需要的临时配置文件(描述品种ID列表)
		self.tempConfigFile = tempfile.mktemp(suffix='.json')
		instrumentIDListJson = json.dumps(instrumentIDList)
		with open(self.tempConfigFile, 'w') as f:
			f.write(instrumentIDListJson.encode('utf-8'))

		# 创建接受行情数据的管道
		context = zmq.Context()
		self.context = context
		socket = context.socket(zmq.SUB)
		socket.connect(self.publishPipe)
		socket.setsockopt(zmq.SUBSCRIBE, '');
		self.reader = socket

		# 构造调用命令
		commandLine = ['md',
		'--FrontAddress',frontAddress,
		'--BrokerID',brokerID,
		'--UserID',userID,
		'--Password', password,
		'--PushbackPipe', self.pushbackPipe,
		'--PublishPipe', self.publishPipe,
		'--InstrumentIDConfigFile',self.tempConfigFile,
		'--loyalty'
		]

		# 创建转换器子进程
		fileOutput = os.path.join(self.workdir,'md.log')
		traderStdout = open(fileOutput, 'w')
		#self.mdProcess = subprocess.Popen(commandLine,stdout=traderStdout
		self.mdProcess = subprocess.Popen(commandLine,stdout=traderStdout,cwd=self.workdir)

		# 检查ctp通道是否建立，如果失败抛出异常
		if not self.__testChannel():
			self.__delTraderProcess()
			raise Exception(u'无法建立ctp连接,具体错误请查看ctp转换器的日志信息')


	def __enter__(self):
		""" 让Md可以使用with语句 """
		#print '__enter__():被调用'
		return self


	def __exit__(self, type, value, tb):
		""" 让Md可以使用with语句 """
		#print '__exit__():被调用',type,value,tb
		pass


	def __del__(self):
		"""
		对象移出过程
		1.结束ctp转换器进程
		"""
		self.__delTraderProcess()


	def readMarketData(self,timeout=1):
		"""
		读取行情数据
		参数
		timeout 如果当前没有消息的等待时间(毫秒)
		"""
		reader = self.reader
		poller = zmq.Poller()
		poller.register(reader, zmq.POLLIN)
		sockets = dict(poller.poll(timeout))
		if reader in sockets :
			result = reader.recv_multipart()
			# TODO 这里假设了result只有一个元素,最好是检查一下
			if len(result) == 1 and result[0] != "":
				resultDict = json.loads(result[0])
				marketData = CThostFtdcDepthMarketDataField(**resultDict)
				return marketData
			else:
				raise Exception(u'接收到不正确的消息格式')
		else:
			return None


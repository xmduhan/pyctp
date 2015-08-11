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
        timeout=10,converterQueryInterval=1):
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
        converterQueryInterval 转换器的流量控制时间间隔(单位:秒),默认为1
        '''
        # 创建临时工作目录
        self.workdir = tempfile.mkdtemp()

        # 为ctp转换器分配通讯管道地址
        self.requestPipe = mallocIpcAddress()
        self.responsePipe = mallocIpcAddress()
        self.pushbackPipe = mallocIpcAddress()
        self.publishPipe = mallocIpcAddress()

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
        对象移除过程
        1.结束ctp转换器进程
        '''
        self.__delTraderProcess()


{# 所有api的实现 #}
{% for method in reqMethodDict.itervalues() %}
    {% include 'ReqMethod.py.tpl' %}
{% endfor %}


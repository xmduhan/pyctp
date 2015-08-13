#!/usr/bin/env python
# encoding: utf-8

import os
import psutil
from time import sleep
from CTPTrader import Trader
from nose.plugins.attrib import attr
import CTPCallback as callback
import CTPStruct as struct
import zmq

frontAddress = None
mdFrontAddress = None
brokerID = None
userID = None
password = None


def setup():
    """
    所有用例的公共初始化代码
    """
    global frontAddress, mdFrontAddress, brokerID, userID, password
    # 读取环境变量中的信息
    frontAddress = os.environ.get('CTP_FRONT_ADDRESS')
    assert frontAddress, u'必须定义环境变量:CTP_FRONT_ADDRESS'
    mdFrontAddress = os.environ.get('CTP_MD_FRONT_ADDRESS')
    assert mdFrontAddress, u'必须定义环境变量:CTP_MD_FRONT_ADDRESS'
    brokerID = os.environ.get('CTP_BROKER_ID')
    assert brokerID, u'必须定义环境变量:CTP_BROKER_ID'
    userID = os.environ.get('CTP_USER_ID')
    assert userID, u'必须定义环境变量:CTP_USER_ID'
    password = os.environ.get('CTP_PASSWORD')
    assert password, u'必须定义环境变量:CTP_PASSWORD'


@attr('test_trader_process_create_and_clean')
def test_trader_process_create_and_clean():
    """
    测试trader转换器进程的创建和清理
    """
    global frontAddress, mdFrontAddress, brokerID, userID, password
    process = psutil.Process()
    # 没有创建Trader对象前应该没有trader进程
    # assert 'trader' not in [child.name() for child in process.children() ]

    # 创建后可以找到一个trader进程
    trader = Trader(frontAddress, brokerID, userID, password)
    pid = trader.traderProcess.pid
    assert pid and pid != 0
    assert pid in [child.pid for child in process.children()]

    # 将变量指向None迫使垃圾回收,确认进程被清理了
    trader = None
    sleep(1)
    assert pid not in [child.pid for child in process.children()]


@attr('test_trader_bind_callback')
def test_trader_bind_callback():
    """
    测试捆绑回调函数
    """
    flag = []

    def OnRspQryTradingAccount(**kargs):
        flag = kargs['flag']
        flag.append(1)

    global frontAddress, mdFrontAddress, brokerID, userID, password
    trader = Trader(frontAddress, brokerID, userID, password)
    bindId1 = trader.bind(callback.OnRspQryTradingAccount, OnRspQryTradingAccount)
    trader._callback(callback.OnRspQryTradingAccount, {'flag': flag})
    assert len(flag) == 1

    bindId2 = trader.bind(callback.OnRspQryTradingAccount, OnRspQryTradingAccount)
    trader._callback(callback.OnRspQryTradingAccount, {'flag': flag})
    assert len(flag) == 3

    assert trader.unbind(bindId1)
    assert not trader.unbind(bindId1)

    trader._callback(callback.OnRspQryTradingAccount, {'flag': flag})
    assert len(flag) == 4

    assert trader.unbind(bindId2)
    assert not trader.unbind(bindId2)

    trader._callback(callback.OnRspQryTradingAccount, {'flag': flag})
    assert len(flag) == 4


@attr('test_communicate_working_thread')
def test_communicate_working_thread():
    """
    测试和监听进程进行通讯
    """
    global frontAddress, mdFrontAddress, brokerID, userID, password

    # 初始化进程检测工具对象
    process = psutil.Process()
    assert len(process.threads()) == 1

    trader = Trader(frontAddress, brokerID, userID, password)
    assert len(process.threads()) == 4

    # 测试hello命令的响应
    trader._sendToThread(['hello'])
    messageList =trader._recvFromThread()
    assert messageList
    assert messageList[0] == 'hello'

    # 测试线程退出命令的相应
    trader._sendToThread(['exit'])
    sleep(1)
    assert len(process.threads()) == 3

    # 强迫对象进行垃圾回收
    trader = None
    sleep(1)
    print len(process.threads())
    assert len(process.threads()) == 1


@attr('test_qry_trading_account')
def test_qry_trading_account():

    # 创建trader对象
    global frontAddress, mdFrontAddress, brokerID, userID, password
    trader = Trader(frontAddress, brokerID, userID, password)

    # 定义测试标志
    flag = []

    # 定义回调函数,并将其绑定
    def OnRspQryTradingAccount1(RequestID,RspInfo,Data,IsLast):
        print 'OnRspQryTradingAccount is called'
        #print kargs.keys()
        flag.append(1)
    trader.bind(callback.OnRspQryTradingAccount, OnRspQryTradingAccount1)

    def OnRspQryTradingAccount2(**kargs):
        print 'OnRspQryTradingAccount is called'
        #print kargs.keys()
        flag.append(1)
    trader.bind(callback.OnRspQryTradingAccount, OnRspQryTradingAccount2)

    # 发送一个请求并等待回调函数被调用
    sleep(1)
    data = struct.CThostFtdcQryTradingAccountField()
    result = trader.ReqQryTradingAccount(data)
    assert result[0] == 0
    sleep(1)
    assert len(flag) == 2



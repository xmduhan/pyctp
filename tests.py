#!/usr/bin/env python
# encoding: utf-8

import os
import psutil
from time import sleep
from CTPTrader import Trader


frontAddress = None
mdFrontAddress = None
brokerID = None
userID = None
password = None


def setup():
    '''
    所有用例的公共初始化代码
    '''
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


def test_trader_process_create_and_clean():
    '''
    测试trader转换器进程的创建和清理
    '''
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


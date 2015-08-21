#!/usr/bin/env python
# encoding: utf-8

import os
import psutil
from time import sleep
from CTPTrader import Trader, CallbackManager, TraderWorker, TraderConverter
from CTPMd import Md
from nose.plugins.attrib import attr
import CTPCallback as callback
import CTPStruct as struct
from datetime import datetime
from dateutil.relativedelta import relativedelta
import CTPCallback as callback
import inspect


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


def getDefaultInstrumentID(months=1):
    """
    获取一个可用的交易品种ID
    """
    return datetime.strftime(datetime.now() + relativedelta(months=months),"IF%y%m")


orderRefSeq = 0
def getOrderRef():
    '''
    获取OrderRef序列值
    '''
    global orderRefSeq
    orderRefSeq += 1
    return ('%12d' % orderRefSeq).replace(' ','0') # '000000000001'


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
    pid = trader.getConverterPid()
    assert pid and pid != 0
    assert pid in [child.pid for child in process.children()]

    # 将变量指向None迫使垃圾回收,确认进程被清理了
    trader = None
    sleep(1)
    assert pid not in [child.pid for child in process.children()]


@attr('test_bind_callback')
def test_bind_callback():
    """
    测试捆绑回调函数
    """
    flag = []

    def OnRspQryTradingAccount(**kwargs):
        flag = kwargs['flag']
        flag.append(1)

    callbackManager = CallbackManager()
    bindId1 = callbackManager .bind(callback.OnRspQryTradingAccount, OnRspQryTradingAccount)
    callbackManager.callback(callback.OnRspQryTradingAccount, {'flag': flag})
    assert len(flag) == 1

    bindId2 = callbackManager .bind(callback.OnRspQryTradingAccount, OnRspQryTradingAccount)
    callbackManager.callback(callback.OnRspQryTradingAccount, {'flag': flag})
    assert len(flag) == 3

    assert callbackManager .unbind(bindId1)
    assert not callbackManager .unbind(bindId1)

    callbackManager.callback(callback.OnRspQryTradingAccount, {'flag': flag})
    assert len(flag) == 4

    assert callbackManager .unbind(bindId2)
    assert not callbackManager .unbind(bindId2)

    callbackManager.callback(callback.OnRspQryTradingAccount, {'flag': flag})
    assert len(flag) == 4


@attr('test_communicate_working_trader_thread')
def test_communicate_working_trader_thread():
    """
    测试和监听进程进行通讯
    """
    # 初始化进程检测工具对象
    process = psutil.Process()
    print 'len(process.threads()) =', len(process.threads())

    # 创建一个traderWorker
    global frontAddress, mdFrontAddress, brokerID, userID, password
    traderConverter = TraderConverter(frontAddress, brokerID, userID, password)
    callbackManager = CallbackManager()
    nThread = len(process.threads())
    print 'len(process.threads()) =', len(process.threads())
    assert nThread > 1

    traderWorker = TraderWorker(traderConverter, callbackManager)
    print 'len(process.threads()) =', len(process.threads())
    assert len(process.threads()) == nThread + 1

    # 测试hello命令的响应
    traderWorker.send(['echo', 'hello'])
    messageList = traderWorker.recv()
    assert messageList
    assert messageList[0] == 'hello'

    # 测试线程退出命令的相应
    traderWorker.send(['exit'])
    sleep(1)
    print 'len(process.threads()) =', len(process.threads())
    assert len(process.threads()) == nThread


@attr('test_qry_trading_account')
def test_qry_trading_account():

    # 创建trader对象
    global frontAddress, mdFrontAddress, brokerID, userID, password
    trader = Trader(frontAddress, brokerID, userID, password)

    # 定义测试标志
    f1 = []
    f2 = []
    # 定义回调函数,并将其绑定
    def OnRspQryTradingAccount1(RequestID,RspInfo,Data,IsLast):
        #print 'OnRspQryTradingAccount1 is called'
        #print kwargs.keys()
        f1.append(1)
    trader.bind(callback.OnRspQryTradingAccount, OnRspQryTradingAccount1)

    def OnRspQryTradingAccount2(**kwargs):
        #print 'OnRspQryTradingAccount2 is called'
        #print kwargs.keys()
        if 'ResponseMethod' in kwargs.keys():
            f2.append(1)
        f1.append(1)

    trader.bind(callback.OnRspQryTradingAccount, OnRspQryTradingAccount2)

    # 发送一个请求并等待回调函数被调用
    data = struct.CThostFtdcQryTradingAccountField()
    result = trader.ReqQryTradingAccount(data)
    assert result[0] == 0

    # 等待回调函数被调用
    i = 0
    while len(f1) < 2:
        sleep(.01)
        i += 1
        if i > 300 :
            raise Exception(u'等待回调超时...')
    assert len(f2) > 0


@attr('test_subcribe_depth_market_data')
def test_subcribe_depth_market_data():
    """
    测试订阅行情
    """

    f1 = []
    def OnRspSubMarketData(**kwargs):
        print 'OnRspSubMarketData() is called'
        f1.append(1)

    f2 = []
    def OnRtnDepthMarketData(**kwargs):
        print 'OnRtnDepthMarketData() is called'
        f2.append(1)

    # 创建md对象
    global frontAddress, mdFrontAddress, brokerID, userID, password
    md = Md(mdFrontAddress, brokerID, userID, password)

    md.bind(callback.OnRspSubMarketData, OnRspSubMarketData)
    md.bind(callback.OnRtnDepthMarketData, OnRtnDepthMarketData)
    md.SubscribeMarketData([getDefaultInstrumentID()])
    sleep(1)

    print 'len(f1) =', len(f1)
    assert len(f1) > 0
    assert len(f2) > 0

@attr('test_md_process_create_and_clean')
def test_md_process_create_and_clean():
    """
    测试Md对象的创建和清理
    """
    global frontAddress, mdFrontAddress, brokerID, userID, password
    process = psutil.Process()

    # 创建后可以找到一个trader进程
    md = Md(mdFrontAddress, brokerID, userID, password)
    pid = md.getConverterPid()
    assert pid and pid != 0
    assert pid in [child.pid for child in process.children()]

    # 将变量指向None迫使垃圾回收,确认进程被清理了
    md = None
    sleep(1)
    assert pid not in [child.pid for child in process.children()]


def getInsertOrderField(direction,action,volume=1):
    """
    获取一个有效的建单数据格式
    """
    inputOrderField = struct.CThostFtdcInputOrderField()
    inputOrderField.BrokerID = brokerID
    inputOrderField.InvestorID = userID
    inputOrderField.InstrumentID = getDefaultInstrumentID()
    inputOrderField.OrderRef =  getOrderRef() #
    inputOrderField.UserID = userID
    inputOrderField.OrderPriceType = '1'     # 任意价

    # inputOrderField.Direction = '0'          # 买
    # inputOrderField.CombOffsetFlag = '0'     # 开仓
    if action == 'open':
        inputOrderField.CombOffsetFlag = '0'
        inputOrderField.Direction = {'buy': '0', 'sell': '1'}[direction]
    elif action == 'close':
        inputOrderField.CombOffsetFlag = '1'
        inputOrderField.Direction = {'buy': '1', 'sell': '0'}[direction]
    else:
        raise Exception(u'未知的操作方向')

    inputOrderField.CombHedgeFlag = '1'      # 投机
    inputOrderField.LimitPrice = 0           # 限价 0表不限制
    inputOrderField.VolumeTotalOriginal = volume  # 手数
    inputOrderField.TimeCondition = '1'      # 立即完成否则撤消
    inputOrderField.GTDDate = ''
    inputOrderField.VolumeCondition = '1'    # 成交类型  '1' 任何数量  '2' 最小数量 '3'全部数量
    inputOrderField.MinVolume = volume       # 最小数量
    inputOrderField.ContingentCondition = '1' # 触发类型 '1' 立即否则撤消
    inputOrderField.StopPrice = 0             # 止损价
    inputOrderField.ForceCloseReason = '0'    # 强平标识 '0'非强平
    inputOrderField.IsAutoSuspend = 0         # 自动挂起标识
    inputOrderField.BusinessUnit = ''         # 业务单元
    inputOrderField.RequestID = 1
    inputOrderField.UserForceClose = 0        # 用户强平标识
    inputOrderField.IsSwapOrder = 0           # 互换单标识
    return inputOrderField




@attr('test_open_and_close_position')
def test_open_and_close_position():
    """
    测试开仓和平仓
    """
    def settlement_info_confirm():
        """
        交易结果确认
        """
        result = []
        def OnRspSettlementInfoConfirm(**kwargs):
            print 'OnRspSettlementInfoConfirm() is called ...'
            result.append(kwargs)

        trader.bind(callback.OnRspSettlementInfoConfirm,OnRspSettlementInfoConfirm)
        data = struct.CThostFtdcSettlementInfoConfirmField()
        data.BrokerID = brokerID
        data.InvestorID = userID
        data.ConfirmDate = ''
        data.ConfirmTime = ''
        trader.ReqSettlementInfoConfirm(data)
        while len(result) == 0: sleep(.01)
        print result

    sequence = []

    onRspOrderInsertResult = []
    def OnRspOrderInsert(**kwargs):
        print 'OnRspOrderInsert() is called ...'
        onRspOrderInsertResult.append(kwargs)
        sequence.append('onRspOrderInsert')

    onErrRtnOrderInsertResult = []
    def OnErrRtnOrderInsert(**kwargs):
        print 'OnErrRtnOrderInsert() is called ...'
        onErrRtnOrderInsertResult.append(kwargs)
        sequence.append('onErrRtnOrderInsert')

    onRtnOrderResult = []
    def OnRtnOrder(**kwargs):
        print 'OnRtnOrder() is called ...'
        onRtnOrderResult.append(kwargs)
        sequence.append('onRtnOrder')

    OnRtnTradeResult = []
    def OnRtnTrade(**kwargs):
        print 'OnRtnTrade() is called ...'
        OnRtnTradeResult.append(kwargs)
        sequence.append('OnRtnTrade')

    global frontAddress, mdFrontAddress, brokerID, userID, password
    trader = Trader(frontAddress, brokerID, userID, password)
    trader.bind(callback.OnRspOrderInsert,OnRspOrderInsert)
    trader.bind(callback.OnErrRtnOrderInsert,OnErrRtnOrderInsert)
    trader.bind(callback.OnRtnOrder,OnRtnOrder)
    trader.bind(callback.OnRtnTrade,OnRtnTrade)

    # 交易结果确认
    settlement_info_confirm()

    # 进行开仓测试
    data = getInsertOrderField('buy','open')
    trader.ReqOrderInsert(data)
    sleep(1)
    assert len(onRspOrderInsertResult) == 0
    assert len(onErrRtnOrderInsertResult) == 0
    assert len(onRtnOrderResult) > 0
    assert len(OnRtnTradeResult) == 1
    # 清理数据
    onRspOrderInsertResult = []
    onErrRtnOrderInsertResult = []
    onRtnOrderResult = []
    OnRtnTradeResult = []

    # 进程平仓测试
    data = getInsertOrderField('buy','close')
    trader.ReqOrderInsert(data)
    sleep(1)
    assert len(onRspOrderInsertResult) == 0
    assert len(onErrRtnOrderInsertResult) == 0
    assert len(onRtnOrderResult) > 0
    assert len(OnRtnTradeResult) == 1

    print sequence
    assert sequence[3] == 'OnRtnTrade'
    assert sequence[7] == 'OnRtnTrade'


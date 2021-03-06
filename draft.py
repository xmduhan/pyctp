#!/usr/bin/env python
# encoding: utf-8

#%% 已完成任务
"""
1. 完成Makefile文件(ok)
2. 设定跟__init__.py的命名空间问题 (ok)
3. 增加一个callback文件(ok)
4. 增加response管道(ok)
5. 增加测试trader进程管例的测试用例(ok)
6. 去掉等待相关代码(okk)
7. 是否还是要增加message.py 和 error.py(作为统一差错信息处理，因为现在md和trader的文件分开了)(ok)
8. 将所有的响应函数映射到CTPCallback.py.tpl中(ok)
9. 增加绑定函数(理解多线程问题)(ok)
10. 构造函数中增加启动子进程轮询代码(ok)
11. 修改reqMethond模板,仅保留请求部分(ok)
12. 解决绑定后需要sleep(1)才能确保接收到信号的问题(ok)
13. 将回调列表维护部分代码封装到(CallbackManager)(ok)
14. 将工作线程部分单独分出来(TraderWorker)(ok)
15. 使所有测试用例通过(ok)
16. 解决工作进程无法自动清理的问题(trader=None)(ok)
"""


#%% 任务列表
"""
. 编写md的基础测试用例
. 按Trader模式来封装md
. 要增加MdRequestMessage和MdResponseMessage
. callback 列表中要加入md的函数名称，可以通过union trader.methodDict 和 md.methodDict(ok)
. 解决转换器参数大小写不一致的问题
"""

#%% 目标调用方式
from pyctp import Trader,Md,struct,callback

def OnRspQryTradingAccount(**kwargs):
    pass

trader = Trader(...)
trader.bind(callback.OnRspQryTradingAccount,OnRspQryTradingAccount)
data = struct.CThostFtdcTradingAccountField()
... ...
trader.ReqQryTradingAccount(data)

#%%
import os
from datetime import datetime
os.chdir(u'/home/duhan/github/pyctp')
from CTPStruct import *
from CTPTrader import Trader

frontAddress = os.environ.get('CTP_FRONT_ADDRESS')
assert frontAddress
brokerID = os.environ.get('CTP_BROKER_ID')
assert brokerID
userID = os.environ.get('CTP_USER_ID')
assert userID
password = os.environ.get('CTP_PASSWORD')
assert password

#%%
trader = Trader(frontAddress,brokerID,userID,password)



#%%
from threading import Thread
from time import sleep

class A(object):

    def test(self):
        sleep(5)
        print 'inner thread is exiting...'

    def __init__(self):
        self.thread = Thread(target=self.test)
        self.thread.start()

    def __del__(self):
        print '__del__ is called...'

a = A()
a = None

#%%
from threading import Thread
from time import sleep
def test():
    sleep(5)
    print 'thread is exiting...'

class A(object):

    def test(self):
        sleep(5)
        print 'inner thread is exiting...'

    def __init__(self):
        self.thread = Thread(target=test)
        self.thread.start()

    def __del__(self):
        print '__del__ is called...'

a = A()
a = None

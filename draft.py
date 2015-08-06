#!/usr/bin/env python
# encoding: utf-8

#%% 任务列表
'''
1. 完成Makefile文件(ok)
. 去掉等待相关代码
. 增加response管道
. 增加测试trader进程管例的测试用例
. 修改reqMethond模板,仅保留请求部分
. 解决转换器参数大小写不一致的问题
. 增加绑定函数(理解多线程问题)
. 构造函数中增加启动子进程轮询代码
. 设定跟__init__.py的命名空间问题

'''

#%% 目标调用方式
from pyctp import Trader
from pyctp import struct
from pyctp import callback

def OnRspQryTradingAccount(**kargs):
    pass

trader = Trader(...)
trader.bind(callback.OnRspQryTradingAccount,OnRspQryTradingAccount)
data = struct.CThostFtdcTradingAccountField()
... ...
trader.ReqQryTradingAccount(data)

#%%



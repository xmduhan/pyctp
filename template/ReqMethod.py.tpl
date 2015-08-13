{% set parameter = method['parameters'][0]  %}
    def {{ method['name']}}(self,data):
        """
        {{ method['remark'][3:] }}
        data 调用api需要填写参数表单,类型为{{parameter['raw_type']}},具体参见其定义文件
        返回信息格式[errorID,errorMsg,responseData=[...]]
        """

        requestApiName = '{{ method['name']}}'

        # 检查表单数据的类型是否正确
        if not isinstance(data,{{parameter['raw_type']}}):
            return InvalidRequestFormat

        # 打包消息格式
        reqInfo = packageReqInfo(requestApiName,data.toDict())
        metaData={}
        requestMessage = RequestMessage()
        requestMessage.header = 'REQUEST'
        requestMessage.apiName = requestApiName
        requestMessage.reqInfo = json.dumps(reqInfo)
        requestMessage.metaData = json.dumps(metaData)

        # 发送到服务器
        requestMessage.send(self.request)

        # 等待服务器的REQUESTID响应
        poller = zmq.Poller()
        poller.register(self.request, zmq.POLLIN)
        sockets = dict(poller.poll(self.timeoutMillisecond))
        if not (self.request in sockets) :
            return ResponseTimeOut

        # 从request通讯管道读取返回信息
        requestIDMessage = RequestIDMessage()
        requestIDMessage.recv(self.request)

        # 检查接收的消息格式
        c1 = requestIDMessage.header == 'REQUESTID'
        c2 = requestIDMessage.apiName == requestApiName
        if not ( c1 and c2 ):
            return InvalidMessageFormat

        # 如果没有收到RequestID,返回转换器的出错信息
        if not (int(requestIDMessage.requestID) > 0):
            errorInfo = json.loads(requestIDMessage.errorInfo)
            return errorInfo['ErrorID'],errorInfo['ErrorMsg'],None

        # 返回成功
        return 0,'',int(requestIDMessage.requestID)


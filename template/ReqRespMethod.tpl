	{% set parameter = method['parameters'][0]  %}
	def {{ method['name'][3:]}}(self,data):
		'''
		{{ method['remark'][3:] }}
		data 调用api需要填写参数表单,类型为{{parameter['raw_type']}},具体参见其定义文件
		返回信息格式[errorID,errorMsg,responseData=[...]]
		注意:同步调用没有metaData参数,因为没有意义
		'''
		if not isinstance(data,{{parameter['raw_type']}}):
			return InvalidRequestFormat


		requestApiName = 'Req{{method['name'][3:]}}'
		responseApiName = 'OnRsp{{method['name'][3:]}}'

		# 打包消息格式
		reqInfo = packageReqInfo(requestApiName,data.toDict())
		metaData={}
		requestMessage = RequestMessage()
		requestMessage.header = 'REQUEST'
		requestMessage.apiName = requestApiName
		requestMessage.reqInfo = json.dumps(reqInfo)
		requestMessage.metaData = json.dumps(metaData)

		{## TODO 判断现在多余了,应该去掉,因为这里处理的全部都是 #}
		{# {% if method['name'][3:6] == 'Qry' or method['name'][3:8] == 'Query' %} #}
		# 查询前的等待,避免超过ctp查询api的流量控制
		self.queryWait()
		# NOTE:更新lastQueryTime不能放在同步调用的返回处,因为有的调用返回时间非常长,这样再
		# 等待就没有必要
		self.lastQueryTime = datetime.now()
		{#  {% endif %} #}

		# 发送到服务器
		requestMessage.send(self.request)
		################### 等待服务器的REQUESTID响应 ###################
		# 读取服务
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
			return errorInfo['ErrorID'],errorInfo['ErrorMsg'],[]


		################### 等待服务器的返回的数据信息 ###################
		poller = zmq.Poller()
		poller.register(self.request, zmq.POLLIN)

		# 循环读取所有数据
		respnoseDataList = []
		while(True):
			sockets = dict(poller.poll(self.timeoutMillisecond))
			if not (self.request in sockets) :
				return ResponseTimeOut

			# 从request通讯管道读取返回信息
			responseMessage = ResponseMessage()
			responseMessage.recv(self.request)

			# 返回数据信息格式符合要求
			c1 = responseMessage.header == 'RESPONSE'
			c2 = responseMessage.requestID == requestIDMessage.requestID
			c3 = responseMessage.apiName in (responseApiName,requestApiName)
			if not (c1 and c2 and c3) :
				return InvalidMessageFormat

			# 提取消息中的出错信息
			respInfo = json.loads(responseMessage.respInfo)
			errorID = respInfo['Parameters']['RspInfo']['ErrorID']
			errorMsg = respInfo['Parameters']['RspInfo']['ErrorMsg']
			if errorID != 0 :
				return errorID,errorMsg,[]

			# 提取消息中的数据
			{% set responseDataType = onRspMethodDict['OnRsp' + method['name'][3:]]['parameters'][0]['raw_type']%}
			respnoseDataDict = respInfo['Parameters']['Data']
			if respnoseDataDict:
				respnoseData = {{responseDataType}}(**respnoseDataDict)
				respnoseDataList.append(respnoseData)

			# 判断是否已是最后一条消息
			if int(responseMessage.isLast) == 1:
				break;

		# 返回成功
		return 0,'',respnoseDataList

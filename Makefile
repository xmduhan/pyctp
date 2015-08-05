
all : generate


generate: CTPStruct.py CTPChannel.py CTPChannelPool.py query_api_tests.py


CTPStruct.py : template/CTPStruct.py.tpl
	python generate.py CTPStruct.py.tpl


CTPChannel.py : template/CTPChannel.py.tpl
	python generate.py CTPChannel.py.tpl


CTPChannelPool.py : template/CTPChannelPool.py.tpl
	python generate.py CTPChannelPool.py.tpl


query_api_tests.py : template/query_api_tests.py.tpl
	python generate.py query_api_tests.py.tpl


clean :
	touch template/*
	rm -f *.pyc *.pk *.con

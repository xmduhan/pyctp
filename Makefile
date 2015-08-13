
all : generate


generate: CTPStruct.py CTPTrader.py CTPMd.py CTPCallback.py


CTPStruct.py : template/CTPStruct.py.tpl
	python generate.py CTPStruct.py.tpl


CTPTrader.py : template/CTPTrader.py.tpl template/ReqMethod.py.tpl
	python generate.py CTPTrader.py.tpl


CTPMd.py : template/CTPMd.py.tpl
	python generate.py CTPMd.py.tpl


CTPCallback.py : template/CTPCallback.py.tpl
	python generate.py CTPCallback.py.tpl


clean :
	touch template/*
	rm -f *.pyc *.pk *.con

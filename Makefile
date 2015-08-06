
all : generate


generate: CTPStruct.py CTPTrader.py CTPMd.py __init__.py


CTPStruct.py : template/CTPStruct.py.tpl
	python generate.py CTPStruct.py.tpl


CTPTrader.py : template/CTPTrader.py.tpl
	python generate.py CTPTrader.py.tpl


CTPMd.py : template/CTPMd.py.tpl
	python generate.py CTPMd.py.tpl


__init__.py : template/__init__.py.tpl
	python generate.py __init__.py.tpl


clean :
	touch template/*
	rm -f *.pyc *.pk *.con

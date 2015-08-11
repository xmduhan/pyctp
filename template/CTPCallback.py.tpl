#!/usr/bin/env python
# encoding: utf-8

###################################
#         所有onRsp开头方法       #
###################################
{%- for method in onRspMethodDict.itervalues() %}
{{method['name']}} = '{{method['name']}}'
{%- endfor %}


###################################
#      所有onRspError开头方法     #
###################################
{%- for method in onRspErrorMethodDict.itervalues() %}
{{method['name']}} = '{{method['name']}}'
{%- endfor %}


###################################
#         所有onRtn开头方法       #
###################################
{%- for method in onRtnMethodDict.itervalues() %}
{{method['name']}} = '{{method['name']}}'
{%- endfor %}


###################################
#       所有onErrRtn开头方法      #
###################################
{%- for method in onErrRtnMethodDict.itervalues() %}
{{method['name']}} = '{{method['name']}}'
{%- endfor %}


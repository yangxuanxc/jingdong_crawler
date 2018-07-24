#! -*- coding: utf-8 -*-
import urllib.request
import random
import logging
import threading
import redis
import math
import re
import time
from collections import defaultdict
from scrapy.utils.httpobj import urlparse_cached
from requests import get
from bs4 import BeautifulSoup
from twisted.internet import defer
from twisted.internet.error import TimeoutError, ConnectionRefusedError, \
    ConnectError, ConnectionLost, TCPTimedOutError, ConnectionDone

logger = logging.getLogger(__name__)


class AutoProxyMiddleware(object):

    EXCEPTIONS_TO_CHANGE = (defer.TimeoutError, TimeoutError, ConnectionRefusedError, ConnectError, ConnectionLost, TCPTimedOutError, ConnectionDone)

    #这里的setting是默认值，可以在setting.py 中进行修改
    _settings = [
        ('enable', True),#是否开启中间件，默认True
        ('test_proxy_timeout', 5),#用于测试代理时连接超时设置。默认为5
        ('download_timeout', 60),#与scrapy的download_timeout一样，启用该中间件则设置。
        ('ban_code', [503, ]),#一个列表，代理被禁用的http状态码。确认返回状态码在此范围可自动切换代理。默认为[503,]
        ('ban_re', r''),
        ('redis_host',''),
        ('redis_port',''),
        ('redis_password',''),
        ('if_frush_ava_redis',False),#在删除不可用代理的时候同时删除redis中的代理，默认False
        ('proxy_least', 30),#大于0的整数， 若代理池可用数量小于它则 刷新代理
        ('init_valid_proxys', 5),# 初始化爬虫时等待的可用代理数量。数值大会导致初始化比较慢，在爬虫进行中也可以同时测试保存的代理。
        ('invalid_limit', 100),#大于0的整数，每个代理成功下载到页面时都会对其计数，若突然无法连接或者被网站拒绝将对这个代理进行invaild操作，若代理爬取的页面数大于该设置数值，则暂时不invaild，切换至另一个代理，并减少其页面计数。默认为200
        ('UserAgents',["Mozilla/5.0 (Macintosh; Intel Mac OS X 10_13_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/63.0.3239.84 Safari/537.36",])
    ]
    #setting.py中的配置初始化为 属性
    def __init__(self, proxy_set=None):
        #print(proxy_set)
        self.scheme = 'https'#目标网站的scheme
        #将上边setting中的key作为class的属性，value就是属性的值。
        self.proxy_set = proxy_set or {}
        #print(self.proxy_set)
        for k, v in self._settings:
            setattr(self, k, self.proxy_set.get(k, v))
        #分别用两个列表维护HTTP和HTTPS的代理
        self.available_proxy = defaultdict(list)#可用的代理
        self.counter_proxy = {}#couter_proxy用作该代理下载的网页数量
        self.redis_server = redis.StrictRedis(host = self.redis_host,port = self.redis_port,password =  self.redis_password)
        self.fresh_proxy()

    @classmethod
    def from_crawler(cls, crawler):
        #print(crawler.settings.getdict('AUTO_PROXY'))
        proxy_set = crawler.settings.getdict('AUTO_PROXY')
        return cls(proxy_set)

    def process_request(self, request, spider):
        #判断是否已经开启了proxy
        if not self._is_enabled_for_request(request):
            return

        #获取请求的url scheme
        parsed = urlparse_cached(request)
        scheme = parsed.scheme
        #可用代理的个数
        if self.len_valid_proxy(scheme) > 0:
            self.set_proxy(request,scheme)
            # if 'download_timeout' not in request.meta:
            request.meta['download_timeout'] = self.download_timeout
        else:
            # 没有可用代理，直连
            if 'proxy' in request.meta:
                del request.meta['proxy']

    def process_response(self, request, response, spider):
        if not self._is_enabled_for_request(request):
            return response

        if response.status in self.ban_code:
            self.invaild_proxy(request.meta['proxy'])
            logger.debug("Proxy[%s] ban because return httpstatuscode:[%s]. ", request.meta['proxy'], str(response.status))
            new_request = request.copy()
            new_request.dont_filter = True
            return new_request

        if self.ban_re:
            try:
                pattern = re.compile(self.ban_re)
            except TypeError:
                logger.error('Wrong "ban_re", please check settings')
                return response
            match = re.search(pattern, response.body)
            if match:
                self.invaild_proxy(request.meta['proxy'])
                logger.debug("Proxy[%s] ban because pattern match:[%s]. ", request.meta['proxy'], str(match))
                new_request = request.copy()
                new_request.dont_filter = True
                return new_request

        p = request.meta['proxy']
        self.counter_proxy[p] = self.counter_proxy.setdefault(p, 1) + 1
        return response

    def process_exception(self, request, exception, spider):
        if isinstance(exception, self.EXCEPTIONS_TO_CHANGE) \
                and request.meta.get('proxy', False):
            self.invaild_proxy(request.meta['proxy'])
            logger.debug("Proxy[%s] connect exception[%s].", request.meta['proxy'], exception)
            new_request = request.copy()
            new_request.dont_filter = True
            return new_request

    def invaild_proxy(self, proxy):
        """
        将代理设为invaild。如果之前该代理已下载超过100页（默认）的资源，则暂时不设置，仅切换代理，并减少其计数。
        """
        if self.counter_proxy.get(proxy, 0) > self.invalid_limit:
            self.counter_proxy[proxy] = self.counter_proxy.get(proxy, 0) - 10
            if self.counter_proxy[proxy] < 0:
                self.counter_proxy[proxy] = 0
        else:
            scheme = proxy.split('://')[0].strip().lower()
            self.available_proxy[scheme].remove(proxy)
            if self.if_frush_ava_redis:
                key = 'ava_'+scheme+'_proxy'
                if self.redis_server.scard(key) >= 5:#防止redis被清空造成无可用代理。小于5的时候就不清理了。
                    self.redis_server.srem(key,proxy)
            # logger.info('Set proxy[%s] invaild.', proxy)

    def set_proxy(self, request,scheme):
        """
        设置代理。
        """
        proxy = random.choice(self.available_proxy[scheme])# 随机选取一个
        request.meta['proxy'] = proxy
        print("still have %d %s proxy:"%(len(self.available_proxy[scheme]),scheme))
        logger.info("still have %d %s proxy:"%(len(self.available_proxy[scheme]),scheme))

        # 可用代理数量小于预设值则扩展代理
        if self.len_valid_proxy(scheme) < self.proxy_least:
            self.fresh_proxy()
        # logger.info('Set proxy. request.meta: %s', request.meta)

    #计算可用代理的数量
    def len_valid_proxy(self,scheme):
        return len(self.available_proxy[scheme])

    #更新代理
    def fresh_proxy(self):
        """
        从redis中获取新代理
        """
        self.available_proxy['http'].clear()
        for proxy in list(self.redis_server.smembers("ava_http_proxy")):
            self.available_proxy['http'].append(proxy.decode('utf-8'))
        
        self.available_proxy['https'].clear()
        for proxy in list(self.redis_server.smembers("ava_https_proxy")):
            self.available_proxy['https'].append(proxy.decode('utf-8'))



    #判断可用代理数量是否达到启动爬虫的最低代理要求
    def _has_valid_proxy(self,scheme):
        if self.len_valid_proxy(scheme) >= self.init_valid_proxys:
            return True

    #判断是否已经开启了proxy
    def _is_enabled_for_request(self, request):
        return self.enable and 'dont_proxy' not in request.meta


if __name__ == '__main__':

    AutoProxyMiddleware()

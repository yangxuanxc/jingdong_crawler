# -*- coding: utf-8 -*-
import scrapy
from scrapy import Request
import json
import math
import time
import re

class BraSpider(scrapy.Spider):
    name = 'bra'
    
    headers = {
        ":authority": "sclub.jd.com",
        ":method": "GET",
        ":scheme": "https",
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        "accept-encoding": "gzip, deflate, br",
        "accept-language:": "zh-CN,zh;q=0.9,en;q=0.8",
        "cache-control": "max-age=0",
        "upgrade-insecure-requests": "1",
        "cookie":"t=8444fb486c0aa650928d929717a48022; _tb_token_=e66e31035631e; cookie2=104997325c258947c404278febd993f7",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_13_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/67.0.3396.99 Safari/537.36",
    }

    base_url = "https://sclub.jd.com/comment/productPageComments.action?productId=17209509645&score=0&sortType=5&pageSize=10&page=%d"

    def start_requests(self):
        for page in range(1,100):
            url = self.base_url%page
            print(url)
            self.headers[':path'] = url
            yield Request(url, self.parse,headers = self.headers)
            #time.sleep(2)

    def parse(self, response):
        content = json.loads(response.text)
        comments = content['comments']
        for comment in comments:
            item = {}
            item['content'] = comment['content']#评论正文
            item['guid'] = comment['guid']#用户id
            item['id'] = comment['id']#评论id
            item['time'] = comment['referenceTime']#评论时间
            item['color'] = self.parse_kuohao(comment['productColor'])#商品颜色
            item['size'] = self.parse_kuohao(comment['productSize'])#商品尺码
            item['userClientShow'] = comment['userClientShow']#购物渠道
            print(item)
            yield item
       
    #干掉括号
    def parse_kuohao(self,text):
        new_text = text
        searchObj1 = re.search( r'（.+）', text, re.M|re.I)
        searchObj2 = re.search( r'\(.+\)', text, re.M|re.I)
        if searchObj1:
            text = searchObj1.group().strip()
            new_text = text.replace(text,'').strip()

        if searchObj2:
            text = searchObj2.group().strip()
            new_text = text.replace(text,'').strip()
        
        return new_text   
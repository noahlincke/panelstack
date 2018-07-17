import re
import urllib.request
import os
import requests
import json
from bs4 import BeautifulSoup


opener=urllib.request.build_opener()
opener.addheaders=[('User-Agent','Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/36.0.1941.0 Safari/537.36')]
urllib.request.install_opener(opener)

url=input("INPUT URL: ")
spliturl=url.split("/")
urltitle=spliturl[4]
source = requests.get(url).text

soup = BeautifulSoup(source, 'lxml')

downloadlinks=[]

for link in soup.findAll('a', attrs={'title': 'Zippyshare Link'}):
	downloadlinks.append(link['href'])

REGEX_1 = r'(\(\'dlbutton\'\)\.href = )(.*)(\;)'
zippy = [downloadlinks[0]]
_links = []
REGEX_2 = r'(\")(.*)(\/\"\ \+\ )(.*)(\ \+\ \")(.*)(\")'
#REGEX_3 = r'(var a = )([0-9]+);'
_session = requests.Session()

def do_main():
	extract, status = parse_link(zippy[0])
	if status:
		p = get_domain(zippy[0])[:-1]+extract
		print("Link = {}".format(p))
		_links.append(p)
		r=requests.get(_links[0])
		link=_links[0]
		fileextension=link[-3:]
		with open(urltitle+"."+fileextension, "wb") as code:
			code.write(r.content)

def get_domain(link):
	return '{uri.scheme}://{uri.netloc}/'.format(uri=urllib.parse.urlparse(link))

def get_text_block(link):
	''' Extracts the part that contains the expression '''
	r = _session.get(link)
	soup = BeautifulSoup(r.content, "lxml")
	text = ''
	for i in soup.find_all("script"):
		text += i.text
	return text


def parse_link(link):
	''' Isolate the expression and extract and make the link '''
	block = get_text_block(link)

	matcher = re.search(REGEX_1, block)
	if matcher == None:
		print("REGEX_1 Failed.")
		print(block)
		return None, False
	else:
		expression = matcher.group(2)
		parts = re.search(REGEX_2, expression)
		if parts == None:
			print("REGEX_2 Failed.")
			print(expression)
			return None, False
		else:
			part_1 = parts.group(2)
			part_3 = parts.group(6)
			part_2 = eval(parts.group(4))
			extract = "{}/{}{}".format(part_1, part_2, part_3)
			extract = re.sub('/pd/', '/d/', extract)
			return extract, True		

def get_value_of_a(script_block):
	matcher = re.search(REGEX_3, script_block)
	if matcher == None:
		return None
	a = int(matcher.group(2))
	return a

do_main()
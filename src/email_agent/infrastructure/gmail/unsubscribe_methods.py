"""Extract bounded unsubscribe links from one actual original, without visiting links."""
import re
from html import unescape
from html.parser import HTMLParser
from email_agent.infrastructure.gmail.content_reader import walk_parts,decode_data,headers_of

RELEVANT=re.compile(r'unsubscribe|opt[ -]?out|email preferences|cancel newsletter|الغاء الاشتراك|إلغاء الاشتراك',re.I)

class LinkParser(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.current=None;self.links=[];self.ignored=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','iframe','object'):self.ignored+=1
        if tag=='a' and not self.ignored:self.current=[dict(attrs).get('href',''),'']
    def handle_data(self,data):
        if self.current and not self.ignored:self.current[1]+=data[:500]
    def handle_endtag(self,tag):
        if tag in ('script','style','iframe','object'):self.ignored=max(0,self.ignored-1)
        if tag=='a' and self.current:
            if not re.search(r'unsubscribe all|all subscriptions|all lists|جميع الاشتراكات',self.current[1],re.I) and (RELEVANT.search(self.current[1]) or RELEVANT.search(self.current[0])):self.links.append(self.current[0])
            self.current=None

class GmailUnsubscribeMethods:
    def __init__(self,clients):self.clients=clients
    def read(self,account,id):
        payload=self.clients(account).get_full_message(id).get('payload',{})
        headers=headers_of(payload);links=[];remaining=100000
        for _,part in walk_parts(payload):
            kind=part.get('mimeType','');data=part.get('body',{}).get('data')
            if kind not in ('text/html','text/plain') or not data or remaining<=0:continue
            text=decode_data(data,2*1024*1024).decode('utf-8',errors='replace')[:remaining];remaining-=len(text)
            if kind=='text/html':
                parser=LinkParser();parser.feed(text);links.extend(parser.links)
            else:
                for match in re.finditer(r'https://[^\s<>]+',text):
                    if RELEVANT.search(text[max(text.rfind('\n',0,match.start())+1,match.start()-100):match.start()]) or RELEVANT.search(match[0]):links.append(match[0])
        return {'headers':{key:headers[key] for key in ('list-id','list-unsubscribe','list-unsubscribe-post') if key in headers},'body_links':list(dict.fromkeys(unescape(url).strip() for url in links if isinstance(url,str) and len(url)<=2000))[:10]}

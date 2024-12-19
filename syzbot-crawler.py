#!/usr/bin/python3
import requests
import time
import os
import re
import unicodedata
import sqlite3
import datetime

class MyObj:
    pass

def slugify(value):
    """
    Normalizes string, converts to lowercase, removes non-alpha characters,
    and converts spaces to hyphens.
    """
    value = re.sub(rb'[^\w-]', b'-', value).strip().lower()
    return value

class Cache: # id is link
    def __init__(self, dataDir, manifest):
        self.dataDir = dataDir.encode()
        if not os.path.exists(self.dataDir):
            os.mkdir(self.dataDir)
            assert os.path.exists(self.dataDir)
        self.manifest = manifest
        if os.path.exists(self.manifest):
            data = open(self.manifest, 'rb').read()
        else:
            data = b''
        self.entries = {}
        for i in re.finditer(rb'<entry>\s*?<link>(.+?)</link>\s*?<time>(.+?)</time>\s*?<path>(.+?)</path>\s*</entry>', data, re.MULTILINE):
            link, time, path = i.groups()
            self.entries[link] = [time, path]
        print(b"Cache size: %d" % len(self.entries))

    def now(self):
        return datetime.datetime.now().strftime('%Y-%m-%d %H:%M').encode('ascii')
        
    def add(self, link, time, data):
        if type(link) != bytes:
            link = link.encode()
        if type(time) != bytes:
            time = time.encode()
        if type(data) != bytes:
            data = data.encode()
        # if link exists, overwrite file
        if link in self.entries:
            ent = self.entries[link]
            ent[0] = time
            open(ent[1], 'wb').write(data)
        else: # create new file and add to manifest
            fileName = slugify(link)
            filePath = os.path.join(self.dataDir, fileName)
            while os.path.isfile(filePath):    
                filePath += b'a'
            self.entries[link] = [time, fileName]
            open(filePath, 'wb').write(data)
            f = open(self.manifest, 'ab')
            f.write(b'<entry>\n<link>%s</link>\n<time>%s</time>\n<path>%s</path>\n</entry>' % (link, time, fileName))
            f.close()
                
    def has(self, link):
        if type(link) != bytes:
            link = link.encode()
        return link in self.entries

    def getData(self, link):
        if type(link) != bytes:
            link = link.encode()
        fileName = self.entries[link][1]
        filePath = os.path.join(self.dataDir, fileName)
        return open(filePath, 'rb').read()

#link = "https://syzkaller.appspot.com/upstream"
host = "https://syzkaller.appspot.com"
proxies = {
     'http': 'socks5://127.0.0.1:7890',
     'https': 'socks5://127.0.0.1:7890'
}

cache = Cache('cache', 'cache.txt')

bug_pattern = rb'<td class="title">.+?<a href="(.*?)">(.*?)</a>.+?<\/td>.+?<td class="stat">(.*?)<\/td>.+?<td class="bisect_status">(.*?)<\/td>.*?<td class="bisect_status">(.*?)<\/td>.*?<td class="stat ">(.+?)<\/td>.*?<td class="stat">(.+?)<\/td>.*?<td class="stat">.*?<a href="(.*?)">.+?<\/a>.*?<\/td>'
crash_pattern = rb'<td class="manager">(.*?)<\/td>.+?<td class="time">(.+?)<\/td>.+?<td class="kernel".*?>(.*?)<\/td>.+?<td class="repro">(.*?)<\/td>.+?<td class="repro">(.*?)<\/td>.*?<td class="repro">(.*?)<\/td>.+?<td class="repro">(.*?)<\/td>'

def get_bugs(main_link):
    global cache, bug_pattern, host, proxies
    if type(main_link) != bytes:
        main_link = main_link.encode()
    if type(host) != bytes:
        host = host.encode()
    if cache.has(main_link):
        data = cache.getData(main_link)
    else:
        print(b'Requesting '+main_link)
        # data = requests.get(main_link, proxies=proxies).text.encode()
        data = requests.get(main_link).text.encode()
        print(b'Got '+main_link)
        curTime = cache.now()
        cache.add(main_link, curTime, data)

    bugs = re.findall(bug_pattern, data, re.MULTILINE | re.DOTALL)
    bug_objs = []
    for bug in bugs:
        bug_link = host+bug[0]
        if not cache.has(bug_link):
            print(b'Requesting '+bug_link)
            # data = requests.get(bug_link, proxies=proxies).content
            data = requests.get(bug_link).content
            print(b'Got '+bug_link)
            cache.add(bug_link, cache.now(), data)
        else:
            data = cache.getData(bug_link)
        # search for crashes
        crashes = re.findall(crash_pattern, data, re.MULTILINE | re.DOTALL)
        for i in range(len(crashes)):
            crash = crashes[i]
            print(crash)
            syz = crash[5]
            syz_link = re.search(rb'<a href="(.+?)">syz<\/a>', syz)
            if syz_link:
                print(syz_link.group(1))
                syz_link = host+syz_link.group(1)
                if not cache.has(syz_link):
                    print(b'Requesting '+syz_link)
                    syz_data = requests.get(syz_link).content
                    print(b'Got '+syz_link)
                    cache.add(syz_link, cache.now(), syz_data)
                else:
                    syz_data = cache.getData(syz_link)
                #print(syz_data)
            else:
                syz_data = b''
            crashes[i] = list(crash)+[syz_data]
        bug_obj = MyObj()
        bug_obj.bug = bug
        bug_obj.crashes = crashes
        bug_objs.append(bug_obj)

    return bug_objs


def connect_db():
    path = 'syzkaller.db'
    if os.path.exists(path):
        conn = sqlite3.connect(path)
    else:
        conn = sqlite3.connect(path)
        c = conn.cursor()
        c.execute('''CREATE TABLE bugs
             (id TEXT NOT NULL PRIMARY KEY, name TEXT, bisect TEXT, count INTEGER, last TEXT, reported TEXT)''')
        c.execute('''CREATE TABLE crashes \
             (bug_id TEXT NOT NULL, manager TEXT, tdate TEXT, kernel TEXT, log TEXT, report TEXT, syz TEXT, C text, syz_data TEXT, \
             PRIMARY KEY (bug_id, tdate, kernel))''')
        conn.commit()
    return conn


def save_bugs(bugs):
    conn = connect_db()
    c = conn.cursor()
    for bug_obj in bugs:
        bug = list(bug_obj.bug)
        for i in range(len(bug)):
            bug[i] = bug[i].strip().decode()
        bug_id = bug[0]
        c.execute('SELECT * FROM bugs WHERE id=?', (bug_id, ))
        rows = c.fetchall()
        if len(rows) == 0:
            c.execute('INSERT INTO bugs VALUES (?, ?, ?, ?, ?, ?)', (bug[0], bug[1], bug[3] , bug[4], bug[5], bug[6],))
        crashes = bug_obj.crashes
        for crash in crashes:
            crash = list(crash)
            for i in range(len(crash)):
                crash[i] = crash[i].strip().decode()
            c.execute('SELECT * FROM crashes WHERE bug_id=? AND tdate=? AND kernel=?', (bug_id, crash[1], crash[2]))
            rows = c.fetchall()
            if len(rows) == 0:
                c.execute('INSERT INTO crashes VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)', (bug_id, crash[0], crash[1], crash[2], crash[3], crash[4], crash[5], crash[6], crash[7]))
    conn.commit()
    conn.close()

links = ['https://syzkaller.appspot.com/upstream', 'https://syzkaller.appspot.com/linux-5.15', 'https://syzkaller.appspot.com/linux-6.1']

for link in links:
    bugs = get_bugs(link)
    save_bugs(bugs)
    # bugs = get_bugs(link + '/fixed')
    # save_bugs(bugs)

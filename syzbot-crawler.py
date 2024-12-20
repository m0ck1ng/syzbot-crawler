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

host = "https://syzkaller.appspot.com"
proxies = {
     'http': 'socks5://127.0.0.1:7890',
     'https': 'socks5://127.0.0.1:7890'
}

cache = Cache('cache', 'cache.txt')

bug_pattern = rb'<td class="title">.+?<a href="(.*?)">(.+?)</a>.+?<\/td>.+?<td class="stat">(.*?)<\/td>.+?<td class="bisect_status">(.*?)<\/td>.*?<td class="bisect_status">(.*?)<\/td>.*?<td class="stat ">(.+?)<\/td>.*?<td class="stat">(.+?)<\/td>.*?<td class="stat">.*?<a href="(.*?)">.+?<\/a>.*?<\/td>'
crash_pattern = rb'<td class="time">(.+?)<\/td>.+?<td class="kernel".*?>.+?<a href=".+?">(.+?)<\/a>.+?<\/td>.+?<td class="repro">.*?<\/td>.+?<td class="repro">.*?<\/td>.*?<td class="repro">(.*?)<\/td>.+?<td class="repro">(.*?)<\/td>'


def fetch_data(url, cache, host):
    if cache.has(url):
        return cache.getData(url)

    print(f"Requesting {url.decode()}")
    data = requests.get(url).content
    print(f"Got {url.decode()}")
    cache.add(url, cache.now(), data)
    return data

def extract_repro(repro, host, cache):
    repro_link = re.search(rb'<a href="(.+?)">.+?<\/a>', repro)
    if repro_link:
        repro_url = host + repro_link.group(1)
        return fetch_data(repro_url, cache, host)
    return b''

def parse_crashes(data, host, crash_pattern, cache):
    # crash: tuple (time, kernel_hash, syz_repro_url, c_repro_url)
    crashes = re.findall(crash_pattern, data, re.MULTILINE | re.DOTALL)
    crash_objs = []

    for crash in crashes:
        syz_data = extract_repro(crash[2], host, cache)
        c_data = extract_repro(crash[3], host, cache)
        crash_objs.append(list(crash) + [syz_data])

    return crash_objs

def get_bugs(main_link):
    global cache, bug_pattern, host, proxies
    main_link = main_link.encode() if isinstance(main_link, str) else main_link
    host = host.encode() if isinstance(host, str) else host
    
    data = fetch_data(main_link, cache, host)
    # bug: tuple (id, name, repro, cause, fix, count, last, reported)
    bugs = re.findall(bug_pattern, data, re.MULTILINE | re.DOTALL)

    bug_objs = []
    for bug in bugs:
        bug_url = host+bug[0]
        bug_data = fetch_data(bug_url, cache, host)
        crashes = parse_crashes(bug_data, host, crash_pattern, cache)

        bug_obj = MyObj()
        bug_obj.bug = bug
        bug_obj.crashes = crashes
        bug_objs.append(bug_obj)

    return bug_objs


def connect_db():
    path = 'syzbot-corpus.db'
    if os.path.exists(path):
        conn = sqlite3.connect(path)
    else:
        conn = sqlite3.connect(path)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE bugs (
                id TEXT NOT NULL PRIMARY KEY,
                name TEXT,
                repro TEXT,
                cause TEXT,
                fix TEXT,
                count INTEGER,
                last TEXT,
                reported TEXT
            )
        """)
        c.execute("""
            CREATE TABLE crashes (
                bug_id TEXT NOT NULL,
                tdate TEXT NOT NULL,
                kernel TEXT NOT NULL,
                syz TEXT,
                cprog TEXT,
                syz_data TEXT,
                PRIMARY KEY (bug_id, tdate, kernel)
            )
        """)
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
            c.execute('INSERT INTO bugs VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (bug[0], bug[1], bug[3] , bug[4], bug[5], bug[6], bug[6], bug[7]))
        crashes = bug_obj.crashes
        for crash in crashes:
            crash = list(crash)
            for i in range(len(crash)):
                crash[i] = crash[i].strip().decode()
            c.execute('SELECT * FROM crashes WHERE bug_id=? AND tdate=? AND kernel=?', (bug_id, crash[1], crash[2]))
            rows = c.fetchall()
            if len(rows) == 0:
                c.execute('INSERT OR IGNORE INTO crashes VALUES(?, ?, ?, ?, ?, ?)', (bug_id, crash[0], crash[1], crash[2], crash[3], crash[4]))
    conn.commit()
    conn.close()

# links = ['https://syzkaller.appspot.com/upstream', 'https://syzkaller.appspot.com/linux-5.15', 'https://syzkaller.appspot.com/linux-6.1']
links = ['https://syzkaller.appspot.com/upstream']

for link in links:
    bugs = get_bugs(link)
    save_bugs(bugs)
    # bugs = get_bugs(link + '/fixed')
    # save_bugs(bugs)

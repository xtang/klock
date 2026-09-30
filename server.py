"""Klock: a small, dependency-free time tracker. Python 3.9+."""
import google_login
import calendar
import csv
import io
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode

ROOT = Path(__file__).parent
DB_PATH = Path(os.environ.get('KLOCK_DB', ROOT / 'data/klock.db'))
COLORS = ['#507d69', '#c28b53', '#7d78ac', '#648ba4', '#c87972']

@contextmanager
def connect():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        with db:
            yield db
    finally:
        db.close()

def initialize():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS members(id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, member_id TEXT REFERENCES members(id));
        CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, name TEXT NOT NULL, client TEXT NOT NULL DEFAULT '', color TEXT NOT NULL, archived INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS entries(id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id), member_id TEXT REFERENCES members(id), description TEXT NOT NULL, date TEXT NOT NULL, seconds INTEGER NOT NULL CHECK(seconds>=0));
        CREATE TABLE IF NOT EXISTS timers(member_id TEXT PRIMARY KEY REFERENCES members(id), project_id TEXT REFERENCES projects(id), description TEXT NOT NULL, started INTEGER NOT NULL, timezone_offset INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS invites(token TEXT PRIMARY KEY, name TEXT NOT NULL, created INTEGER NOT NULL, used INTEGER DEFAULT 0);
        ''')
        google_login.initialize(db)

def uid(): return secrets.token_hex(12)
def rows(db, sql, params=()): return [dict(r) for r in db.execute(sql, params)]
def hours(seconds): return str((Decimal(seconds) / 3600).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
def duration(seconds): return f'{seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}'
def split_time(start, end, offset):
    """Split a timer at local midnight without losing seconds."""
    cursor = start
    while cursor < end:
        local = datetime.fromtimestamp(cursor - offset * 60, timezone.utc)
        midnight = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        boundary = calendar.timegm(midnight.timetuple()) + offset * 60
        stop = min(end, boundary)
        yield local.date().isoformat(), stop-cursor
        cursor = stop

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass
    def send(self, status, payload, mime='application/json; charset=utf-8', headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode() if mime.startswith('application/json') else payload
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'same-origin')
        for k, v in (headers or {}).items():
            for value in (v if isinstance(v, list) else [v]): self.send_header(k, value)
        self.end_headers()
        self.wfile.write(body)
    def member(self, db):
        cookie = SimpleCookie(self.headers.get('Cookie', ''))
        token = cookie.get('klock_session')
        if not token: return None
        row = db.execute('SELECT m.* FROM members m JOIN sessions s ON s.member_id=m.id WHERE s.token=?', (token.value,)).fetchone()
        return dict(row) if row else None
    def login(self, db, member_id):
        token = secrets.token_urlsafe(32)
        db.execute('INSERT INTO sessions VALUES (?,?)', (token, member_id))
        return {'Set-Cookie': google_login.cookie('klock_session', token, 31536000)}
    def auth_info(self, db, me=None):
        account = db.execute('SELECT email FROM google_accounts WHERE member_id=?', (me['id'],)).fetchone() if me else None
        return {'google_enabled': google_login.enabled(), 'google_required': google_login.configured(),
                'google_linked': bool(account), 'email': account['email'] if account else None}

    def auth_error(self, message, invite=''):
        query = {'auth_error': message}
        if invite: query['invite'] = invite
        self.send(303, b'', 'text/plain', {'Location': '/?' + urlencode(query),
                  'Set-Cookie': google_login.cookie('klock_oauth', '', 0)})

    def google_start(self, query):
        invite = query.get('invite', [''])[0]
        try:
            with connect() as db:
                url, cookie = google_login.begin(db, invite)
            self.send(303, b'', 'text/plain', {'Location': url, 'Set-Cookie': cookie})
        except ValueError as error:
            self.auth_error(str(error), invite)

    def google_callback(self, query):
        flow = None
        try:
            with connect() as db:
                db.execute('BEGIN IMMEDIATE')
                flow = google_login.consume(db, query.get('state', [''])[0],
                                            google_login.cookie_value(self.headers, 'klock_oauth'))
            if query.get('error'):
                raise ValueError('Google 登录已取消，你可以重新尝试')
            code = query.get('code', [''])[0]
            if not code: raise ValueError('Google 未返回授权码，请重新登录')
            try:
                claims = google_login.exchange(code, flow)
            except Exception:
                # Never echo provider responses, codes, tokens or credentials to the browser/logs.
                raise ValueError('Google 身份验证未完成，请检查服务配置与网络后重试') from None
            with connect() as db:
                db.execute('BEGIN IMMEDIATE')
                mid = google_login.resolve_member(db, claims, flow)
                headers = self.login(db, mid)
                old_session = google_login.cookie_value(self.headers, 'klock_session')
                if old_session:
                    db.execute('DELETE FROM sessions WHERE token=?', (old_session,))
            headers['Set-Cookie'] = [headers['Set-Cookie'], google_login.cookie('klock_oauth', '', 0)]
            headers['Location'] = '/'
            self.send(303, b'', 'text/plain', headers)
        except (ValueError, sqlite3.IntegrityError) as error:
            self.auth_error(str(error), flow['invite'] if flow else '')

    def do_GET(self):
        path = urlparse(self.path)
        if path.path == '/api/auth/google/start':
            return self.google_start(parse_qs(path.query))
        if path.path == '/api/auth/google/callback':
            return self.google_callback(parse_qs(path.query))
        if not path.path.startswith('/api/'):
            file = ROOT / 'public' / {'/app.js':'app.js','/styles.css':'styles.css'}.get(path.path, 'index.html')
            mime = {'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8'}[file.suffix]
            return self.send(200, file.read_bytes(), mime)
        with connect() as db:
            me = self.member(db)
            if path.path == '/api/state':
                configured = db.execute('SELECT count(*) FROM members').fetchone()[0] > 0
                if not me: return self.send(200, {'me': None, 'configured': configured, 'auth': self.auth_info(db)})
                return self.send(200, {'me':me, 'auth': self.auth_info(db, me), 'projects':rows(db,'SELECT * FROM projects ORDER BY rowid'), 'members':rows(db,'SELECT * FROM members ORDER BY rowid'), 'entries':rows(db,'SELECT * FROM entries ORDER BY date DESC,rowid DESC'), 'timer':next(iter(rows(db,'SELECT * FROM timers WHERE member_id=?',(me['id'],))),None), 'invites':rows(db,'SELECT * FROM invites WHERE used=0 AND created>?',(int(time.time())-604800,)) if me['role']=='owner' else []})
            if not me: return self.send(401, {'error':'请先加入工作空间'})
            if path.path == '/api/export':
                month = parse_qs(path.query).get('month',[''])[0]
                try: first = datetime.strptime(month, '%Y-%m'); assert first.strftime('%Y-%m') == month
                except (ValueError, AssertionError): return self.send(400, {'error':'月份格式无效'})
                data = rows(db, 'SELECT p.name project,e.description,e.date,e.seconds,m.name user FROM entries e JOIN projects p ON p.id=e.project_id JOIN members m ON m.id=e.member_id WHERE substr(e.date,1,7)=? ORDER BY e.date DESC,e.rowid DESC', (month,))
                last = f'{month}-{calendar.monthrange(first.year,first.month)[1]:02}'
                fmt = parse_qs(path.query).get('format',['csv'])[0]
                if fmt == 'json':
                    return self.send(200, {'period_start':month+'-01','period_end':last,'scope_note':'仅统计工作空间中已保存的记录，不含正在计时的记录。','entries':[{'project':e['project'],'description':e['description'],'date':e['date'],'duration':duration(e['seconds']),'user':e['user']} for e in data]}, headers={'Content-Disposition':f'attachment; filename="Klock_{month}.json"'})
                out=io.StringIO(); writer=csv.writer(out)
                writer.writerow(['Project','Description','Start Date','Duration (h)','Duration (seconds)','User'])
                def safe(s): return "'"+s if isinstance(s,str) and s.lstrip().startswith(('=','+','-','@')) else s
                for e in data: writer.writerow([safe(e['project']),safe(e['description']),e['date'],hours(e['seconds']),e['seconds'],safe(e['user'])])
                writer.writerow(['TOTAL','','',hours(sum(e['seconds'] for e in data)),sum(e['seconds'] for e in data),''])
                return self.send(200, ('\ufeff'+out.getvalue()).encode(), 'text/csv; charset=utf-8', {'Content-Disposition':f'attachment; filename="Klock_{month}.csv"'})
            self.send(404, {'error':'未找到'})
    def do_POST(self):
        origin = self.headers.get('Origin')
        if origin and urlparse(origin).netloc != self.headers.get('Host'): return self.send(403, {'error':'请求来源不匹配'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length>65536: raise ValueError('请求过大')
            data=json.loads(self.rfile.read(length) or '{}')
            if not isinstance(data,dict): raise ValueError('请求格式错误')
            with connect() as db:
                db.execute('BEGIN IMMEDIATE')
                me=self.member(db); path=urlparse(self.path).path
                def text(key, required=True, maxlen=250):
                    value=str(data.get(key,'')).strip()
                    if required and not value: raise ValueError('请填写完整信息')
                    if len(value)>maxlen: raise ValueError('内容过长')
                    return value
                if path in ('/api/setup', '/api/join') and google_login.configured():
                    raise ValueError('请使用 Google 账号登录')
                if path == '/api/auth/logout':
                    token = google_login.cookie_value(self.headers, 'klock_session')
                    db.execute('DELETE FROM sessions WHERE token=?', (token,))
                    db.commit()
                    return self.send(200, {'ok': True}, headers={'Set-Cookie': google_login.cookie('klock_session', '', 0)})
                if path == '/api/auth/google/link':
                    if not me: return self.send(401, {'error': '请先登录原本地身份'})
                    if db.execute('SELECT 1 FROM google_accounts WHERE member_id=?', (me['id'],)).fetchone():
                        raise ValueError('此成员已绑定 Google 账号')
                    url, cookie = google_login.begin(db, link_session=google_login.cookie_value(self.headers, 'klock_session'))
                    db.commit()
                    return self.send(200, {'url': url}, headers={'Set-Cookie': cookie})
                if path == '/api/setup':
                    if db.execute('SELECT count(*) FROM members').fetchone()[0]: raise ValueError('工作空间已创建，请使用邀请链接加入')
                    mid=uid(); db.execute('INSERT INTO members VALUES (?,?,?)',(mid,text('name',maxlen=60),'owner'))
                    headers=self.login(db,mid); db.commit(); return self.send(200,{'ok':True},headers=headers)
                if path == '/api/join':
                    token=text('token'); invite=db.execute('SELECT * FROM invites WHERE token=? AND used=0 AND created>?',(token,int(time.time())-604800)).fetchone()
                    if not invite: raise ValueError('邀请已失效或已被使用，请联系管理员重新邀请')
                    mid=uid(); db.execute('INSERT INTO members VALUES (?,?,?)',(mid,text('name',maxlen=60),'member'))
                    db.execute('UPDATE invites SET used=1 WHERE token=?',(token,))
                    headers=self.login(db,mid); db.commit(); return self.send(200,{'ok':True},headers=headers)
                if not me: return self.send(401,{'error':'请先加入工作空间'})
                if path == '/api/projects':
                    name=text('name',maxlen=80); client=text('client',False,80)
                    db.execute('INSERT INTO projects(id,name,client,color) VALUES (?,?,?,?)',(uid(),name,client,COLORS[db.execute('SELECT count(*) FROM projects').fetchone()[0]%len(COLORS)]))
                elif path == '/api/projects/archive':
                    if me['role']!='owner': raise ValueError('仅管理员可以归档项目')
                    pid=text('id')
                    if db.execute('SELECT 1 FROM timers WHERE project_id=?',(pid,)).fetchone(): raise ValueError('项目仍有正在进行的计时')
                    db.execute('UPDATE projects SET archived=1-archived WHERE id=?',(pid,))
                elif path in ('/api/timer/start','/api/entries'):
                    pid=text('project_id'); description=text('description',False,1000)
                    if not db.execute('SELECT 1 FROM projects WHERE id=? AND archived=0',(pid,)).fetchone(): raise ValueError('请选择有效项目')
                    if path.endswith('/start'):
                        if db.execute('SELECT 1 FROM timers WHERE member_id=?',(me['id'],)).fetchone(): raise ValueError('已有正在进行的计时')
                        offset=int(data.get('timezone_offset',0))
                        if abs(offset)>840: raise ValueError('时区无效')
                        db.execute('INSERT INTO timers VALUES (?,?,?,?,?)',(me['id'],pid,description,int(time.time()),offset))
                    else:
                        date=text('date'); parsed=datetime.strptime(date,'%Y-%m-%d')
                        if parsed.strftime('%Y-%m-%d')!=date: raise ValueError('日期无效')
                        seconds=int(data.get('seconds',0))
                        if seconds<=0 or seconds>86400: raise ValueError('时长应在 1 秒至 24 小时之间')
                        db.execute('INSERT INTO entries VALUES (?,?,?,?,?,?)',(uid(),pid,me['id'],description,date,seconds))
                elif path == '/api/timer/stop':
                    timer=db.execute('SELECT * FROM timers WHERE member_id=?',(me['id'],)).fetchone()
                    if not timer: raise ValueError('没有正在进行的计时')
                    end=max(int(time.time()),timer['started']+1)
                    for date, seconds in split_time(timer['started'],end,timer['timezone_offset']):
                        db.execute('INSERT INTO entries VALUES (?,?,?,?,?,?)',(uid(),timer['project_id'],me['id'],timer['description'],date,seconds))
                    db.execute('DELETE FROM timers WHERE member_id=?',(me['id'],))
                elif path == '/api/entries/delete':
                    entry=db.execute('SELECT * FROM entries WHERE id=?',(text('id'),)).fetchone()
                    if not entry or entry['member_id']!=me['id']: raise ValueError('只能删除自己的记录')
                    db.execute('DELETE FROM entries WHERE id=?',(entry['id'],))
                elif path == '/api/invites':
                    if me['role']!='owner': raise ValueError('仅管理员可以邀请协作者')
                    token=secrets.token_urlsafe(24)
                    db.execute('INSERT INTO invites VALUES (?,?,?,0)',(token,text('name',maxlen=60),int(time.time())))
                elif path == '/api/invites/revoke':
                    if me['role']!='owner': raise ValueError('仅管理员可以撤销邀请')
                    db.execute('DELETE FROM invites WHERE token=?',(text('token'),))
                else: return self.send(404,{'error':'未找到'})
                db.commit(); self.send(200,{'ok':True})
        except (ValueError,TypeError,KeyError,sqlite3.IntegrityError) as error: self.send(400,{'error':str(error) or '数据无效'})

if __name__ == '__main__':
    # Literal dotenv values only: do not execute shell content or override the process environment.
    env_file = ROOT / '.env'
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                if key.strip() in ('GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'KLOCK_BASE_URL', 'KLOCK_BOOTSTRAP_EMAIL', 'PORT', 'HOST', 'KLOCK_DB'):
                    os.environ.setdefault(key.strip(), value.strip().strip('"\''))
    DB_PATH = Path(os.environ.get('KLOCK_DB', ROOT / 'data/klock.db'))
    if google_login.configured():
        if not google_login.enabled():
            raise SystemExit('Google 登录配置不完整：请检查 .env 中的凭据和 KLOCK_BASE_URL')
        try:
            from google.oauth2 import id_token
            from google.auth.transport.requests import Request
        except ImportError:
            raise SystemExit('请先安装 Google 登录依赖：python3 -m pip install -r requirements.txt')
    initialize()
    port=int(os.environ.get('PORT','5188'))
    httpd=ThreadingHTTPServer((os.environ.get('HOST','127.0.0.1'),port),Handler)
    print(f'Klock is running at http://localhost:{port}',flush=True)
    httpd.serve_forever()

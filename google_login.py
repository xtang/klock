"""Google authorization-code login. Tokens never reach the frontend."""
import base64
import hashlib
import json
import os
import secrets
import time
from http.cookies import SimpleCookie
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


def settings():
    origin = os.environ.get('KLOCK_BASE_URL', 'http://localhost:5188').rstrip('/')
    parsed = urlparse(origin)
    if (parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.path or
            parsed.query or parsed.fragment or parsed.username or parsed.password or
            (parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'))):
        raise ValueError('KLOCK_BASE_URL 必须是 HTTPS 地址（本机 localhost 可使用 HTTP）')
    return {'client_id': os.environ.get('GOOGLE_CLIENT_ID', '').strip(),
            'client_secret': os.environ.get('GOOGLE_CLIENT_SECRET', '').strip(),
            'origin': origin, 'redirect_uri': origin + '/api/auth/google/callback'}


def configured():
    return bool(os.environ.get('GOOGLE_CLIENT_ID') or os.environ.get('GOOGLE_CLIENT_SECRET'))


def enabled():
    try:
        cfg = settings()
        return bool(cfg['client_id'] and cfg['client_secret'])
    except ValueError:
        return False


def cookie(name, value, age):
    secure = '; Secure' if os.environ.get('KLOCK_BASE_URL', '').startswith('https://') else ''
    return f'{name}={value}; HttpOnly; SameSite=Lax; Path=/; Max-Age={age}{secure}'


def cookie_value(headers, name):
    jar = SimpleCookie(headers.get('Cookie', ''))
    return jar[name].value if name in jar else ''


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS google_accounts(
        subject TEXT PRIMARY KEY, member_id TEXT NOT NULL UNIQUE REFERENCES members(id),
        email TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS oauth_flows(
        state TEXT PRIMARY KEY, browser_hash TEXT NOT NULL, verifier TEXT NOT NULL,
        nonce TEXT NOT NULL, invite TEXT NOT NULL, link_session TEXT NOT NULL,
        expires INTEGER NOT NULL);
    ''')


def begin(db, invite='', link_session=''):
    cfg = settings()
    if not enabled():
        raise ValueError('Google 登录尚未配置完成，请联系工作空间管理员')
    now = int(time.time())
    if invite and not db.execute('SELECT 1 FROM invites WHERE token=? AND used=0 AND created>?',
                                (invite, now-604800)).fetchone():
        raise ValueError('邀请已失效或已被使用，请联系管理员重新邀请')
    state, browser, verifier, nonce = (secrets.token_urlsafe(32) for _ in range(4))
    db.execute('DELETE FROM oauth_flows WHERE expires<=?', (now,))
    db.execute('INSERT INTO oauth_flows VALUES (?,?,?,?,?,?,?)',
               (state, digest(browser), verifier, nonce, invite, link_session, now+600))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    url = 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
        'client_id': cfg['client_id'], 'redirect_uri': cfg['redirect_uri'],
        'response_type': 'code', 'scope': 'openid email profile', 'state': state,
        'nonce': nonce, 'code_challenge': challenge, 'code_challenge_method': 'S256',
        'prompt': 'select_account',
    })
    return url, cookie('klock_oauth', browser, 600)


def consume(db, state, browser):
    row = db.execute('SELECT * FROM oauth_flows WHERE state=?', (state,)).fetchone()
    if (not row or not browser or row['expires'] <= int(time.time()) or
            not secrets.compare_digest(row['browser_hash'], digest(browser))):
        raise ValueError('登录请求已过期或浏览器不匹配，请重新登录')
    db.execute('DELETE FROM oauth_flows WHERE state=?', (state,))
    return dict(row)


def verify_id_token(token, client_id):
    # Google's library validates signature, issuer, audience and expiration using Google's certificates.
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2 import id_token
    transport = GoogleRequest()
    def request(url, method='GET', **kwargs):
        kwargs['timeout'] = 10
        return transport(url, method=method, **kwargs)
    return id_token.verify_oauth2_token(token, request, client_id)


def exchange(code, flow):
    cfg = settings()
    request = Request('https://oauth2.googleapis.com/token', data=urlencode({
        'code': code, 'client_id': cfg['client_id'], 'client_secret': cfg['client_secret'],
        'redirect_uri': cfg['redirect_uri'], 'grant_type': 'authorization_code',
        'code_verifier': flow['verifier'],
    }).encode(), headers={'Content-Type': 'application/x-www-form-urlencoded'})
    with urlopen(request, timeout=15) as response:
        tokens = json.load(response)
    claims = verify_id_token(tokens['id_token'], cfg['client_id'])
    if (claims.get('nonce') != flow['nonce'] or claims.get('email_verified') is not True or
            not isinstance(claims.get('sub'), str) or not claims['sub'] or
            not isinstance(claims.get('email'), str) or not claims['email']):
        raise ValueError('Google 身份校验失败，请重新登录')
    return claims


def resolve_member(db, claims, flow):
    """Caller holds BEGIN IMMEDIATE; enrollment and invite consumption are atomic."""
    subject, email = claims['sub'], claims['email']
    account = db.execute('SELECT * FROM google_accounts WHERE subject=?', (subject,)).fetchone()
    if flow['link_session']:
        session = db.execute('SELECT member_id FROM sessions WHERE token=?', (flow['link_session'],)).fetchone()
        if not session:
            raise ValueError('原登录已失效，请回到原浏览器重新绑定')
        mid = session['member_id']
        linked = db.execute('SELECT subject FROM google_accounts WHERE member_id=?', (mid,)).fetchone()
        if (account and account['member_id'] != mid) or (linked and linked['subject'] != subject):
            raise ValueError('账号已经绑定，无法合并或替换其他成员的身份')
    elif account:
        mid = account['member_id']
    else:
        count = db.execute('SELECT count(*) FROM members').fetchone()[0]
        if not count:
            allowed = os.environ.get('KLOCK_BOOTSTRAP_EMAIL', '').strip().casefold()
            if (settings()['origin'].startswith('https://') and not allowed) or (allowed and email.casefold() != allowed):
                raise ValueError('工作空间尚未创建，请使用指定的管理员 Google 账号登录')
        if count:
            invite = db.execute('SELECT 1 FROM invites WHERE token=? AND used=0 AND created>?',
                                (flow['invite'], int(time.time())-604800)).fetchone()
            if not invite:
                raise ValueError('这个 Google 账号尚未加入，请使用邀请链接；已有本地身份请在原浏览器中绑定')
            db.execute('UPDATE invites SET used=1 WHERE token=?', (flow['invite'],))
        mid = secrets.token_hex(12)
        name = str(claims.get('name') or email.split('@')[0])[:60]
        db.execute('INSERT INTO members VALUES (?,?,?)', (mid, name, 'member' if count else 'owner'))
    db.execute('INSERT INTO google_accounts VALUES (?,?,?) ON CONFLICT(subject) DO UPDATE SET email=excluded.email',
               (subject, mid, email))
    return mid

"""Read-only deployment checks; deliberately never prints OAuth URLs or cookies."""
import argparse
import os
from render import domain_from_base_url
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--base-url', default=os.environ.get('KLOCK_BASE_URL'))
parser.add_argument('--origin', action='store_true', help='Connect to localhost while validating the public TLS hostname')
args = parser.parse_args()
try:
    domain = domain_from_base_url(args.base_url or '')
except ValueError as error:
    parser.error(str(error))
base = 'https://' + domain
if args.origin:
    resolve = socket.getaddrinfo
    socket.getaddrinfo = lambda host, *a, **kw: resolve('127.0.0.1' if host == domain else host, *a, **kw)
    print('Checking the origin directly (bypassing Cloudflare)')
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None
opener = urllib.request.build_opener(NoRedirect)
opener.addheaders = [('User-Agent', 'KlockDeploymentCheck/1.0')]
regular = urllib.request.build_opener()
regular.addheaders = [('User-Agent', 'KlockDeploymentCheck/1.0')]
urllib.request.install_opener(regular)
with urllib.request.urlopen(base+'/api/state', timeout=20) as response:
    state=json.load(response)
    assert state['auth']['google_enabled'] and state['auth']['google_required']
    assert response.headers.get('Cache-Control') == 'no-store'
print('Public HTTPS API: OK; Google authentication enabled')
with urllib.request.urlopen(base+'/', timeout=20) as response:
    assert 'Klock' in response.read().decode()
print('Public application: OK')
try: opener.open(base+'/api/auth/google/start', timeout=20)
except urllib.error.HTTPError as response:
    assert response.code==303
    location=response.headers['Location']
    url=urllib.parse.urlparse(location)
    query=urllib.parse.parse_qs(url.query)
    assert url.scheme=='https' and url.hostname=='accounts.google.com'
    assert query['redirect_uri']==[base+'/api/auth/google/callback']
    assert query['code_challenge_method']==['S256']
    assert 'state' in query and 'nonce' in query
    cookie=response.headers['Set-Cookie']
    assert all(flag in cookie for flag in ['HttpOnly','Secure','SameSite=Lax'])
    print('Google redirect, callback address, PKCE and secure cookie: OK')
else: raise AssertionError('Expected a Google redirect')
try: opener.open('http://' + domain + '/',timeout=20)
except urllib.error.HTTPError as response:
    assert response.code in (301,302,307,308)
    assert response.headers['Location'].startswith(base)
else: raise AssertionError('Expected HTTP to redirect to HTTPS')
print('HTTP to HTTPS redirect: OK')
# Visit Google's authorization endpoint without credentials; report only named errors.
try:
    with urllib.request.urlopen(location,timeout=20) as response:
        html=response.read().decode()
        final=response.url
    errors=[e for e in ('redirect_uri_mismatch','invalid_client','deleted_client') if e in html or e in final]
    if errors: raise AssertionError('Google configuration error: '+', '.join(errors))
    print('Google authorization endpoint reachable; interactive sign-in still needs the account owner')
except urllib.error.HTTPError as error:
    raise AssertionError('Google authorization endpoint HTTP status: '+str(error.code)) from None

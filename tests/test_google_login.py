import io
import json
import os
import unittest
from urllib.parse import urlparse, parse_qs
from unittest.mock import patch
import test_server
import server
import google_login as auth

class GoogleLoginTests(unittest.TestCase):
    request = test_server.KlockTests.request
    state = test_server.KlockTests.state
    setup_owner = test_server.KlockTests.setup_owner
    project = test_server.KlockTests.project

    def setUp(self):
        self.env=patch.dict(os.environ, {'GOOGLE_CLIENT_ID':'test-client', 'GOOGLE_CLIENT_SECRET':'test-secret',
                                        'KLOCK_BASE_URL':'http://localhost:5188'})
        self.env.start()
        test_server.KlockTests.setUp(self)
    def tearDown(self):
        test_server.KlockTests.tearDown(self)
        self.env.stop()
    def start(self, invite=''):
        result=self.request('/api/auth/google/start'+('?invite='+invite if invite else ''))
        self.assertEqual(result['status'],303)
        query=parse_qs(urlparse(result['headers']['Location']).query)
        self.assertEqual(query['code_challenge_method'],['S256'])
        self.assertEqual(query['scope'],['openid email profile'])
        self.assertNotIn('test-secret',result['headers']['Location'])
        return query['state'][0],result['headers']['Set-Cookie'].split(';')[0]
    def callback(self, state, cookie, subject='google-1', error=''):
        claims={'sub':subject,'email':'person@gmail.com','name':'Google Person','email_verified':True}
        with patch('google_login.exchange',return_value=claims):
            return self.request('/api/auth/google/callback?state='+state+'&code=code'+error,cookie=cookie)
    def session_cookie(self,result):
        self.assertEqual(result['headers']['Location'],'/')
        return result['headers']['Set-Cookie'][0].split(';')[0]
    def google_owner(self):
        state,cookie=self.start()
        return self.session_cookie(self.callback(state,cookie))
    def test_new_owner_and_returning_login_preserve_identity(self):
        cookie=self.google_owner();first=self.state(cookie)
        self.assertEqual(first['me']['role'],'owner')
        self.assertTrue(first['auth']['google_linked'])
        pid=self.project(cookie)
        self.request('/api/entries',{'project_id':pid,'date':'2026-09-29','seconds':60},cookie)
        second=self.state(self.google_owner())
        self.assertEqual(first['me']['id'],second['me']['id'])
        self.assertEqual(len(second['entries']),1)
        self.assertEqual(len(second['members']),1)
    def test_invite_required_and_used_once(self):
        owner=self.google_owner()
        state,cookie=self.start()
        rejected=self.callback(state,cookie,'unknown')
        self.assertIn('auth_error',rejected['headers']['Location'])
        self.request('/api/invites',{'name':'Lin'},owner)
        invite=self.state(owner)['invites'][0]['token']
        state,cookie=self.start(invite)
        member=self.state(self.session_cookie(self.callback(state,cookie,'lin')))
        self.assertEqual(member['me']['role'],'member')
        self.assertEqual(len(member['members']),2)
        self.assertIn('auth_error',self.request('/api/auth/google/start?invite='+invite)['headers']['Location'])
    def test_invalid_browser_expired_and_replayed_state(self):
        state,cookie=self.start()
        with patch('google_login.exchange') as exchange:
            self.request('/api/auth/google/callback?state='+state+'&code=code',cookie='klock_oauth=wrong')
            exchange.assert_not_called()
        self.assertEqual(self.callback(state,cookie)['headers']['Location'],'/')
        self.assertIn('auth_error',self.callback(state,cookie)['headers']['Location'])
        state,cookie=self.start()
        with server.connect() as db:db.execute('UPDATE oauth_flows SET expires=0')
        self.assertIn('auth_error',self.callback(state,cookie)['headers']['Location'])
    def test_cancellation_and_provider_errors(self):
        state,cookie=self.start()
        with patch('google_login.exchange') as exchange:
            result=self.request('/api/auth/google/callback?state='+state+'&error=access_denied',cookie=cookie)
            exchange.assert_not_called()
            self.assertIn('auth_error',result['headers']['Location'])
        state,cookie=self.start()
        with patch('google_login.exchange',side_effect=RuntimeError('secret-token')):
            result=self.request('/api/auth/google/callback?state='+state+'&code=code',cookie=cookie)
        self.assertNotIn('secret-token',str(result))
    def test_link_existing_identity_and_reject_replacement(self):
        with patch.dict(os.environ,{'GOOGLE_CLIENT_ID':'','GOOGLE_CLIENT_SECRET':''}):owner=self.setup_owner()
        mid=self.state(owner)['me']['id'];pid=self.project(owner)
        self.request('/api/entries',{'project_id':pid,'seconds':900,'date':'2026-09-29'},owner)
        result=self.request('/api/auth/google/link',{},owner)
        state=parse_qs(urlparse(result['body']['url']).query)['state'][0]
        cookie=result['headers']['Set-Cookie'].split(';')[0]
        linked=self.state(self.session_cookie(self.callback(state,cookie)))
        self.assertEqual(linked['me']['id'],mid)
        self.assertEqual(len(linked['entries']),1)
        self.assertEqual(len(self.state(self.google_owner())['members']),1)
        self.assertEqual(self.request('/api/auth/google/link',{},owner)['status'],400)
    def test_logout_revokes_session_and_link_flow(self):
        with patch.dict(os.environ,{'GOOGLE_CLIENT_ID':'','GOOGLE_CLIENT_SECRET':''}):owner=self.setup_owner()
        flow=self.request('/api/auth/google/link',{},owner)
        self.request('/api/auth/logout',{},owner)
        self.assertIsNone(self.state(owner)['me'])
        state=parse_qs(urlparse(flow['body']['url']).query)['state'][0]
        result=self.callback(state,flow['headers']['Set-Cookie'].split(';')[0])
        self.assertIn('auth_error',result['headers']['Location'])
    def test_legacy_enrollment_blocked_when_google_configured(self):
        self.assertEqual(self.request('/api/setup',{'name':'No'})['status'],400)
        self.assertEqual(self.request('/api/join',{'name':'No','token':'No'})['status'],400)
        self.assertEqual(self.request('/api/auth/google/link',{})['status'],401)
    def test_exchange_uses_pkce_and_rejects_nonce_or_unverified_email(self):
        flow={'verifier':'pkce-secret','nonce':'nonce'}
        claims={'sub':'subject','email':'user@gmail.com','email_verified':True,'nonce':'nonce'}
        with patch('google_login.urlopen',return_value=io.BytesIO(b'{"id_token":"signed-token"}')) as fetch:
            with patch('google_login.verify_id_token',return_value=claims):
                self.assertEqual(auth.exchange('code',flow)['sub'],'subject')
                self.assertIn(b'code_verifier=pkce-secret',fetch.call_args[0][0].data)
                self.assertEqual(fetch.call_args[0][0].full_url,'https://oauth2.googleapis.com/token')
        for changes in [{'nonce':'wrong'},{'email_verified':False},{'email_verified':'true'},{'sub':''}]:
            with patch('google_login.urlopen',return_value=io.BytesIO(b'{"id_token":"signed-token"}')):
                with patch('google_login.verify_id_token',return_value=dict(claims,**changes)):
                    with self.assertRaises(ValueError):auth.exchange('code',flow)
    def test_production_bootstrap_is_restricted_to_designated_admin(self):
        with patch.dict(os.environ, {'KLOCK_BASE_URL':'https://klock.example', 'KLOCK_BOOTSTRAP_EMAIL':''}):
            state,cookie=self.start()
            self.assertIn('auth_error',self.callback(state,cookie)['headers']['Location'])
        with patch.dict(os.environ, {'KLOCK_BASE_URL':'https://klock.example', 'KLOCK_BOOTSTRAP_EMAIL':'owner@gmail.com'}):
            state,cookie=self.start()
            self.assertIn('auth_error',self.callback(state,cookie)['headers']['Location'])
            self.assertFalse(self.state(None)['configured'])
        with patch.dict(os.environ, {'KLOCK_BASE_URL':'https://klock.example', 'KLOCK_BOOTSTRAP_EMAIL':'PERSON@gmail.com'}):
            cookie=self.google_owner()
            self.assertEqual(self.state(cookie)['me']['role'],'owner')

    def test_fixed_redirect_secure_cookie_and_public_config(self):
        with patch.dict(os.environ,{'KLOCK_BASE_URL':'https://klock.example'}):
            self.assertIn('; Secure',auth.cookie('session','x',60))
            self.assertEqual(auth.settings()['redirect_uri'],'https://klock.example/api/auth/google/callback')
        with patch.dict(os.environ,{'KLOCK_BASE_URL':'http://public.example'}):
            self.assertFalse(auth.enabled())
        self.assertNotIn('test-secret',json.dumps(self.state(None)))
        self.assertEqual(self.request('/api/auth/google/link',{},origin='https://evil.test')['status'],403)

if __name__=='__main__':unittest.main()

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server

class KlockTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.old_path=server.DB_PATH
        server.DB_PATH=Path(self.directory.name)/'test.db'
        server.initialize()
    def tearDown(self):
        server.DB_PATH=self.old_path
        self.directory.cleanup()
    def request(self,path,data=None,cookie=None,origin=None):
        h=object.__new__(server.Handler);h.path=path
        body=json.dumps(data).encode() if data is not None else b''
        h.headers={'Content-Length':str(len(body)),'Host':'localhost:5188'}
        if cookie:h.headers['Cookie']=cookie
        if origin:h.headers['Origin']=origin
        h.rfile=io.BytesIO(body)
        result={}
        def send(status,payload,mime='application/json; charset=utf-8',headers=None):result.update(status=status,body=payload,headers=headers or {})
        h.send=send
        h.do_GET() if data is None else h.do_POST()
        return result
    def setup_owner(self):
        r=self.request('/api/setup',{'name':'Owner'})
        self.assertEqual(r['status'],200)
        return r['headers']['Set-Cookie'].split(';')[0]
    def state(self,cookie):return self.request('/api/state',cookie=cookie)['body']
    def project(self,cookie,name='Design'):
        self.request('/api/projects',{'name':name,'client':'Studio'},cookie)
        return self.state(cookie)['projects'][-1]['id']
    def test_setup_only_once_and_anonymous_access(self):
        self.assertIsNone(self.state(None)['me'])
        self.assertFalse(self.state(None)['configured'])
        cookie=self.setup_owner()
        self.assertEqual(self.state(cookie)['me']['name'],'Owner')
        self.assertEqual(self.request('/api/setup',{'name':'Attacker'})['status'],400)
        self.assertEqual(self.request('/api/projects',{'name':'No'})['status'],401)
        self.assertEqual(self.request('/api/export?month=2026-09')['status'],401)
    def test_timer_persists_and_splits_at_midnight(self):
        cookie=self.setup_owner();pid=self.project(cookie)
        start=1788278390 # arbitrary fixed instant; compute exact local boundary below
        import calendar
        from datetime import datetime
        start=calendar.timegm(datetime(2026,9,29,15,59,50).timetuple())
        with patch('server.time.time',return_value=start):
            self.assertEqual(self.request('/api/timer/start',{'project_id':pid,'description':'work','timezone_offset':-480},cookie)['status'],200)
            self.assertEqual(self.request('/api/timer/start',{'project_id':pid},cookie)['status'],400)
        server.initialize()
        self.assertEqual(self.state(cookie)['timer']['started'],start)
        with patch('server.time.time',return_value=start+35):
            self.assertEqual(self.request('/api/timer/stop',{},cookie)['status'],200)
        es=self.state(cookie)['entries']
        self.assertEqual(sorted((e['date'],e['seconds']) for e in es),[('2026-09-29',10),('2026-09-30',25)])
        self.assertIsNone(self.state(cookie)['timer'])
    def test_invite_consumption_and_member_permissions(self):
        owner=self.setup_owner();pid=self.project(owner)
        self.request('/api/invites',{'name':'Lin'},owner)
        token=self.state(owner)['invites'][0]['token']
        response=self.request('/api/join',{'token':token,'name':'Lin'})
        self.assertEqual(response['status'],200)
        member=response['headers']['Set-Cookie'].split(';')[0]
        self.assertEqual(self.request('/api/join',{'token':token,'name':'Again'})['status'],400)
        self.assertEqual(self.request('/api/invites',{'name':'Bad'},member)['status'],400)
        self.assertEqual(self.request('/api/projects/archive',{'id':pid},member)['status'],400)
        self.request('/api/entries',{'project_id':pid,'date':'2026-09-29','seconds':3600},owner)
        eid=self.state(owner)['entries'][0]['id']
        self.assertEqual(self.request('/api/entries/delete',{'id':eid},member)['status'],400)
        self.assertEqual(self.request('/api/entries/delete',{'id':eid},owner)['status'],200)
        self.assertEqual(len(self.state(member)['members']),2)
    def test_expired_and_revoked_invites(self):
        cookie=self.setup_owner()
        with patch('server.time.time',return_value=100):self.request('/api/invites',{'name':'Old'},cookie)
        with server.connect() as db:token=db.execute('SELECT token FROM invites').fetchone()[0]
        self.assertEqual(self.request('/api/join',{'name':'Old','token':token})['status'],400)
        self.request('/api/invites',{'name':'New'},cookie)
        token=self.state(cookie)['invites'][0]['token']
        self.request('/api/invites/revoke',{'token':token},cookie)
        self.assertEqual(self.request('/api/join',{'name':'New','token':token})['status'],400)
    def test_export_precision_scope_and_formula_protection(self):
        cookie=self.setup_owner();pid=self.project(cookie,'=1+1')
        for date in ['2026-09-29','2026-09-29','2026-08-31']:
            self.request('/api/entries',{'project_id':pid,'date':date,'description':'@test, "quoted"\nline','seconds':18},cookie)
        result=self.request('/api/export?month=2026-09&format=json',cookie=cookie)['body']
        self.assertEqual(result['period_end'],'2026-09-30')
        self.assertEqual(len(result['entries']),2)
        self.assertEqual(result['entries'][0]['duration'],'00:00:18')
        self.assertEqual(result['entries'][0]['project'],'=1+1')
        csv=self.request('/api/export?month=2026-09',cookie=cookie)['body'].decode('utf-8-sig')
        self.assertIn("'=1+1",csv)
        self.assertIn("'@test",csv)
        self.assertIn('TOTAL,,,0.01,36,',csv)
        self.assertEqual(server.hours(18),'0.01')
        self.assertEqual(self.request('/api/export?month=2026-02&format=json',cookie=cookie)['body']['period_end'],'2026-02-28')
        self.assertEqual(self.request('/api/export?month=oops',cookie=cookie)['status'],400)
    def test_archive_and_validation(self):
        cookie=self.setup_owner();pid=self.project(cookie)
        for data in [{'seconds':0,'date':'2026-09-29'},{'seconds':86401,'date':'2026-09-29'},{'seconds':12,'date':'2026-02-30'}]:
            self.assertEqual(self.request('/api/entries',dict(project_id=pid,**data),cookie)['status'],400)
        self.request('/api/projects/archive',{'id':pid},cookie)
        self.assertEqual(self.request('/api/timer/start',{'project_id':pid},cookie)['status'],400)
        self.request('/api/projects/archive',{'id':pid},cookie)
        self.assertEqual(self.request('/api/timer/start',{'project_id':pid},cookie)['status'],200)
        self.assertEqual(self.request('/api/projects/archive',{'id':pid},cookie)['status'],400)
    def test_origin_and_sql_injection(self):
        cookie=self.setup_owner()
        self.assertEqual(self.request('/api/projects',{'name':'Bad'},cookie,origin='https://evil.test')['status'],403)
        self.project(cookie,"x'); DROP TABLE projects;--")
        self.assertEqual(len(self.state(cookie)['projects']),1)
    def test_split_time_across_multiple_days(self):
        from datetime import datetime
        import calendar
        start=calendar.timegm(datetime(2026,9,29).timetuple())
        parts=list(server.split_time(start,start+86400*2+60,0))
        self.assertEqual(parts,[('2026-09-29',86400),('2026-09-30',86400),('2026-10-01',60)])

if __name__=='__main__':unittest.main()

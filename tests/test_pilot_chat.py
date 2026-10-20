import tempfile
import unittest
from fastapi.testclient import TestClient
from app.main import app
from app.api.routers import pilot
from app.services.pilot import Pilot
from app.services.pilot_chat import PilotChat, MAX_AUDIO_BYTES
from test_pilot import P

class ChatTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.p=Pilot(self.tmp.name+'/chat.db');self.chat=PilotChat(self.p)
        self.a,self.b,self.stranger=[self.p.register()['token'] for _ in range(3)]
        plan={**P,'discovery':'browse','public':True}
        self.p.join(self.a,plan);self.p.join(self.b,plan)
        self.sid=self.p.invite(self.a,self.p.state(self.b)['ticket']['id'])['squad']['id']
    def tearDown(self):self.tmp.cleanup()
    def confirm(self):
        self.p.action(self.a,'confirm');self.p.action(self.b,'confirm')
    def test_chat_locked_until_both_confirm(self):
        with self.assertRaises(PermissionError):self.chat.messages(self.a,self.sid)
        self.p.action(self.a,'confirm')
        with self.assertRaises(PermissionError):self.chat.send(self.a,self.sid,'hello')
        self.p.action(self.b,'confirm')
        self.assertEqual(self.chat.messages(self.a,self.sid)['items'],[])
    def test_text_is_durable_private_and_incremental(self):
        self.confirm();m=self.chat.send(self.a,self.sid,'你好，游戏 ID 是 test')
        self.assertTrue(m['is_me'])
        other=PilotChat(Pilot(self.p.path)).messages(self.b,self.sid)['items']
        self.assertEqual(other[0]['content'],m['content']);self.assertFalse(other[0]['is_me'])
        self.assertEqual(self.chat.messages(self.b,self.sid,m['id'])['items'],[])
        with self.assertRaises(PermissionError):self.chat.messages(self.stranger,self.sid)
        with self.assertRaises(PermissionError):self.chat.send(self.stranger,self.sid,'intrude')
        self.assertNotIn(self.a,str(other));self.assertNotIn(self.b,str(other))
    def test_voice_private_download_and_validation(self):
        self.confirm();blob=b'\x1a\x45\xdf\xa3'+b'container-test'
        m=self.chat.send(self.a,self.sid,audio=blob,mime='audio/webm;codecs=opus',duration=3)
        self.assertEqual(self.chat.audio(self.b,self.sid,m['id']),(blob,'audio/webm'))
        with self.assertRaises(PermissionError):self.chat.audio(self.stranger,self.sid,m['id'])
        for audio,mime,duration in [(b'<html>','audio/webm',3),(blob,'text/html',3),(blob,'audio/webm',61),(blob,'audio/webm',0),(blob+b'x'*MAX_AUDIO_BYTES,'audio/webm',30)]:
            with self.assertRaises(ValueError):self.chat.send(self.a,self.sid,audio=audio,mime=mime,duration=duration)
        self.assertNotIn('audio',self.chat.messages(self.b,self.sid)['items'][0])
    def test_rate_limit_and_retention(self):
        self.confirm()
        for i in range(20):self.chat.send(self.a,self.sid,str(i))
        with self.assertRaises(ValueError):self.chat.send(self.a,self.sid,'too many')
        with self.p.db() as db:db.execute("UPDATE pilot_messages SET created='2000-01-01T00:00:00+00:00'")
        self.assertEqual(self.chat.messages(self.b,self.sid)['items'],[])
    def test_cancelled_room_not_accessible(self):
        self.confirm();self.chat.send(self.a,self.sid,'before cancel');self.p.action(self.a,'cancel')
        with self.assertRaises(PermissionError):self.chat.messages(self.b,self.sid)
    def test_http_upload_history_and_private_audio(self):
        self.confirm();previous=pilot.service;pilot.service=self.p
        try:
            c=TestClient(app);a={'Authorization':'Bearer '+self.a};b={'Authorization':'Bearer '+self.b};other={'Authorization':'Bearer '+self.stranger}
            url=f'/api/pilot/squads/{self.sid}'
            self.assertEqual(c.post(url+'/messages',headers=a,json={'content':'hello'}).status_code,200)
            self.assertEqual(c.post(url+'/messages',headers=a,json={'content':'  '}).status_code,400)
            blob=b'OggS'+b'audio-test'
            response=c.post(url+'/voice',headers=a,files={'file':('voice.ogg',blob,'audio/ogg')},data={'duration':'2'})
            self.assertEqual(response.status_code,200)
            mid=response.json()['id'];download=url+'/voice/'+str(mid)
            response=c.get(download,headers=b)
            self.assertEqual(response.content,blob);self.assertEqual(response.headers['cache-control'],'private, no-store')
            self.assertEqual(c.get(download,headers=other).status_code,403)
            self.assertEqual(c.get(download).status_code,401)
            self.assertEqual(c.get(url+'/messages',headers=b).json()['items'][1]['kind'],'voice')
        finally:pilot.service=previous

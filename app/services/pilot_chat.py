"""Private confirmed-squad messages. Audio stays in the same persistent SQLite database."""
from datetime import datetime, timezone, timedelta

MAX_AUDIO_BYTES = 1024 * 1024
RETENTION_DAYS = 7


class PilotChat:
    def __init__(self, pilot):
        self.pilot = pilot

    @staticmethod
    def _schema(db):
        db.execute("""CREATE TABLE IF NOT EXISTS pilot_messages(
            id INTEGER PRIMARY KEY, squad_id INTEGER NOT NULL, sender_ticket INTEGER NOT NULL,
            kind TEXT NOT NULL, content TEXT NOT NULL DEFAULT '', audio BLOB,
            mime TEXT, duration REAL, created TEXT NOT NULL)""")
        db.execute('CREATE INDEX IF NOT EXISTS pilot_message_room ON pilot_messages(squad_id,id)')

    def _authorize(self, db, token, squad_id):
        self.pilot.participant(db, token)
        squad = db.execute('SELECT * FROM squads WHERE id=?', (squad_id,)).fetchone()
        if not squad or squad['status'] not in ('ready', 'done'):
            raise PermissionError('双方确认组队后才能聊天')
        member = db.execute('SELECT * FROM tickets WHERE id IN (?,?) AND token=?',
                            (squad['a'], squad['b'], token)).fetchone()
        if not member:
            raise PermissionError('你不在这支队伍中')
        return member

    def _prepare(self, db, token, squad_id):
        member = self._authorize(db, token, squad_id)
        self._schema(db)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
        db.execute('DELETE FROM pilot_messages WHERE created<?', (cutoff,))
        return member

    @staticmethod
    def _out(row, mine):
        return {'id': row['id'], 'kind': row['kind'], 'content': row['content'],
                'duration': row['duration'], 'created': row['created'],
                'sender_id': row['sender_ticket'], 'is_me': row['sender_ticket'] == mine}

    def messages(self, token, squad_id, after=0):
        with self.pilot.db() as db:
            member = self._prepare(db, token, squad_id)
            rows = db.execute('SELECT id,kind,content,duration,created,sender_ticket FROM pilot_messages WHERE squad_id=? AND id>? ORDER BY id LIMIT 100',
                              (squad_id, after)).fetchall()
            return {'items': [self._out(r, member['id']) for r in rows], 'retention_days': RETENTION_DAYS}

    def send(self, token, squad_id, text='', audio=None, mime=None, duration=None):
        if audio is None:
            text = text.strip()
            if not text or len(text) > 2000:
                raise ValueError('消息需为 1–2000 字')
        else:
            mime = (mime or '').split(';')[0].lower().strip()
            signatures = {
                'audio/webm': audio.startswith(b'\x1a\x45\xdf\xa3'),
                'audio/ogg': audio.startswith(b'OggS'),
                'audio/wav': len(audio) >= 12 and audio[:4] == b'RIFF' and audio[8:12] == b'WAVE',
                'audio/mp4': len(audio) >= 12 and audio[4:8] == b'ftyp',
            }
            if not audio or len(audio) > MAX_AUDIO_BYTES:
                raise ValueError('语音文件不能超过 1 MB')
            if not signatures.get(mime, False):
                raise ValueError('不支持的语音格式')
            if duration is None or not 0 < duration <= 60:
                raise ValueError('语音最长 60 秒')
        with self.pilot.db() as db:
            member = self._prepare(db, token, squad_id)
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            recent = db.execute('SELECT COUNT(*) FROM pilot_messages WHERE sender_ticket=? AND created>?', (member['id'], cutoff)).fetchone()[0]
            if recent >= 20:
                raise ValueError('消息发送太频繁，请稍后再试')
            if audio is not None:
                count = db.execute("SELECT COUNT(*) FROM pilot_messages WHERE squad_id=? AND kind='voice'", (squad_id,)).fetchone()[0]
                if count >= 100:
                    raise ValueError('本队语音空间已满，请使用文字')
            cur = db.execute('INSERT INTO pilot_messages(squad_id,sender_ticket,kind,content,audio,mime,duration,created) VALUES(?,?,?,?,?,?,?,?)',
                             (squad_id, member['id'], 'voice' if audio is not None else 'text', text if audio is None else '', audio, mime, duration, self.pilot.now()))
            row = db.execute('SELECT * FROM pilot_messages WHERE id=?', (cur.lastrowid,)).fetchone()
            return self._out(row, member['id'])

    def audio(self, token, squad_id, message_id):
        with self.pilot.db() as db:
            self._prepare(db, token, squad_id)
            row = db.execute("SELECT audio,mime FROM pilot_messages WHERE id=? AND squad_id=? AND kind='voice'", (message_id, squad_id)).fetchone()
            if not row:
                raise LookupError('语音已过期或不存在')
            return bytes(row['audio']), row['mime']

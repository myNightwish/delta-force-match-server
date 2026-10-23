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
        # Persist the high-water mark before retention deletes any rows, including on old databases
        db.execute('CREATE TABLE IF NOT EXISTS pilot_message_sequence(singleton INTEGER PRIMARY KEY CHECK(singleton=1), last_id INTEGER NOT NULL)')
        db.execute('INSERT INTO pilot_message_sequence(singleton,last_id) SELECT 1,COALESCE(MAX(id),0) FROM pilot_messages WHERE 1 ON CONFLICT(singleton) DO UPDATE SET last_id=MAX(last_id,excluded.last_id)')

    def _authorize(self, db, token, squad_id):
        self.pilot.participant(db, token)
        squad = db.execute('SELECT * FROM squads WHERE id=?', (squad_id,)).fetchone()
        if not squad or squad['status'] not in ('ready', 'done'):
            raise PermissionError('ui.both_players_must_confirm_before_chatting')
        member = db.execute('SELECT * FROM tickets WHERE id IN (?,?) AND token=?',
                            (squad['a'], squad['b'], token)).fetchone()
        if not member:
            raise PermissionError('ui.you_are_not_a_member_of_this_team')
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
                raise ValueError('ui.messages_must_contain_1_2000_characters')
        else:
            mime = (mime or '').split(';')[0].lower().strip()
            signatures = {
                'audio/webm': audio.startswith(b'\x1a\x45\xdf\xa3'),
                'audio/ogg': audio.startswith(b'OggS'),
                'audio/wav': len(audio) >= 12 and audio[:4] == b'RIFF' and audio[8:12] == b'WAVE',
                'audio/mp4': len(audio) >= 12 and audio[4:8] == b'ftyp',
            }
            if not audio or len(audio) > MAX_AUDIO_BYTES:
                raise ValueError('ui.audio_files_cannot_exceed_1_mb')
            if not signatures.get(mime, False):
                raise ValueError('ui.unsupported_audio_format')
            if duration is None or not 0 < duration <= 60:
                raise ValueError('ui.audio_messages_cannot_exceed_60_seconds')
        with self.pilot.db() as db:
            member = self._prepare(db, token, squad_id)
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            recent = db.execute('SELECT COUNT(*) FROM pilot_messages WHERE sender_ticket=? AND created>?', (member['id'], cutoff)).fetchone()[0]
            if recent >= 20:
                raise ValueError('ui.messages_are_being_sent_too_quickly_try_again_later')
            if audio is not None:
                count = db.execute("SELECT COUNT(*) FROM pilot_messages WHERE squad_id=? AND kind='voice'", (squad_id,)).fetchone()[0]
                if count >= 100:
                    raise ValueError('ui.this_team_s_audio_storage_is_full_please_use')
            next_id = db.execute('SELECT last_id+1 FROM pilot_message_sequence WHERE singleton=1').fetchone()[0]
            db.execute('UPDATE pilot_message_sequence SET last_id=? WHERE singleton=1', (next_id,))
            cur = db.execute('INSERT INTO pilot_messages(id,squad_id,sender_ticket,kind,content,audio,mime,duration,created) VALUES(?,?,?,?,?,?,?,?,?)',
                             (next_id, squad_id, member['id'], 'voice' if audio is not None else 'text', text if audio is None else '', audio, mime, duration, self.pilot.now()))
            row = db.execute('SELECT * FROM pilot_messages WHERE id=?', (cur.lastrowid,)).fetchone()
            return self._out(row, member['id'])

    def audio(self, token, squad_id, message_id):
        with self.pilot.db() as db:
            self._prepare(db, token, squad_id)
            row = db.execute("SELECT audio,mime FROM pilot_messages WHERE id=? AND squad_id=? AND kind='voice'", (message_id, squad_id)).fetchone()
            if not row:
                raise LookupError('ui.audio_message_expired_or_was_not_found')
            return bytes(row['audio']), row['mime']

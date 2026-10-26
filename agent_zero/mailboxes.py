"""Persistent, bounded agent IPC. Sending never changes authority or wakes inference."""
import time
import json
from .processes import TERMINAL

MAIL_TOOLS = {'sys_mail_send', 'sys_mail_recv', 'sys_mail_ack'}


class Mailboxes:
    MAX_PENDING = 64
    MAX_TOTAL = 4096

    def __init__(self, runtime):
        self.runtime = runtime
        self.db = runtime.store.db
        self.db.execute('''CREATE TABLE IF NOT EXISTS agent_mail(
            id INTEGER PRIMARY KEY AUTOINCREMENT, sender INTEGER NOT NULL,
            recipient INTEGER NOT NULL, body TEXT NOT NULL, created REAL NOT NULL)''')
        self.db.commit()
        for p in runtime.processes.records.values():
            if p['state'] == 'reaped':
                self.reap(p['pid'])

    def send(self, sender, recipient, body):
        target = self.runtime.processes.get(recipient)
        if target['state'] in TERMINAL:
            raise ValueError('ESRCH: recipient has terminated')
        if sender:
            source = self.runtime.processes.get(sender)
            if source['state'] in TERMINAL:
                raise PermissionError('EPERM: sender has terminated')
            if not (self.runtime.processes.visible(sender, recipient) or self.runtime.processes.visible(recipient, sender)):
                raise PermissionError('EPERM: mail is restricted to ancestors and descendants')
        if not isinstance(body, str) or not body or len(body.encode('utf8')) > 2048 or len(json.dumps(body, ensure_ascii=False).encode()) > 2800:
            raise ValueError('Mail requires 1–2048 UTF-8 bytes')
        if self.count(recipient) >= self.MAX_PENDING or self.db.execute('SELECT count(*) FROM agent_mail').fetchone()[0] >= self.MAX_TOTAL:
            raise ValueError('EAGAIN: mailbox full; acknowledge messages first')
        with self.db:
            row = self.db.execute('INSERT INTO agent_mail(sender,recipient,body,created) VALUES(?,?,?,?)', (sender, recipient, body, time.time()))
        return {'message_id': row.lastrowid, 'recipient': recipient}

    def count(self, pid):
        return self.db.execute('SELECT count(*) FROM agent_mail WHERE recipient=?', (pid,)).fetchone()[0]

    def receive(self, pid, limit=10):
        self.runtime.processes.get(pid)
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError('Mail receive limit is 1–20')
        return [{'id': i, 'sender': s, 'recipient': pid, 'body': b, 'created': t}
                for i, s, b, t in self.db.execute('SELECT id,sender,body,created FROM agent_mail WHERE recipient=? ORDER BY id LIMIT ?', (pid, limit))]

    def syscall(self, method, params, actor):
        pid = params.get('pid', 1) if actor == 0 else actor
        self.runtime.processes.get(pid)
        if method == 'sys_mail_send':
            return self.send(actor, params.get('recipient'), params.get('body'))
        if method == 'sys_mail_recv':
            return {'messages': self.receive(pid, params.get('limit', 10)), 'pending': self.count(pid)}
        if method == 'sys_mail_ack':
            identity = params.get('message_id')
            if type(identity) is not int or identity < 1:
                raise ValueError('Invalid message ID')
            with self.db:
                deleted = self.db.execute('DELETE FROM agent_mail WHERE id=? AND recipient=?', (identity, pid)).rowcount
            if not deleted:
                raise PermissionError('Message does not belong to this mailbox')
            return {'acknowledged': identity}
        raise ValueError('Unknown mailbox syscall')

    def reap(self, pid):
        with self.db:
            self.db.execute('DELETE FROM agent_mail WHERE recipient=?', (pid,))

    def flush(self, session, scope):
        with self.db:
            if scope == 'all':
                self.db.execute('DELETE FROM agent_mail')
            else:
                for process in self.runtime.processes.records.values():
                    if process['session'] == session:
                        self.db.execute('DELETE FROM agent_mail WHERE sender=? OR recipient=?', (process['pid'], process['pid']))

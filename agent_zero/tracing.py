"""Bounded metadata-only execution spans; no prompts, payloads or credentials."""
from contextlib import contextmanager
from contextvars import ContextVar
import time

CURRENT_SPAN = ContextVar('trace_span', default=None)


class ExecutionTrace:
    LIMIT = 5000

    def __init__(self, store):
        self.db = store.db
        self.db.execute('''CREATE TABLE IF NOT EXISTS execution_traces(
            id INTEGER PRIMARY KEY AUTOINCREMENT, parent INTEGER, pid INTEGER, kind TEXT,
            operation TEXT, status TEXT, started REAL, finished REAL, duration_ms REAL, error_type TEXT)''')
        self.db.execute("UPDATE execution_traces SET status='interrupted',finished=? WHERE status='running'", (time.time(),))
        self.db.commit()

    @contextmanager
    def span(self, pid, kind, operation):
        started = time.monotonic()
        row = self.db.execute('INSERT INTO execution_traces(parent,pid,kind,operation,status,started) VALUES(?,?,?,?,?,?)',
                              (CURRENT_SPAN.get(), pid, kind, operation, 'running', time.time()))
        self.db.commit()
        identity = row.lastrowid
        token = CURRENT_SPAN.set(identity)
        result = {'status': 'ok', 'error_type': None}
        try:
            yield result
        except BaseException as error:
            result.update(status='cancelled' if type(error).__name__ == 'CancelledError' else 'denied' if isinstance(error, PermissionError) else 'failed', error_type=type(error).__name__)
            raise
        finally:
            CURRENT_SPAN.reset(token)
            self.db.execute('UPDATE execution_traces SET status=?,finished=?,duration_ms=?,error_type=? WHERE id=?',
                            (result['status'], time.time(), round((time.monotonic()-started)*1000, 2), result['error_type'], identity))
            self.db.execute("DELETE FROM execution_traces WHERE status!='running' AND id NOT IN (SELECT id FROM execution_traces ORDER BY id DESC LIMIT ?)", (self.LIMIT,))
            self.db.commit()

    def event(self, pid, operation, kind='process'):
        with self.span(pid, kind, operation):
            pass

    def list(self, pid=None, after=0, limit=50):
        if pid is not None and (type(pid) is not int or pid < 1):
            raise ValueError('Invalid trace PID')
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Invalid trace cursor/limit')
        cursor = self.db.execute('''SELECT * FROM execution_traces WHERE id>? AND (? IS NULL OR pid=?) ORDER BY id DESC LIMIT ?''', (after, pid, pid, limit))
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row)) for row in cursor]

    def clear(self):
        with self.db:
            count = self.db.execute("DELETE FROM execution_traces WHERE status!='running'").rowcount
        return {'cleared': count}

"""Durable one-shot/interval jobs, coalesced dispatch and crash-safe claims."""
import json
import math
import time
from .memory import VirtualMemory
from .processes import TERMINAL
from .services import capabilities, launch_worker

SCHEDULE_ADMIN = {'sys_job_create', 'sys_job_list', 'sys_job_runs', 'sys_job_enable', 'sys_job_remove', 'sys_job_run'}


class Scheduler:
    def __init__(self, runtime):
        self.runtime = runtime
        self.db = runtime.store.db
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS scheduled_jobs(name TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS scheduled_runs(id INTEGER PRIMARY KEY AUTOINCREMENT,
                job TEXT NOT NULL, due REAL NOT NULL, claimed REAL NOT NULL, pid INTEGER,
                status TEXT NOT NULL, result TEXT NOT NULL DEFAULT '');
        ''')
        # A committed claim without an associated PID is deliberately not replayed.
        self.db.execute("UPDATE scheduled_runs SET status='interrupted' WHERE status='claimed'")
        self.db.commit()
        self.reconcile()

    def save(self, spec):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO scheduled_jobs VALUES(?,?)', (spec['name'], json.dumps(spec)))

    def get(self, name):
        VirtualMemory.name(name)
        row = self.db.execute('SELECT data FROM scheduled_jobs WHERE name=?', (name,)).fetchone()
        if not row:
            raise ValueError('Scheduled job not found')
        return json.loads(row[0])

    def list(self):
        return [json.loads(data) for data, in self.db.execute('SELECT data FROM scheduled_jobs ORDER BY name')]

    def runs(self, name=None):
        if name is not None:
            VirtualMemory.name(name)
        return [dict(zip(('id', 'job', 'due', 'claimed', 'pid', 'status', 'result'), row))
                for row in self.db.execute('SELECT * FROM scheduled_runs WHERE (? IS NULL OR job=?) ORDER BY id DESC LIMIT 30', (name, name))]

    def reconcile(self):
        for identity, pid, job in self.db.execute("SELECT id,pid,job FROM scheduled_runs WHERE status='running'").fetchall():
            process = self.runtime.processes.records.get(pid)
            if not process or process['state'] in TERMINAL or process['state'] == 'waiting_review':
                state = process['state'] if process else 'interrupted'
                result = (process.get('result') or process.get('error') or '')[:2000] if process else ''
                with self.db:
                    self.db.execute('UPDATE scheduled_runs SET status=?,result=? WHERE id=?', (state, result, identity))
                # Status is committed first: a crash never replays notifications.
                notice = f'Scheduled job {job}, PID {pid}: {state}. ' + result.encode()[:1300].decode('utf8', 'ignore')
                try:
                    self.runtime.mailboxes.send(0, 1, notice)
                except ValueError:
                    pass  # The durable run result remains available if mail is full.
        with self.db:
            self.db.execute("DELETE FROM scheduled_runs WHERE status NOT IN ('claimed','running') AND id NOT IN (SELECT id FROM scheduled_runs ORDER BY id DESC LIMIT 256)")

    def dispatch(self, spec, now):
        last = self.runtime.processes.records.get(spec.get('last_pid'))
        if last and last['state'] not in TERMINAL:
            return {'deferred': 'previous worker still active'}
        # Persist the claim and advance the due time BEFORE creating any work.
        # A crash in the gap loses this occurrence, rather than duplicating it.
        due = spec['next_run']
        interval = spec['interval']
        with self.db:
            row = self.db.execute("INSERT INTO scheduled_runs(job,due,claimed,status) VALUES(?,?,?,'claimed')", (spec['name'], due, now))
            spec.update(next_run=now + interval if interval else due, enabled=bool(interval), retry_at=0)
            self.db.execute('UPDATE scheduled_jobs SET data=? WHERE name=?', (json.dumps(spec), spec['name']))
        identity = row.lastrowid
        try:
            if spec['kind'] == 'notice':
                delivered = self.runtime.mailboxes.send(0, 1, spec['task'])
                with self.db:
                    self.db.execute("UPDATE scheduled_runs SET status='delivered',result=? WHERE id=?", (f"mail:{delivered['message_id']}", identity))
                self.runtime.trace.event(1, 'schedule.notice', 'scheduler')
                return {'run_id': identity, 'status': 'delivered'}
            if not (self.runtime.config.cloud_enabled or self.runtime.config.local_enabled):
                raise PermissionError('No conversation engine enabled')
            process = launch_worker(self.runtime, spec['task'], f'job-{spec["name"]}'[:60], spec['capabilities'])
            spec['last_pid'] = process['pid']
            self.save(spec)
            with self.db:
                self.db.execute("UPDATE scheduled_runs SET status='running',pid=? WHERE id=?", (process['pid'], identity))
            self.runtime.trace.event(process['pid'], 'schedule.dispatched', 'scheduler')
            return {'run_id': identity, 'pid': process['pid'], 'status': 'running'}
        except (ValueError, PermissionError) as error:
            # Definite local admission failure: no worker/provider call was made.
            spec.update(next_run=due, enabled=True, retry_at=now+30)
            self.save(spec)
            with self.db:
                self.db.execute("UPDATE scheduled_runs SET status='deferred',result=? WHERE id=?", (type(error).__name__, identity))
            return {'run_id': identity, 'status': 'deferred'}

    def tick(self, now=None):
        self.reconcile()
        if self.runtime.audio.mode != 'userspace':
            return
        now = time.time() if now is None else now
        for spec in self.list():
            if spec['enabled'] and spec['next_run'] <= now and spec.get('retry_at', 0) <= now:
                self.dispatch(spec, now)

    def syscall(self, method, params):
        if method == 'sys_job_list':
            return {'jobs': self.list()}
        if method == 'sys_job_runs':
            self.reconcile()
            return {'runs': self.runs(params.get('name'))}
        name = VirtualMemory.name(params.get('name'))
        if method == 'sys_job_create':
            when, interval, task, kind = params.get('run_at'), params.get('interval', 0), params.get('task'), params.get('kind', 'agent')
            if type(when) not in (int, float) or not math.isfinite(when) or when < 0:
                raise ValueError('run_at must be a finite Unix timestamp')
            if type(interval) not in (int, float) or not math.isfinite(interval) or (interval != 0 and not 60 <= interval <= 31536000):
                raise ValueError('Interval must be zero or 60–31536000 seconds')
            if kind not in ('agent', 'notice') or not isinstance(task, str) or not 1 <= len(task) <= 6000 or (kind == 'notice' and (len(task.encode()) > 2048 or len(json.dumps(task, ensure_ascii=False).encode()) > 2800)):
                raise ValueError('Invalid job kind or task size')
            if self.db.execute('SELECT 1 FROM scheduled_jobs WHERE name=?', (name,)).fetchone():
                raise ValueError('Job already exists')
            if len(self.list()) >= 32:
                raise ValueError('Scheduler full')
            spec = dict(name=name, task=task, kind=kind, next_run=when, interval=interval, enabled=True,
                        capabilities=capabilities(self.runtime, params.get('capabilities')), last_pid=None, retry_at=0)
            self.save(spec)
            self.runtime.store.audit('schedule.created', name)
            return spec
        spec = self.get(name)
        if method == 'sys_job_enable':
            if type(params.get('enabled')) is not bool:
                raise ValueError('enabled must be boolean')
            spec['enabled'] = params['enabled']
            spec['retry_at'] = 0
            self.save(spec)
            self.runtime.store.audit('schedule.enabled', f'{name}={spec["enabled"]}')
            return spec
        if method == 'sys_job_remove':
            with self.db:
                self.db.execute('DELETE FROM scheduled_jobs WHERE name=?', (name,))
            self.runtime.store.audit('schedule.removed', name)
            return {'removed': name, 'running_worker_cancelled': False}
        if method == 'sys_job_run':
            return self.dispatch(spec, time.time())
        raise ValueError('Unknown scheduler syscall')

    def flush(self, session, scope):
        with self.db:
            if scope == 'all':
                self.db.execute("UPDATE scheduled_runs SET result=''")
            else:
                for process in self.runtime.processes.records.values():
                    if process['session'] == session:
                        self.db.execute("UPDATE scheduled_runs SET result='' WHERE pid=?", (process['pid'],))

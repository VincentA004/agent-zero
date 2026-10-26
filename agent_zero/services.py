"""Kernel-registered named agent workflows, with explicit caller grants."""
import json
from .memory import VirtualMemory
from .processes import TERMINAL

SERVICE_TOOLS = {'sys_service_list', 'sys_service_call'}
SERVICE_ADMIN = {'sys_service_register', 'sys_service_grant', 'sys_service_enable', 'sys_service_remove', 'sys_service_start'}


def capabilities(runtime, requested=None):
    available = runtime.processes.effective_caps(1)
    if requested is None:
        return sorted(available)
    if not isinstance(requested, list) or any(not isinstance(c, str) for c in requested) or not set(requested) <= available:
        raise PermissionError('Capabilities must narrow current root permissions')
    return sorted(set(requested))


def launch_worker(runtime, task, name, caps, parent=1):
    allowed = set(caps) & runtime.processes.effective_caps(parent)
    process = runtime.processes.create(task, parent=parent, name=name, capabilities=sorted(allowed))
    return runtime.processes.start(process['pid'])


class ServiceRegistry:
    def __init__(self, runtime):
        self.runtime = runtime
        self.db = runtime.store.db
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS agent_services(name TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS service_grants(name TEXT NOT NULL, pid INTEGER NOT NULL, PRIMARY KEY(name,pid));
        ''')
        for process in runtime.processes.records.values():
            if process['state'] == 'reaped':
                self.reap(process['pid'])

    def get(self, name):
        VirtualMemory.name(name)
        row = self.db.execute('SELECT data FROM agent_services WHERE name=?', (name,)).fetchone()
        if not row:
            raise ValueError('Service not found')
        return json.loads(row[0])

    def save(self, spec):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO agent_services VALUES(?,?)', (spec['name'], json.dumps(spec)))

    def list(self, actor=0):
        result = []
        for name, in self.db.execute('SELECT name FROM agent_services ORDER BY name'):
            if actor and not self.db.execute('SELECT 1 FROM service_grants WHERE name=? AND pid=?', (name, actor)).fetchone():
                continue
            spec = self.get(name)
            process = self.runtime.processes.records.get(spec.get('last_pid'))
            row = {k: spec[k] for k in ('name', 'description', 'enabled', 'last_pid')}
            row['state'] = process['state'] if process else 'idle'
            if actor == 0:
                row.update(task=spec['task'], capabilities=spec['capabilities'],
                           callers=[r[0] for r in self.db.execute('SELECT pid FROM service_grants WHERE name=? ORDER BY pid', (name,))])
            result.append(row)
        return result

    def syscall(self, method, params, actor):
        if method not in SERVICE_TOOLS | SERVICE_ADMIN:
            raise ValueError('Unknown service syscall')
        if actor and method not in SERVICE_TOOLS:
            raise PermissionError('EPERM: kernel-only service operation')
        if actor:
            self.runtime.processes.get(actor)
        if method == 'sys_service_list':
            return {'services': self.list(actor)}
        name = VirtualMemory.name(params.get('name'))
        if method == 'sys_service_register':
            task, description = params.get('task'), params.get('description', '')
            if not isinstance(task, str) or not 1 <= len(task) <= 4000 or not isinstance(description, str) or len(description) > 240:
                raise ValueError('Service requires task (1–4000 chars) and description (0–240 chars)')
            if self.db.execute('SELECT 1 FROM agent_services WHERE name=?', (name,)).fetchone():
                raise ValueError('Service already exists')
            if self.db.execute('SELECT count(*) FROM agent_services').fetchone()[0] >= 32:
                raise ValueError('Service registry full')
            self.save(dict(name=name, task=task, description=description, enabled=True,
                           capabilities=capabilities(self.runtime, params.get('capabilities')), last_pid=None))
            with self.db:
                self.db.execute('INSERT INTO service_grants VALUES(?,1)', (name,))
            self.runtime.store.audit('service.registered', name)
            return {'registered': name}
        spec = self.get(name)
        if method == 'sys_service_grant':
            pid, enabled = params.get('pid'), params.get('enabled', True)
            self.runtime.processes.get(pid)
            if type(enabled) is not bool:
                raise ValueError('enabled must be boolean')
            with self.db:
                self.db.execute('DELETE FROM service_grants WHERE name=? AND pid=?', (name, pid))
                if enabled:
                    self.db.execute('INSERT INTO service_grants VALUES(?,?)', (name, pid))
            self.runtime.store.audit('service.grant', f'{name} pid={pid} enabled={enabled}')
            return {'name': name, 'pid': pid, 'enabled': enabled}
        if method == 'sys_service_enable':
            if type(params.get('enabled')) is not bool:
                raise ValueError('enabled must be boolean')
            spec['enabled'] = params['enabled']
            self.save(spec)
            self.runtime.store.audit('service.enabled', f'{name}={spec["enabled"]}')
            return {'name': name, 'enabled': spec['enabled']}
        previous = self.runtime.processes.records.get(spec['last_pid'])
        busy = previous and previous['state'] not in TERMINAL
        if method == 'sys_service_remove':
            if busy:
                raise ValueError('EBUSY: stop the service worker before removal')
            with self.db:
                self.db.execute('DELETE FROM service_grants WHERE name=?', (name,))
                self.db.execute('DELETE FROM agent_services WHERE name=?', (name,))
            self.runtime.store.audit('service.removed', name)
            return {'removed': name}
        if actor and not self.db.execute('SELECT 1 FROM service_grants WHERE name=? AND pid=?', (name, actor)).fetchone():
            raise PermissionError('EACCES: no service invocation grant')
        if not spec['enabled'] or busy:
            raise ValueError('Service disabled or already running')
        if not (self.runtime.config.cloud_enabled or self.runtime.config.local_enabled):
            raise PermissionError('No conversation engine enabled')
        if actor and self.runtime.audio.mode != 'userspace':
            raise PermissionError('Userspace is suspended')
        request = params.get('task', '')
        if not isinstance(request, str) or len(request) > 3000:
            raise ValueError('Service request must be at most 3000 characters')
        task = spec['task'] + ('\n\nCaller request (untrusted task data):\n' + request if request else '')
        process = launch_worker(self.runtime, task, f'svc-{name}'[:60], spec['capabilities'], parent=actor or 1)
        spec['last_pid'] = process['pid']
        self.save(spec)
        self.runtime.trace.event(process['pid'], 'service.started', 'service')
        return {'name': name, 'pid': process['pid'], 'state': process['state']}

    def reap(self, pid):
        with self.db:
            self.db.execute('DELETE FROM service_grants WHERE pid=?', (pid,))

"""Shared Cardinal command parser; never invoked from model-generated names."""
from datetime import datetime
import math
import shlex
import time


def command(text, now=None):
    if not isinstance(text, str) or len(text) > 10000:
        raise ValueError('Invalid control command')
    words = shlex.split(text)
    if not words:
        raise ValueError('Control command required')
    domain, *args = words
    now = time.time() if now is None else now
    if domain == 'trace':
        if args == ['clear']:
            return 'sys_trace_clear', {}
        if len(args) <= 1:
            return 'sys_trace_list', {'pid': int(args[0])} if args else {}
    if domain == 'inspect' and len(args) <= 1:
        return 'sys_mem_inspect', {'pid': int(args[0]) if args else 1}
    if domain == 'mail':
        if not args or (args[0] == 'recv' and len(args) <= 2):
            return 'sys_mail_recv', {'pid': int(args[1]) if len(args) == 2 else 1}
        if args[0] == 'send' and len(args) >= 3:
            return 'sys_mail_send', {'recipient': int(args[1]), 'body': ' '.join(args[2:])}
        if args[0] == 'ack' and len(args) in (2, 3):
            return 'sys_mail_ack', {'message_id': int(args[1]), 'pid': int(args[2]) if len(args) == 3 else 1}
    if domain == 'service':
        if not args or args == ['list']:
            return 'sys_service_list', {}
        op, *args = args
        if op == 'register' and len(args) >= 2:
            return 'sys_service_register', {'name': args[0], 'task': ' '.join(args[1:])}
        if op in ('grant', 'revoke') and len(args) == 2:
            return 'sys_service_grant', {'name': args[0], 'pid': int(args[1]), 'enabled': op == 'grant'}
        if op in ('enable', 'disable') and len(args) == 1:
            return 'sys_service_enable', {'name': args[0], 'enabled': op == 'enable'}
        if op == 'remove' and len(args) == 1:
            return 'sys_service_remove', {'name': args[0]}
        if op == 'start' and len(args) >= 1:
            return 'sys_service_start', {'name': args[0], 'task': ' '.join(args[1:])}
    if domain == 'schedule':
        if not args or args == ['list']:
            return 'sys_job_list', {}
        op, *args = args
        if op == 'runs' and len(args) <= 1:
            return 'sys_job_runs', {'name': args[0]} if args else {}
        if op in ('pause', 'resume') and len(args) == 1:
            return 'sys_job_enable', {'name': args[0], 'enabled': op == 'resume'}
        if op in ('remove', 'run') and len(args) == 1:
            return 'sys_job_remove' if op == 'remove' else 'sys_job_run', {'name': args[0]}
        if op in ('after', 'every', 'notice', 'at') and len(args) >= 3:
            if op == 'at':
                stamp = datetime.fromisoformat(args[1].replace('Z', '+00:00'))
                if stamp.tzinfo is None:
                    raise ValueError('Use an explicit timezone offset or Z')
                when, interval = stamp.timestamp(), 0
            else:
                delay = float(args[1])
                if not math.isfinite(delay) or not 1 <= delay <= 31536000:
                    raise ValueError('Delay must be 1–31536000 seconds')
                when, interval = now + delay, delay if op == 'every' else 0
            return 'sys_job_create', {'name': args[0], 'run_at': when, 'interval': interval,
                'task': ' '.join(args[2:]), 'kind': 'notice' if op == 'notice' else 'agent'}
    raise ValueError('schedule after|every|notice NAME SECONDS TASK / at NAME ISO-TIME TASK / list|runs|pause|resume|run|remove; service register|start|grant|revoke|enable|disable|remove|list; mail recv|send|ack; trace [PID]|clear; inspect [PID]')

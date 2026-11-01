#!/usr/bin/env python3
"""Deterministic, synthetic comparison of the context policy BEFORE vs AFTER.

Pure arithmetic over a fixture: no inference, no network, no clock, no randomness and
no files written. The numbers only show how the *model* below behaves; they are NOT
evidence of real savings. See docs/RENDIMIENTO.md for the real A/B protocol.

Model (one task = ordered subtasks; every turn re-sends the whole context):
  input of a turn = prefix + history; it is a cache read when the same prefix+history was
  sent by the previous turn of that session. The first turn of a session reads the prefix
  from cache only if the prefix is stable (shared across instances) and was already used
  in this task or the cache is warm. A legacy prefix starts with the instance id, so a new
  session never hits it. After a turn: history += turn_tokens + tokens loaded by its read.
"""

import argparse
import json
import math
from pathlib import Path
import sys

POLICY_FLAGS = ('fresh_worker_per_subtask', 'bounded_reads', 'task_plan_with_pointers', 'stable_prompt_prefix')
NUMBERS = ('tokens_per_line', 'prefix_tokens', 'chief_prefix_tokens', 'turn_tokens', 'brief_tokens',
           'chief_turn_tokens', 'report_tokens')
METRICS = ('total_input', 'cached_read', 'new_tokens', 'rereads')

DEFAULT_FIXTURE = {
    'name': 'refactor-6-subtareas',
    'tokens_per_line': 12, 'prefix_tokens': 6000, 'chief_prefix_tokens': 3000, 'turn_tokens': 400,
    'brief_tokens': 500, 'chief_turn_tokens': 300, 'report_tokens': 800,
    'files': {'backend/core.py': 900, 'backend/api.py': 600, 'tests/test_core.py': 700, 'README.md': 400, 'backend/db.py': 500},
    'subtasks': [
        {'id': 'explorar', 'turns': 5, 'reads': [{'file': 'backend/core.py', 'needed_lines': 80}, {'file': 'backend/api.py', 'needed_lines': 60},
                                                  {'file': 'backend/db.py', 'needed_lines': 40}]},
        {'id': 'cambiar-core', 'turns': 6, 'reads': [{'file': 'backend/core.py', 'needed_lines': 80}, {'file': 'tests/test_core.py', 'needed_lines': 90}]},
        {'id': 'cambiar-api', 'turns': 6, 'reads': [{'file': 'backend/api.py', 'needed_lines': 60}, {'file': 'backend/core.py', 'needed_lines': 30, 'changed': True}]},
        {'id': 'probar', 'turns': 5, 'reads': [{'file': 'tests/test_core.py', 'needed_lines': 90}, {'file': 'backend/api.py', 'needed_lines': 40}]},
        {'id': 'documentar', 'turns': 4, 'reads': [{'file': 'README.md', 'needed_lines': 50}, {'file': 'backend/core.py', 'needed_lines': 30}]},
        {'id': 'revisar', 'turns': 5, 'reads': [{'file': 'backend/core.py', 'needed_lines': 80}, {'file': 'backend/api.py', 'needed_lines': 60},
                                                {'file': 'tests/test_core.py', 'needed_lines': 90}]},
    ],
}


class _Task:
    """Accumulates the totals of every session of one simulated task."""

    def __init__(self, fixture, policy, cache, window):
        self.fixture, self.policy, self.cache, self.window = fixture, policy, cache, window
        self.stable = policy['stable_prompt_prefix']
        self.primed = set()
        self.seen = set()
        self.totals = dict.fromkeys(METRICS, 0)
        self.totals['read_tokens'] = 0

    def session(self, role, prefix, history=0):
        """Open a session: its first turn reads the prefix from cache only when allowed."""
        shared = self.stable and (role in self.primed or self.cache == 'warm')
        self.primed.add(role)
        return {'prefix': prefix, 'history': history, 'previous': prefix if shared else 0, 'first': True}

    def turn(self, state, loaded=0, added=0):
        """One model turn; returns its input tokens."""
        sent = state['prefix'] + state['history']
        cached = min(state['previous'], sent)
        self.totals['total_input'] += sent
        self.totals['cached_read'] += cached
        self.totals['new_tokens'] += sent - cached
        state['history'] += added + loaded
        state['previous'] = sent
        return sent

    def read(self, spec):
        """Tokens loaded by one read, honouring the pointer list and the bounded window."""
        per_line, lines = self.fixture['tokens_per_line'], self.fixture['files'][spec['file']]
        again = spec['file'] in self.seen
        if again and self.policy['task_plan_with_pointers'] and not spec.get('changed', False):
            return 0
        self.totals['rereads'] += again
        self.seen.add(spec['file'])
        if self.policy['bounded_reads']:
            lines = min(lines, math.ceil(spec['needed_lines'] / self.window) * self.window)
        tokens = lines * per_line
        self.totals['read_tokens'] += tokens
        return tokens

    def subtask(self, state, spec):
        total = 0
        for turn in range(max(spec['turns'], len(spec['reads']))):
            loaded = self.read(spec['reads'][turn]) if turn < len(spec['reads']) else 0
            total += self.turn(state, loaded, self.fixture['turn_tokens'])
        return total


def simulate(fixture, policy, cache='cold', window=120):
    """Totals for one task under ``policy`` (all four flags) and a cold or warm cache."""
    task = _Task(fixture, policy, cache, window)
    by_subtask = {}
    if policy['fresh_worker_per_subtask']:
        chief = task.session('chief', fixture['chief_prefix_tokens'])
        for spec in fixture['subtasks']:
            task.turn(chief)  # the chief writes the brief and later receives the report
            chief['history'] += fixture['chief_turn_tokens'] + fixture['report_tokens']
            worker = task.session('worker', fixture['prefix_tokens'], fixture['brief_tokens'])
            by_subtask[spec['id']] = task.subtask(worker, spec)
    else:
        session = task.session('single', fixture['prefix_tokens'])
        for spec in fixture['subtasks']:
            by_subtask[spec['id']] = task.subtask(session, spec)
    return {**task.totals, 'by_subtask': by_subtask}


def compare(fixture, cache='cold', window=120):
    off = dict.fromkeys(POLICY_FLAGS, False)
    on = dict.fromkeys(POLICY_FLAGS, True)
    return {'before': simulate(fixture, off, cache, window), 'after': simulate(fixture, on, cache, window),
            'ablation': {flag: simulate(fixture, {**off, flag: True}, cache, window) for flag in POLICY_FLAGS}}


def _delta(before, after):
    if not before:
        return '—' if not after else 'n/a'
    return f'{(after - before) / before * 100:+.1f}%'


def _row(label, result):
    return f"| {label} | {result['total_input']:,} | {result['cached_read']:,} | {result['new_tokens']:,} | {result['rereads']} |"


def markdown(report, label):
    before, after = report['before'], report['after']
    lines = [f'### Caché {label}', '',
             '> Datos sintéticos y deterministas del modelo aritmético: no acredita ahorro real.', '',
             '| Variante | Entrada total | Lectura de caché | Tokens nuevos | Relecturas |', '|---|---:|---:|---:|---:|',
             _row('ANTES (política desactivada)', before), _row('DESPUÉS (política activa)', after),
             '| Δ | ' + ' | '.join(_delta(before[key], after[key]) for key in METRICS) + ' |', '',
             '| Un solo ajuste activado | Entrada total | Lectura de caché | Tokens nuevos | Relecturas |', '|---|---:|---:|---:|---:|']
    lines += [_row(flag, result) for flag, result in report['ablation'].items()]
    return '\n'.join(lines)


def validate(fixture):
    def positive(value):
        return type(value) is int and value >= 1
    if not isinstance(fixture, dict) or not isinstance(fixture.get('name'), str) or not fixture['name']:
        raise ValueError('El fixture necesita un nombre.')
    for key in NUMBERS:
        if not positive(fixture.get(key)):
            raise ValueError(f'El fixture necesita {key} como entero positivo.')
    files = fixture.get('files')
    if not isinstance(files, dict) or not files or not all(isinstance(k, str) and positive(v) for k, v in files.items()):
        raise ValueError('El fixture necesita files: nombre -> líneas positivas.')
    subtasks = fixture.get('subtasks')
    if not isinstance(subtasks, list) or not subtasks:
        raise ValueError('El fixture necesita subtareas.')
    ids = [item.get('id') if isinstance(item, dict) else None for item in subtasks]
    if not all(isinstance(i, str) and i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('Las subtareas necesitan id único.')
    for item in subtasks:
        reads = item.get('reads')
        if not positive(item.get('turns')) or not isinstance(reads, list):
            raise ValueError(f"La subtarea {item['id']} necesita turns positivo y reads.")
        for spec in reads:
            if (not isinstance(spec, dict) or spec.get('file') not in files or not positive(spec.get('needed_lines'))
                    or type(spec.get('changed', False)) is not bool):
                raise ValueError(f"Lectura no válida en la subtarea {item['id']}.")
    return fixture


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--fixture', help='JSON con el formato de DEFAULT_FIXTURE')
    parser.add_argument('--cache', choices=('cold', 'warm', 'both'), default='both')
    parser.add_argument('--window', type=int, default=120, help='read_window_lines (1..2000)')
    parser.add_argument('--format', choices=('markdown', 'json'), default='markdown')
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.window <= 2000:
            raise ValueError('La ventana debe estar entre 1 y 2000 líneas.')
        fixture = validate(json.loads(Path(args.fixture).read_text()) if args.fixture else DEFAULT_FIXTURE)
    except (OSError, ValueError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 1
    caches = ('cold', 'warm') if args.cache == 'both' else (args.cache,)
    reports = {cache: compare(fixture, cache, args.window) for cache in caches}
    if args.format == 'json':
        print(json.dumps(reports, indent=2, sort_keys=True))
        return 0
    print(f"## Benchmark sintético de contexto · fixture `{fixture['name']}` · ventana {args.window} líneas\n")
    print('Para medir ahorro real, ejecuta el protocolo A/B de docs/RENDIMIENTO.md.\n')
    print('\n\n'.join(markdown(report, cache) for cache, report in reports.items()))
    return 0


if __name__ == '__main__':
    sys.exit(main())

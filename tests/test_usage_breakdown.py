import contextlib
import itertools
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/usage'))
import breakdown
import prices

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


def stamp(days_ago=0):
    return (NOW - timedelta(days=days_ago)).isoformat().replace('+00:00', 'Z')


def iso(moment):
    return moment.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def claude_line(message_id, *, model='claude-opus-5-5', cwd='/home/u/Work/a', tools=(), days_ago=0,
                usage=(10, 20, 30, 40), at=None):
    entry = {'type': 'assistant', 'timestamp': iso(at) if at else stamp(days_ago), 'message': {
        'id': message_id, 'model': model,
        'usage': {'input_tokens': usage[0], 'output_tokens': usage[1],
                  'cache_read_input_tokens': usage[2], 'cache_creation_input_tokens': usage[3]},
        'content': [{'type': 'tool_use', 'name': name} for name in tools] or [{'type': 'text', 'text': 'hi'}]}}
    if cwd is not None:
        entry['cwd'] = cwd
    if at is False:
        del entry['timestamp']
    return json.dumps(entry)


def codex_context(cwd='/home/u/Work/a', model='gpt-6.1-sol', days_ago=0):
    return json.dumps({'timestamp': stamp(days_ago), 'type': 'turn_context', 'payload': {'cwd': cwd, 'model': model}})


def codex_tool(name='exec', kind='custom_tool_call', days_ago=0):
    return json.dumps({'timestamp': stamp(days_ago), 'type': 'response_item', 'payload': {'type': kind, 'name': name}})


_COUNTER = itertools.count(1)


def codex_count(input_tokens=1000, cached=400, written=0, output=100, days_ago=0, record=True, at=None, total_id=None):
    usage = {'input_tokens': input_tokens, 'cached_input_tokens': cached, 'cache_write_input_tokens': written,
             'output_tokens': output, 'reasoning_output_tokens': 7, 'total_tokens': input_tokens + output}
    total = dict(usage, total_tokens=total_id or next(_COUNTER))  # cumulative totals only repeat when Codex repeats the event
    when = iso(at) if at else stamp(days_ago)
    event = {'timestamp': when, 'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {'last_token_usage': usage, 'total_token_usage': total}}}
    if at is False:
        del event['timestamp']
    lines = [json.dumps(event)]
    if record:
        lines.append(json.dumps({'timestamp': when, 'type': 'event_msg', 'payload': {'type': 'token_usage_record'}}))
    return '\n'.join(lines)


@contextlib.contextmanager
def denied(path, function='scandir'):
    """``os.<function>`` raises PermissionError for ``path`` only; every other call goes to the real one."""
    real = getattr(os, function)

    def guarded(target, *args, **kwargs):
        if isinstance(target, (str, os.PathLike)) and os.fspath(target) == str(path):
            raise PermissionError(13, 'denied', str(path))
        return real(target, *args, **kwargs)
    with patch.object(os, function, guarded):
        yield


class _TypeDeniedEntry:
    """A DirEntry whose type cannot be told, like one on a filesystem that denies the lookup."""
    def __init__(self, entry):
        self.name, self.path = entry.name, entry.path

    def is_dir(self, *, follow_symlinks=True):
        raise PermissionError(13, 'denied', self.path)

    is_file = is_symlink = is_dir

    def stat(self, *, follow_symlinks=True):
        raise PermissionError(13, 'denied', self.path)


class _Entries:
    def __init__(self, entries, name):
        self.entries, self.name = entries, name

    def __enter__(self):
        self.entries.__enter__()
        return self

    def __exit__(self, *error):
        return self.entries.__exit__(*error)

    def __iter__(self):
        return (_TypeDeniedEntry(e) if e.name == self.name else e for e in self.entries)

    def close(self):
        self.entries.close()


@contextlib.contextmanager
def entry_type_denied(name='unreadable-subtree'):
    """Real ``os.scandir`` everywhere, but the entry called ``name`` cannot be classified (PermissionError)."""
    real = os.scandir
    with patch.object(os, 'scandir', lambda *args, **kwargs: _Entries(real(*args, **kwargs), name)):
        yield


START = datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc)
US =timedelta(microseconds=1)


def without_since_start(body):
    return {key: value for key, value in body.items() if key != 'sinceStart'}


def count_opens(test, suffix='.jsonl'):
    """Opens per transcript path while ``test.addCleanup`` restores Path.open."""
    opened, real = {}, Path.open

    def counting(self, *args, **kwargs):
        if self.name.endswith(suffix):
            opened[str(self)] = opened.get(str(self), 0) + 1
        return real(self, *args, **kwargs)
    Path.open = counting
    test.addCleanup(setattr, Path, 'open', real)
    return opened


MISSING = object()
INVALID_COUNTERS = (MISSING, None, True, False, '12', -1, 1.0, 1.5, float('nan'), float('inf'), 10 ** 15)


def claude_raw(message_id, fields, at):
    """An assistant line whose usage is exactly ``fields`` (a field set to MISSING is left out)."""
    usage = {key: value for key, value in fields.items() if value is not MISSING}
    return json.dumps({'type': 'assistant', 'timestamp': iso(at), 'cwd': '/home/u/Work/a', 'message': {
        'id': message_id, 'model': 'claude-opus-5-5', 'usage': usage, 'content': [{'type': 'text', 'text': 'hi'}]}})


OVERFLOW_STAMPS = ('0001-01-01T00:00:00+01:00', '9999-12-31T23:59:59-01:00')  # valid ISO, but not representable in UTC


def with_stamp(line, stamp):
    entry = json.loads(line)
    entry['timestamp'] = stamp
    return json.dumps(entry)


CLAUDE_OK = {'input_tokens': 10, 'output_tokens': 5, 'cache_read_input_tokens': 20, 'cache_creation_input_tokens': 30}


def codex_raw(fields, at):
    """A token_count whose last_token_usage is exactly ``fields`` (MISSING leaves a key out); each has its own total."""
    usage = {key: value for key, value in fields.items() if value is not MISSING}
    return json.dumps({'timestamp': iso(at), 'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
        'last_token_usage': usage, 'total_token_usage': {'total_tokens': next(_COUNTER)}}}})


CODEX_OK = {'input_tokens': 1000, 'cached_input_tokens': 400, 'output_tokens': 100}


class ClaudeScanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def write(self, *lines, name='session.jsonl'):
        folder = self.root / '-home-u-Work-a'
        folder.mkdir(exist_ok=True)
        (folder / name).write_text('\n'.join(lines) + '\n')

    def scan(self, days=30):
        return breakdown.scan_claude(self.root, prices.DEFAULT_PRICES, days, NOW)

    def test_a_message_repeated_per_tool_call_is_counted_once(self):
        self.write(claude_line('m1', tools=['Bash']), claude_line('m1', tools=['Bash']))
        result = self.scan()
        self.assertEqual(result['byKind'], {'input': 10, 'output': 20, 'cacheRead': 30, 'cacheWrite': 40})

    def test_tokens_are_split_by_kind_project_and_model(self):
        self.write(claude_line('m1'), claude_line('m2', model='claude-sonnet-5-5', cwd='/home/u/Work/b', usage=(1, 2, 3, 4)))
        result = self.scan()
        self.assertEqual(result['byKind'], {'input': 11, 'output': 22, 'cacheRead': 33, 'cacheWrite': 44})
        self.assertEqual({row['model']: row['total'] for row in result['byModel']},
                         {'claude-opus-5-5': 100, 'claude-sonnet-5-5': 10})
        self.assertEqual({row['path']: row['total'] for row in result['byProject']},
                         {'/home/u/Work/a': 100, '/home/u/Work/b': 10})
        self.assertEqual(result['windowDays'], 30)

    def test_cost_is_priced_per_model_and_summed(self):
        self.write(claude_line('m1', usage=(1_000_000, 0, 0, 0)))
        result = self.scan()
        self.assertAlmostEqual(result['costUsd'], 4.00)
        self.assertAlmostEqual(result['byProject'][0]['costUsd'], 4.00)

    def test_tools_in_later_lines_of_the_same_message_are_attributed(self):
        # Claude writes each content block of one message as its own line, all with the same id and usage.
        text_first = claude_line('m1', usage=(100, 100, 50, 50))
        tool_later = claude_line('m1', tools=['Bash'], usage=(100, 100, 50, 50))
        self.write(text_first, tool_later)
        result = self.scan()
        self.assertEqual({row['name']: row['total'] for row in result['byTool']}, {'Bash': 300})
        self.assertEqual(result['byKind']['input'], 100)

    def test_the_largest_usage_of_a_repeated_message_wins(self):
        self.write(claude_line('m1', usage=(10, 5, 0, 0)), claude_line('m1', usage=(10, 20, 0, 0)))
        self.assertEqual(self.scan()['byKind'], {'input': 10, 'output': 20, 'cacheRead': 0, 'cacheWrite': 0})

    def test_tools_share_a_messages_tokens_equally(self):
        self.write(claude_line('m1', tools=['Bash', 'Read'], usage=(100, 100, 50, 50)), claude_line('m2', usage=(1, 1, 1, 1)))
        tools = {row['name']: row['total'] for row in self.scan()['byTool']}
        self.assertEqual(tools, {'Bash': 150, 'Read': 150, '(solo texto)': 4})

    def test_messages_outside_the_window_are_ignored(self):
        self.write(claude_line('old', days_ago=31), claude_line('new', days_ago=1))
        self.assertEqual(self.scan()['byKind']['input'], 10)

    def test_corrupt_and_truncated_lines_are_skipped(self):
        self.write(claude_line('m1'), '{"type":"assist', '', 'not json at all', claude_line('m2'))
        self.assertEqual(self.scan()['byKind']['input'], 20)

    def test_missing_cwd_goes_to_no_project(self):
        self.write(claude_line('m1', cwd=None))
        self.assertEqual(self.scan()['byProject'][0]['path'], '(sin proyecto)')

    def test_unpriced_model_has_null_cost_and_is_listed(self):
        self.write(claude_line('m1', usage=(1_000_000, 0, 0, 0)), claude_line('m2', model='<synthetic>'))
        result = self.scan()
        synthetic = next(row for row in result['byModel'] if row['model'] == '<synthetic>')
        self.assertIsNone(synthetic['costUsd'])
        self.assertEqual(result['unpricedModels'], ['<synthetic>'])
        self.assertAlmostEqual(result['costUsd'], 4.00)

    def test_all_unpriced_gives_null_total(self):
        self.write(claude_line('m1', model='<synthetic>'))
        self.assertIsNone(self.scan()['costUsd'])

    def test_top_eight_projects_and_tools(self):
        lines = [claude_line('m%d' % n, cwd='/p/%d' % n, tools=['T%d' % n], usage=(n + 1, 0, 0, 0)) for n in range(10)]
        self.write(*lines)
        result = self.scan()
        self.assertEqual(len(result['byProject']), 8)
        self.assertEqual(len(result['byTool']), 8)
        self.assertEqual([row['path'] for row in result['byProject']][:2], ['/p/9', '/p/8'])

    def test_message_without_usage_is_ignored(self):
        broken = json.dumps({'type': 'assistant', 'timestamp': stamp(), 'message': {'id': 'x', 'model': 'm', 'content': None}})
        self.write(broken, claude_line('m1'))
        self.assertEqual(self.scan()['byKind']['input'], 10)

    def test_no_messages_returns_none(self):
        self.assertIsNone(self.scan())
        self.write('{}', '[1,2]')
        self.assertIsNone(self.scan())


class ClaudeSinceStartTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def write(self, *lines, name='session.jsonl'):
        folder = self.root / '-home-u-Work-a'
        folder.mkdir(exist_ok=True)
        (folder / name).write_text('\n'.join(lines) + '\n')
        return folder / name

    def scan(self, since=START, days=30):
        return breakdown.scan_claude(self.root, prices.DEFAULT_PRICES, days, NOW, since=since)

    def test_claude_since_start_is_inclusive(self):
        self.write(claude_line('m0', at=START - US, usage=(10, 5, 20, 30)), claude_line('m1', at=START, usage=(10, 5, 20, 30)),
                   claude_line('m2', at=START + US, usage=(10, 5, 20, 30)))
        self.assertEqual(self.scan()['sinceStart'], {'since': START.isoformat(), 'total_input': 120, 'output': 10})

    def test_claude_since_start_uses_all_input_classes_once(self):
        self.write(claude_line('m1', at=START + timedelta(days=1), usage=(10, 5, 20, 30)))
        self.assertEqual(self.scan()['sinceStart'], {'since': START.isoformat(), 'total_input': 60, 'output': 5})

    def test_claude_repeated_id_multiple_lines_counts_maxima_once(self):
        later = START + timedelta(days=1)
        first = [claude_line('m1', at=later, usage=(10, 1, 0, 0)), claude_line('m1', at=later, usage=(10, 5, 20, 0)),
                 claude_line('m1', at=later, usage=(10, 5, 20, 30))]
        self.write(*reversed(first), name='b.jsonl')
        self.write(*first, name='a.jsonl')
        self.assertEqual(self.scan()['sinceStart'], {'since': START.isoformat(), 'total_input': 60, 'output': 5})

    def test_claude_repeated_id_crossing_start_uses_earliest_timestamp(self):
        before, after = claude_line('m1', at=START - US, usage=(1, 1, 0, 0)), claude_line('m1', at=START + US, usage=(100, 50, 0, 0))
        for lines in ((before, after), (after, before)):
            self.write(*lines)
            self.assertEqual(self.scan()['sinceStart'], {'since': START.isoformat(), 'total_input': 0, 'output': 0})

    def test_claude_timestamp_can_be_recovered_from_another_line_of_same_id(self):
        self.write(claude_line('m1', at=False, usage=(10, 5, 20, 30)), claude_line('m1', at=START + timedelta(days=1), usage=(10, 1, 0, 0)))
        self.assertEqual(self.scan()['sinceStart'], {'since': START.isoformat(), 'total_input': 60, 'output': 5})

    def test_claude_message_without_any_valid_timestamp_publishes_no_known_zero(self):
        self.write(claude_line('m1', at=False, usage=(10, 5, 20, 30)), claude_line('m2', at=START + timedelta(days=1)))
        result = self.scan()
        self.assertNotIn('sinceStart', result)
        self.assertEqual(result['windowDays'], 30)

    def test_claude_old_mtime_does_not_hide_post_start_message(self):
        path = self.write(claude_line('m1', at=START + US, usage=(10, 5, 20, 30)))
        old = (NOW - timedelta(days=90)).timestamp()
        os.utime(path, (old, old))
        self.assertEqual(self.scan()['sinceStart']['total_input'], 60)

    def test_claude_lifetime_survives_without_recent_messages(self):
        start = NOW - timedelta(days=100)
        self.write(claude_line('m1', at=NOW - timedelta(days=60), usage=(10, 5, 20, 30)))
        result = self.scan(since=start)
        self.assertEqual(result['sinceStart'], {'since': start.isoformat(), 'total_input': 60, 'output': 5})
        self.assertEqual((result['windowDays'], result['byKind'], result['byProject'], result['byTool']),
                         (30, dict.fromkeys(breakdown.KIND_NAMES, 0), [], []))

    def test_claude_lifetime_does_not_change_legacy_breakdown(self):
        self.write(claude_line('m1', days_ago=1, tools=['Bash', 'Edit']), claude_line('m1', days_ago=1, tools=['Bash']),
                   claude_line('m2', days_ago=40, usage=(1, 2, 3, 4)), claude_line('m3', days_ago=2, cwd='/p/b', model='mystery'),
                   claude_line('m4', at=False), 'garbage')
        self.assertEqual(without_since_start(self.scan(since=NOW - timedelta(days=90))), self.scan(since=None))
        self.assertNotIn('sinceStart', self.scan(since=None))

    def test_claude_each_transcript_is_opened_once_with_since(self):
        first = self.write(claude_line('m1', days_ago=1), name='a.jsonl')
        second = self.write(claude_line('m2', days_ago=50), name='b.jsonl')
        opened = count_opens(self)
        self.scan(since=NOW - timedelta(days=90))
        self.assertEqual(opened, {str(first): 1, str(second): 1})

    def test_claude_empty_readable_source_is_a_known_zero_and_a_missing_one_is_not(self):
        self.root.mkdir(exist_ok=True)
        result = self.scan()
        self.assertEqual(result['sinceStart'], {'since': START.isoformat(), 'total_input': 0, 'output': 0})
        self.assertEqual(result['byKind'], dict.fromkeys(breakdown.KIND_NAMES, 0))
        self.assertIsNone(breakdown.scan_claude(self.root / 'missing', prices.DEFAULT_PRICES, 30, NOW, since=START))

    def test_claude_start_is_reported_in_utc(self):
        self.write(claude_line('m1', at=START + US))
        local = START.astimezone(timezone(timedelta(hours=-3)))
        self.assertEqual(self.scan(since=local)['sinceStart']['since'], START.isoformat())


    def test_claude_directory_enumeration_error_fails_scan(self):
        folder = self.root / '-home-u-Work-a'
        folder.mkdir()
        for since in (START, None):
            with denied(folder), self.assertRaises(PermissionError):
                self.scan(since=since)

    def test_claude_partial_tree_is_not_a_complete_measurement(self):
        self.write(claude_line('m1', at=START + timedelta(days=1)))
        hidden = self.root / '-home-u-Work-b'
        hidden.mkdir()
        (hidden / 'other.jsonl').write_text(claude_line('m2', at=START + timedelta(days=1)) + '\n')
        for since in (START, None):
            with denied(hidden), self.assertRaises(PermissionError):
                self.scan(since=since)

    def test_entry_type_permission_error_aborts_scan(self):
        self.write(claude_line('m1', at=START + timedelta(days=1)))
        (self.root / 'unreadable-subtree').mkdir()
        for since in (START, None):
            with entry_type_denied(), self.assertRaises(PermissionError):
                self.scan(since=since)

    def test_claude_root_that_cannot_be_inspected_is_not_a_missing_source(self):
        self.root.mkdir(exist_ok=True)
        with denied(self.root, 'stat'), self.assertRaises(PermissionError):
            self.scan()

    def test_claude_source_disappearing_mid_scan_does_not_publish_partial_total(self):
        self.write(claude_line('m1', at=START + timedelta(days=1)))
        folder = self.root / '-home-u-Work-a'
        real = os.scandir

        def vanishing(target, *args, **kwargs):
            if os.fspath(target) == str(folder):
                raise FileNotFoundError(2, 'gone', str(folder))
            return real(target, *args, **kwargs)
        with patch.object(os, 'scandir', vanishing), self.assertRaises(FileNotFoundError):
            self.scan()


    def test_claude_invalid_or_missing_required_counter_prevents_known_total(self):
        later = START + timedelta(days=1)
        for field in CLAUDE_OK:
            for bad in INVALID_COUNTERS:
                with self.subTest(field=field, bad=repr(bad)):
                    self.write(claude_raw('m0', CLAUDE_OK, later), claude_raw('m1', {**CLAUDE_OK, field: bad}, later))
                    result = self.scan()
                    self.assertNotIn('sinceStart', result)
                    self.assertEqual(result['windowDays'], 30)
        with self.subTest(case='empty usage'):
            self.write(claude_raw('m1', {}, later))
            self.assertNotIn('sinceStart', self.scan())

    def test_invalid_relevant_usage_among_valid_rows_does_not_publish_partial_total(self):
        later = START + timedelta(days=1)
        self.write(*[claude_raw('ok%d' % n, CLAUDE_OK, later) for n in range(5)], claude_raw('bad', {'output_tokens': 3}, later))
        self.assertNotIn('sinceStart', self.scan())

    def test_valid_all_zero_usage_remains_known_zero(self):
        self.write(claude_raw('m1', dict.fromkeys(CLAUDE_OK, 0), START + timedelta(days=1)))
        self.assertEqual(self.scan()['sinceStart'], {'since': START.isoformat(), 'total_input': 0, 'output': 0})

    def test_complete_duplicate_can_resolve_partial_copy_without_counting_twice(self):
        later = START + timedelta(days=1)
        partial, complete = claude_raw('m1', {'input_tokens': 10, 'output_tokens': 1}, later), claude_raw('m1', CLAUDE_OK, later)
        for lines in ((partial, complete), (complete, partial)):
            with self.subTest(order=lines[0] is partial):
                self.write(*lines)
                self.assertEqual(self.scan()['sinceStart'], {'since': START.isoformat(), 'total_input': 60, 'output': 5})

    def test_unresolved_partial_message_keeps_measurement_unknown(self):
        later = START + timedelta(days=1)
        self.write(claude_raw('m1', {'input_tokens': 10, 'output_tokens': 5, 'cache_read_input_tokens': 20}, later),
                   claude_raw('m1', {'input_tokens': 10, 'output_tokens': 5}, later))
        self.assertNotIn('sinceStart', self.scan())

    def test_invalid_value_in_one_copy_is_not_resolved_by_another_copy(self):
        later = START + timedelta(days=1)
        self.write(claude_raw('m1', {**CLAUDE_OK, 'cache_read_input_tokens': '20'}, later), claude_raw('m1', CLAUDE_OK, later))
        self.assertNotIn('sinceStart', self.scan())

    def test_invalid_consumption_proven_before_the_start_does_not_invalidate(self):
        self.write(claude_raw('old', {}, START - US), claude_raw('m1', CLAUDE_OK, START))
        self.assertEqual(self.scan()['sinceStart'], {'since': START.isoformat(), 'total_input': 60, 'output': 5})

    def test_non_consumption_event_does_not_invalidate_empty_measurement(self):
        later = START + timedelta(days=1)
        no_usage = json.dumps({'type': 'assistant', 'timestamp': iso(later), 'message': {'id': 'n1', 'content': []}})
        user = json.dumps({'type': 'user', 'timestamp': iso(later), 'message': {'role': 'user', 'content': 'hi'}})
        self.write(no_usage, user)
        self.assertEqual(self.scan()['sinceStart'], {'since': START.isoformat(), 'total_input': 0, 'output': 0})

    def test_invalid_measurement_publishes_detail_without_measurement_block(self):
        self.write(claude_line('m1', days_ago=1, tools=['Bash']), claude_raw('bad', {}, NOW - timedelta(days=2)))
        result = self.scan(since=NOW - timedelta(days=5))
        self.assertNotIn('sinceStart', result)
        self.assertEqual(result, self.scan(since=None))

    def test_claude_utc_overflow_timestamp_prevents_known_total(self):
        later = START + timedelta(days=1)
        for stamp in OVERFLOW_STAMPS:
            with self.subTest(stamp=stamp):
                self.write(claude_raw('ok', CLAUDE_OK, later), with_stamp(claude_raw('x', CLAUDE_OK, later), stamp))
                result = self.scan()
                self.assertNotIn('sinceStart', result)
                self.assertEqual(result['windowDays'], 30)
        with self.subTest(case='equivalent offset'):
            self.write(with_stamp(claude_raw('ok', CLAUDE_OK, later), later.astimezone(timezone(timedelta(hours=-3))).isoformat()))
            self.assertEqual(self.scan()['sinceStart'], {'since': START.isoformat(), 'total_input': 60, 'output': 5})


class CodexScanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def write(self, *lines, name='rollout.jsonl'):
        folder = self.root / '2026' / '11' / '02'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text('\n'.join(lines) + '\n')

    def scan(self, days=30, price_table=None):
        return breakdown.scan_codex(self.root, price_table or prices.DEFAULT_PRICES, days, NOW)

    def test_codex_input_includes_cached_tokens(self):
        self.write(codex_context(), codex_count(input_tokens=17438, cached=12288, output=126))
        self.assertEqual(self.scan()['byKind'], {'input': 5150, 'output': 126, 'cacheRead': 12288, 'cacheWrite': 0})

    def test_cache_writes_are_not_counted_as_fresh_input(self):
        self.write(codex_context(), codex_count(input_tokens=1000, cached=300, written=200, output=0))
        self.assertEqual(self.scan()['byKind'], {'input': 500, 'output': 0, 'cacheRead': 300, 'cacheWrite': 200})

    def test_cwd_and_model_come_from_the_latest_turn_context(self):
        self.write(codex_context('/p/one', 'gpt-6-sol'), codex_count(), codex_context('/p/two', 'gpt-6.1-sol'), codex_count())
        result = self.scan()
        self.assertEqual({row['path'] for row in result['byProject']}, {'/p/one', '/p/two'})
        self.assertEqual({row['model'] for row in result['byModel']}, {'gpt-6-sol', 'gpt-6.1-sol'})

    def test_tool_calls_between_two_token_counts_get_that_turns_tokens(self):
        self.write(codex_context(), codex_tool('exec'), codex_tool('exec'), codex_tool('wait', 'function_call'),
                   codex_count(input_tokens=600, cached=0, output=0, record=False),
                   codex_count(input_tokens=60, cached=0, output=0, record=False))
        tools = {row['name']: row['total'] for row in self.scan()['byTool']}
        self.assertEqual(tools, {'exec': 400, 'wait': 200, '(solo texto)': 60})

    def test_token_usage_record_events_are_not_double_counted(self):
        self.write(codex_context(), codex_count(input_tokens=100, cached=0, output=0, record=True))
        self.assertEqual(self.scan()['byKind']['input'], 100)

    def test_codex_models_have_null_cost_and_are_listed_as_unpriced(self):
        self.write(codex_context(), codex_count())
        result = self.scan()
        self.assertIsNone(result['costUsd'])
        self.assertIsNone(result['byModel'][0]['costUsd'])
        self.assertEqual(result['unpricedModels'], ['gpt-6.1-sol'])

    def test_a_filled_in_price_is_used(self):
        table = {**prices.DEFAULT_PRICES, 'gpt-6.1-sol': {'input': 1.0, 'output': 2.0, 'cacheRead': 0.1, 'cacheWrite': 1.0}}
        self.write(codex_context(), codex_count(input_tokens=2_000_000, cached=1_000_000, output=500_000))
        self.assertAlmostEqual(self.scan(price_table=table)['costUsd'], 1.0 + 1.0 + 0.1)

    def test_a_repeated_token_count_with_unchanged_totals_is_counted_once(self):
        # Codex re-emits token_count when only the rate limits change; the cumulative totals do not move.
        repeated = codex_count(input_tokens=100, cached=0, output=0, record=False)
        self.write(codex_context(), repeated, repeated, codex_count(input_tokens=50, cached=0, output=0, record=False))
        self.assertEqual(self.scan()['byKind']['input'], 150)

    def test_symlinked_transcripts_are_not_followed(self):
        outside = self.root.parent / ('outside-%d.jsonl' % os.getpid())
        outside.write_text(codex_context() + '\n' + codex_count(input_tokens=999, cached=0, output=0) + '\n')
        self.addCleanup(outside.unlink)
        folder = self.root / 'x'
        folder.mkdir()
        (folder / 'alias.jsonl').symlink_to(outside)
        self.assertIsNone(self.scan())

    def test_an_unreadable_transcript_fails_the_scan_instead_of_hiding_it(self):
        self.write(codex_context(), codex_count())
        target = next(self.root.rglob('rollout.jsonl'))
        target.chmod(0)
        self.addCleanup(target.chmod, 0o600)
        if os.access(target, os.R_OK):
            self.skipTest('running with privileges that ignore file modes')
        with self.assertRaises(PermissionError):
            self.scan()

    def test_a_file_that_disappears_during_the_scan_is_tolerated(self):
        self.write(codex_context(), codex_count())
        original = breakdown._recent_files

        def vanishing(root, cutoff):
            for path in original(root, cutoff):
                path.unlink()
                yield path
        breakdown._recent_files = vanishing
        self.addCleanup(setattr, breakdown, '_recent_files', original)
        self.assertIsNone(self.scan())

    def test_archived_sessions_still_count(self):
        other = tempfile.TemporaryDirectory(); self.addCleanup(other.cleanup)
        archived = Path(other.name)
        (archived / 'old.jsonl').write_text(codex_context('/p/archived') + '\n' + codex_count(input_tokens=77, cached=0, output=0) + '\n')
        self.write(codex_context('/p/live'), codex_count(input_tokens=23, cached=0, output=0))
        result = breakdown.scan_codex(self.root, prices.DEFAULT_PRICES, 30, NOW, extra_dirs=(archived,))
        self.assertEqual(result['byKind']['input'], 100)
        self.assertEqual({row['path'] for row in result['byProject']}, {'/p/archived', '/p/live'})

    def test_codex_corrupt_lines_and_missing_cwd(self):
        self.write('{"type":"event_ms', codex_count(), '', 'garbage')
        result = self.scan()
        self.assertEqual(result['byProject'][0]['path'], '(sin proyecto)')
        self.assertEqual(result['byModel'][0]['model'], 'unknown')

    def test_session_meta_cwd_is_the_fallback_project(self):
        meta = json.dumps({'timestamp': stamp(), 'type': 'session_meta', 'payload': {'cwd': '/p/meta'}})
        self.write(meta, codex_count())
        self.assertEqual(self.scan()['byProject'][0]['path'], '/p/meta')

    def test_codex_window_and_empty_return_none(self):
        self.assertIsNone(self.scan())
        self.write(codex_context(), codex_count(days_ago=31))
        self.assertIsNone(self.scan())


class CodexSinceStartTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def write(self, *lines, name='rollout.jsonl', root=None):
        folder = (root or self.root) / '2026' / '11' / '02'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text('\n'.join(lines) + '\n')
        return folder / name

    def scan(self, since=START, days=30, extra_dirs=()):
        return breakdown.scan_codex(self.root, prices.DEFAULT_PRICES, days, NOW, extra_dirs, since=since)

    def test_codex_since_start_is_inclusive(self):
        self.write(codex_context(), codex_count(100, 40, output=10, at=START - US), codex_count(100, 40, output=10, at=START),
                   codex_count(100, 40, output=10, at=START + US))
        self.assertEqual(self.scan()['sinceStart'], {'since': START.isoformat(), 'total_input': 200, 'output': 20})

    def test_codex_input_tokens_already_include_the_cache_and_count_once(self):
        self.write(codex_context(), codex_count(17438, 12288, written=100, output=126, at=START + timedelta(days=1)))
        self.assertEqual(self.scan()['sinceStart'], {'since': START.isoformat(), 'total_input': 17438, 'output': 126})

    def test_codex_repeated_event_counts_once(self):
        repeated = codex_count(100, 0, output=5, at=START + timedelta(days=1), record=False)
        self.write(codex_context(), repeated, repeated, codex_count(50, 0, output=1, at=START + timedelta(days=2), record=False))
        self.assertEqual(self.scan()['sinceStart'], {'since': START.isoformat(), 'total_input': 150, 'output': 6})

    def test_codex_repeat_of_an_event_before_the_start_is_still_a_repeat(self):
        self.write(codex_context(), codex_count(100, 0, output=5, at=START - US, total_id=900, record=False),
                   codex_count(100, 0, output=5, at=START + US, total_id=900, record=False),
                   codex_count(7, 0, output=1, at=START + timedelta(days=1), record=False))
        self.assertEqual(self.scan()['sinceStart'], {'since': START.isoformat(), 'total_input': 7, 'output': 1})

    def test_codex_event_without_a_valid_timestamp_publishes_no_known_zero(self):
        self.write(codex_context(), codex_count(100, 0, at=False, record=False), codex_count(10, 0, days_ago=1, record=False))
        result = self.scan()
        self.assertNotIn('sinceStart', result)
        self.assertEqual(result['windowDays'], 30)

    def test_codex_events_without_usage_are_not_unknown(self):
        broken = json.dumps({'type': 'event_msg', 'payload': {'type': 'token_count', 'info': None}})
        self.write(codex_context(), broken, codex_count(10, 0, output=2, at=START + US, record=False))
        self.assertEqual(self.scan()['sinceStart'], {'since': START.isoformat(), 'total_input': 10, 'output': 2})

    def test_codex_old_mtime_does_not_hide_post_start_events(self):
        path = self.write(codex_context(), codex_count(10, 0, output=2, at=START + US, record=False))
        old = (NOW - timedelta(days=90)).timestamp()
        os.utime(path, (old, old))
        self.assertEqual(self.scan()['sinceStart']['total_input'], 10)

    def test_codex_lifetime_survives_without_recent_events(self):
        start = NOW - timedelta(days=100)
        self.write(codex_context(), codex_count(10, 0, output=2, at=NOW - timedelta(days=60), record=False))
        result = self.scan(since=start)
        self.assertEqual(result['sinceStart'], {'since': start.isoformat(), 'total_input': 10, 'output': 2})
        self.assertEqual((result['windowDays'], result['byKind'], result['byProject'], result['byTool']),
                         (30, dict.fromkeys(breakdown.KIND_NAMES, 0), [], []))

    def test_codex_lifetime_does_not_change_legacy_breakdown(self):
        self.write(codex_context(), codex_tool('exec'), codex_count(1000, 400, days_ago=1), codex_count(5, 0, days_ago=40),
                   codex_count(9, 0, at=False), 'garbage')
        self.assertEqual(without_since_start(self.scan(since=NOW - timedelta(days=90))), self.scan(since=None))
        self.assertNotIn('sinceStart', self.scan(since=None))

    def test_codex_each_transcript_and_extra_dir_is_opened_once(self):
        archived = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, archived)
        first = self.write(codex_context(), codex_count(10, 0, days_ago=1))
        second = self.write(codex_context(), codex_count(20, 0, days_ago=50), root=archived)
        opened = count_opens(self)
        result = self.scan(since=NOW - timedelta(days=90), extra_dirs=(archived,))
        self.assertEqual(opened, {str(first): 1, str(second): 1})
        self.assertEqual(result['sinceStart']['total_input'], 30)

    def test_codex_empty_readable_source_is_a_known_zero_and_a_missing_one_is_not(self):
        result = self.scan()
        self.assertEqual(result['sinceStart'], {'since': START.isoformat(), 'total_input': 0, 'output': 0})
        self.assertIsNone(breakdown.scan_codex(self.root / 'missing', prices.DEFAULT_PRICES, 30, NOW, since=START))

    def test_codex_start_is_reported_in_utc(self):
        self.write(codex_context(), codex_count(10, 0, at=START + US, record=False))
        local = START.astimezone(timezone(timedelta(hours=-3)))
        self.assertEqual(self.scan(since=local)['sinceStart']['since'], START.isoformat())


    def test_codex_archive_enumeration_error_prevents_partial_total(self):
        archived = self.root.parent / ('archived-%d' % os.getpid())
        archived.mkdir()
        self.addCleanup(shutil.rmtree, archived, True)
        self.write(codex_context(), codex_count(at=START + timedelta(days=1)))
        for since in (START, None):
            with denied(archived), self.assertRaises(PermissionError):
                self.scan(since=since, extra_dirs=(archived,))

    def test_entry_type_permission_error_aborts_scan(self):
        archived = self.root.parent / ('archived-%d' % os.getpid())
        (archived / 'unreadable-subtree').mkdir(parents=True)
        self.addCleanup(shutil.rmtree, archived, True)
        self.write(codex_context(), codex_count(at=START + timedelta(days=1)))
        (self.root / 'unreadable-subtree').mkdir()
        for since in (START, None):
            with entry_type_denied(), self.assertRaises(PermissionError):
                self.scan(since=since)
            (self.root / 'unreadable-subtree').rmdir()
            with entry_type_denied(), self.assertRaises(PermissionError):
                self.scan(since=since, extra_dirs=(archived,))
            (self.root / 'unreadable-subtree').mkdir()

    def test_codex_archive_that_cannot_be_inspected_does_not_hide_behind_a_readable_root(self):
        archived = self.root.parent / ('archived-%d' % os.getpid())
        self.write(codex_context(), codex_count(at=START + timedelta(days=1)))
        with denied(archived, 'stat'), self.assertRaises(PermissionError):
            self.scan(extra_dirs=(archived,))

    def test_codex_partial_tree_is_not_a_complete_measurement(self):
        self.write(codex_context(), codex_count(at=START + timedelta(days=1)))
        hidden = self.root / 'x'
        hidden.mkdir()
        with denied(hidden), self.assertRaises(PermissionError):
            self.scan()

    def test_codex_invalid_or_missing_required_counter_prevents_known_total(self):
        later = START + timedelta(days=1)
        for field in ('input_tokens', 'output_tokens'):
            for bad in INVALID_COUNTERS:
                with self.subTest(field=field, bad=repr(bad)):
                    self.write(codex_context(), codex_raw(CODEX_OK, later), codex_raw({**CODEX_OK, field: bad}, later))
                    result = self.scan()
                    self.assertNotIn('sinceStart', result)
                    self.assertEqual(result['windowDays'], 30)
        with self.subTest(case='empty usage'):
            self.write(codex_context(), codex_raw({}, later))
            self.assertNotIn('sinceStart', self.scan())

    def test_invalid_present_optional_counter_is_not_coerced_to_zero(self):
        later = START + timedelta(days=1)
        for bad in INVALID_COUNTERS[1:]:
            with self.subTest(bad=repr(bad)):
                self.write(codex_context(), codex_raw({**CODEX_OK, 'cached_input_tokens': bad}, later))
                self.assertNotIn('sinceStart', self.scan())
        with self.subTest(case='absent'):
            self.write(codex_context(), codex_raw({**CODEX_OK, 'cached_input_tokens': MISSING}, later))
            self.assertEqual(self.scan()['sinceStart'], {'since': START.isoformat(), 'total_input': 1000, 'output': 100})

    def test_invalid_relevant_usage_among_valid_rows_does_not_publish_partial_total(self):
        later = START + timedelta(days=1)
        self.write(codex_context(), *[codex_raw(CODEX_OK, later) for _ in range(5)], codex_raw({'output_tokens': 3}, later))
        self.assertNotIn('sinceStart', self.scan())

    def test_valid_all_zero_usage_remains_known_zero(self):
        self.write(codex_context(), codex_raw({'input_tokens': 0, 'cached_input_tokens': 0, 'output_tokens': 0}, START + timedelta(days=1)))
        self.assertEqual(self.scan()['sinceStart'], {'since': START.isoformat(), 'total_input': 0, 'output': 0})

    def test_invalid_consumption_proven_before_the_start_does_not_invalidate(self):
        self.write(codex_context(), codex_raw({}, START - US), codex_raw(CODEX_OK, START))
        self.assertEqual(self.scan()['sinceStart'], {'since': START.isoformat(), 'total_input': 1000, 'output': 100})

    def test_non_consumption_event_does_not_invalidate_empty_measurement(self):
        later = START + timedelta(days=1)
        self.write(json.dumps({'timestamp': iso(later), 'type': 'event_msg', 'payload': {'type': 'token_count', 'info': None}}),
                   json.dumps({'timestamp': iso(later), 'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {'total_token_usage': {}}}}))
        self.assertEqual(self.scan()['sinceStart'], {'since': START.isoformat(), 'total_input': 0, 'output': 0})

    def test_invalid_measurement_publishes_detail_without_measurement_block(self):
        self.write(codex_context(), codex_count(days_ago=1), codex_raw({}, NOW - timedelta(days=2)))
        result = self.scan(since=NOW - timedelta(days=5))
        self.assertNotIn('sinceStart', result)
        self.assertEqual(result, self.scan(since=None))

    def test_codex_utc_overflow_timestamp_prevents_known_total(self):
        later = START + timedelta(days=1)
        for stamp in OVERFLOW_STAMPS:
            with self.subTest(stamp=stamp):
                self.write(codex_context(), codex_raw(CODEX_OK, later), with_stamp(codex_raw(CODEX_OK, later), stamp))
                result = self.scan()
                self.assertNotIn('sinceStart', result)
                self.assertEqual(result['windowDays'], 30)
        with self.subTest(case='equivalent offset'):
            self.write(codex_context(), with_stamp(codex_raw(CODEX_OK, later), later.astimezone(timezone(timedelta(hours=-3))).isoformat()))
            self.assertEqual(self.scan()['sinceStart'], {'since': START.isoformat(), 'total_input': 1000, 'output': 100})


if __name__ == '__main__':
    unittest.main()

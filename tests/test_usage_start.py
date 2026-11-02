import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import usage_start

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


class UsageStartTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.folder = self.root / 'omarchy-ai-team'
        patcher = patch.dict(os.environ, {'XDG_STATE_HOME': str(self.root)}); patcher.start(); self.addCleanup(patcher.stop)
        self.start = self.folder / 'usage-start.json'
        self.ledger = self.folder / 'role-usage.jsonl'
        previous = os.umask(0o077); self.addCleanup(os.umask, previous)  # the state is private, as in a real session

    def rows(self, *rows):
        self.folder.mkdir(parents=True, exist_ok=True)
        self.ledger.write_text('\n'.join(row if isinstance(row, str) else json.dumps(row) for row in rows) + '\n')

    @staticmethod
    def row(at, role='code', agent='claude', tokens=1):
        return {'at': at, 'role': role, 'agent': agent, 'tokens': tokens}

    def test_bootstrap_uses_now_and_ignores_older_ledger_entries(self):
        self.rows(self.row('2026-10-05T10:00:00+00:00'), self.row('2026-10-04T23:30:00-03:00', agent='codex-2'),
                  self.row('2026-10-03T00:00:00'), 'not json', self.row('2026-01-01T00:00:00+00:00', role='jefe'))
        self.assertEqual(usage_start.get_or_create_start(now=T0), T0)

    def test_bootstrap_normalises_now_with_another_offset_to_utc(self):
        local = T0.astimezone(timezone(timedelta(hours=-3)))
        self.assertEqual(usage_start.get_or_create_start(now=local), T0)
        self.assertEqual(json.loads(self.start.read_text())['since'], T0.isoformat())

    def test_without_prior_start_uses_now_once(self):
        first = usage_start.get_or_create_start(now=T0)
        saved = self.start.read_bytes()
        self.assertEqual(first, T0)
        self.assertEqual(usage_start.get_or_create_start(now=T0 + timedelta(hours=5)), T0)
        self.assertEqual(self.start.read_bytes(), saved)
        self.assertEqual(json.loads(saved), {'schemaVersion': 1, 'since': T0.isoformat()})

    def test_bootstrap_does_not_read_an_unreadable_or_fifo_ledger(self):
        self.folder.mkdir()
        os.mkfifo(self.ledger)
        with patch.object(usage_start, 'iter_ledger_entries', side_effect=AssertionError('ledger read')):
            self.assertEqual(usage_start.get_or_create_start(now=T0), T0)
        self.assertEqual(self.start.read_text(), json.dumps({'schemaVersion': 1, 'since': T0.isoformat()}) + '\n')

    def test_existing_start_survives_older_ledger_rows_and_selection_changes(self):
        usage_start.get_or_create_start(now=T0)
        self.rows(self.row('2020-01-01T00:00:00+00:00'))
        self.assertEqual(usage_start.get_or_create_start(now=T0 + timedelta(days=2)), T0)

    def test_clock_rollback_does_not_reset_start(self):
        usage_start.get_or_create_start(now=T0)
        self.assertEqual(usage_start.get_or_create_start(now=T0 - timedelta(hours=1)), T0)

    def test_a_start_with_another_offset_is_normalised_to_utc(self):
        self.folder.mkdir()
        self.start.write_text(json.dumps({'schemaVersion': 1, 'since': '2026-11-02T09:00:00-03:00'}))
        self.assertEqual(usage_start.get_or_create_start(now=T0), T0)

    def test_corrupt_start_is_not_replaced(self):
        self.folder.mkdir()
        for content in ('{"schemaVersion": 1, "sin', json.dumps({'schemaVersion': 1, 'since': '2026-11-02T12:00:00'}),
                        json.dumps({'schemaVersion': 2, 'since': T0.isoformat()}), '[]', json.dumps({'schemaVersion': 1})):
            self.start.write_text(content)
            with self.assertRaises(usage_start.UsageStartError):
                usage_start.get_or_create_start(now=T0)
            self.assertEqual(self.start.read_text(), content)

    def test_start_utc_overflow_is_rejected_without_replacement(self):
        self.folder.mkdir()
        for stamp in ('0001-01-01T00:00:00+01:00', '9999-12-31T23:59:59-01:00'):
            content = json.dumps({'schemaVersion': 1, 'since': stamp})
            self.start.write_text(content)
            with self.assertRaises(usage_start.UsageStartError):
                usage_start.get_or_create_start(now=T0)
            self.assertEqual(self.start.read_text(), content)

    def test_ledger_utc_overflow_row_is_skipped_without_losing_valid_rows(self):
        self.rows(self.row('2026-10-01T00:00:00+00:00', tokens=1), self.row('0001-01-01T00:00:00+01:00', tokens=2),
                  self.row('2026-10-02T00:00:00-03:00', tokens=3), self.row('9999-12-31T23:59:59-01:00', tokens=4))
        self.assertEqual([entry.tokens for entry in usage_start.iter_ledger_entries(self.ledger)], [1, 3])

    def elsewhere(self, start=True):
        """A real directory outside the state path (with a valid start); the state path becomes a symlink to it."""
        target = self.root / 'elsewhere'
        target.mkdir(mode=0o700)
        if start:
            (target / 'usage-start.json').write_text(json.dumps({'schemaVersion': 1, 'since': '2026-01-01T00:00:00+00:00'}))
        self.folder.symlink_to(target)
        return target

    @staticmethod
    def snapshot(folder):
        return sorted((path.name, path.stat().st_mode, path.read_bytes()) for path in folder.iterdir())

    def test_symlinked_state_directory_with_existing_start_is_rejected_before_read(self):
        target = self.elsewhere()
        before = self.snapshot(target)
        with self.assertRaises(usage_start.UsageStartError):
            usage_start.get_or_create_start(now=T0)
        self.assertEqual(self.snapshot(target), before)

    def test_symlinked_state_directory_without_start_is_not_modified(self):
        target = self.elsewhere(start=False)
        mode = target.stat().st_mode
        with self.assertRaises(usage_start.UsageStartError):
            usage_start.get_or_create_start(now=T0)
        self.assertEqual((list(target.iterdir()), target.stat().st_mode), ([], mode))

    def test_existing_public_start_is_rejected_without_chmod(self):
        self.folder.mkdir(mode=0o700)
        for mode in (0o644, 0o666):
            with self.subTest(mode=oct(mode)):
                self.start.write_text(json.dumps({'schemaVersion': 1, 'since': T0.isoformat()}))
                self.start.chmod(mode)
                with self.assertRaises(usage_start.UsageStartError):
                    usage_start.get_or_create_start(now=T0)
                self.assertEqual(self.start.stat().st_mode & 0o777, mode)

    def test_existing_public_state_directory_is_rejected_without_chmod(self):
        for mode in (0o755, 0o777):
            with self.subTest(mode=oct(mode)):
                self.folder.mkdir(mode=0o700, exist_ok=True)
                self.folder.chmod(mode)
                with self.assertRaises(usage_start.UsageStartError):
                    usage_start.get_or_create_start(now=T0)
                self.assertEqual((self.folder.stat().st_mode & 0o777, list(self.folder.iterdir())), (mode, []))

    def test_existing_restrictive_modes_are_not_widened(self):
        if os.geteuid() == 0:
            self.skipTest('running with privileges that ignore file modes')
        self.folder.mkdir(mode=0o700)
        self.start.write_text(json.dumps({'schemaVersion': 1, 'since': T0.isoformat()}))
        self.start.chmod(0o400)
        self.folder.chmod(0o500)
        self.addCleanup(self.folder.chmod, 0o700)
        self.assertEqual(usage_start.get_or_create_start(now=T0 + timedelta(days=1)), T0)
        self.assertEqual((self.start.stat().st_mode & 0o777, self.folder.stat().st_mode & 0o777), (0o400, 0o500))

    def test_directory_replaced_during_read_is_not_followed(self):
        self.folder.mkdir(mode=0o700)
        self.start.write_text(json.dumps({'schemaVersion': 1, 'since': T0.isoformat()}))
        evil = self.root / 'evil'
        evil.mkdir(mode=0o700)
        (evil / 'usage-start.json').write_text(json.dumps({'schemaVersion': 1, 'since': '2001-01-01T00:00:00+00:00'}))
        real_open, swapped = os.open, []

        def swapping(path, *args, **kwargs):
            if kwargs.get('dir_fd') is not None and not swapped:  # the start is opened relative to the validated directory
                swapped.append(True)
                self.folder.rename(self.root / 'moved')
                self.folder.symlink_to(evil)
            return real_open(path, *args, **kwargs)
        with patch.object(usage_start.os, 'open', swapping):
            self.assertEqual(usage_start.get_or_create_start(now=T0 + timedelta(days=1)), T0)
        self.assertEqual(swapped, [True])

    def test_symlinked_start_is_not_followed(self):
        self.folder.mkdir()
        target = self.root / 'elsewhere.json'
        target.write_text('keep')
        for destination in (target, self.root / 'missing.json'):
            self.start.unlink(missing_ok=True)
            self.start.symlink_to(destination)
            with self.assertRaises(usage_start.UsageStartError):
                usage_start.get_or_create_start(now=T0)
            self.assertTrue(self.start.is_symlink())
        self.assertEqual(target.read_text(), 'keep')
        self.assertFalse((self.root / 'missing.json').exists())

    def test_a_directory_or_fifo_as_start_fails_without_blocking(self):
        self.folder.mkdir()
        self.start.mkdir()
        with self.assertRaises(usage_start.UsageStartError):
            usage_start.get_or_create_start(now=T0)
        self.start.rmdir()
        os.mkfifo(self.start)
        with self.assertRaises(usage_start.UsageStartError):
            usage_start.get_or_create_start(now=T0)

    def test_start_create_race_publishes_one_complete_value(self):
        results, barrier = [], threading.Barrier(6)

        def work(hours):
            barrier.wait()
            results.append(usage_start.get_or_create_start(now=T0 + timedelta(hours=hours)))
        threads = [threading.Thread(target=work, args=(hours,)) for hours in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(results), 6)
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(datetime.fromisoformat(json.loads(self.start.read_text())['since']), results[0])
        self.assertEqual([path.name for path in self.folder.iterdir()], ['usage-start.json'])

    def test_a_lost_race_returns_the_winner_without_replacing_it(self):
        real_link = os.link

        def losing_link(source, destination, *args, **kwargs):
            self.start.write_text(json.dumps({'schemaVersion': 1, 'since': '2026-01-01T00:00:00+00:00'}))
            return real_link(source, destination, *args, **kwargs)
        with patch.object(usage_start.os, 'link', losing_link):
            self.assertEqual(usage_start.get_or_create_start(now=T0), datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(json.loads(self.start.read_text())['since'], '2026-01-01T00:00:00+00:00')
        self.assertEqual([path.name for path in self.folder.iterdir()], ['usage-start.json'])

    def test_a_filesystem_without_exclusive_publishing_is_an_error_not_an_overwrite(self):
        with patch.object(usage_start.os, 'link', side_effect=PermissionError):
            with self.assertRaises(usage_start.UsageStartError):
                usage_start.get_or_create_start(now=T0)
        self.assertFalse(self.start.exists())
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_start_permissions_are_private(self):
        previous = os.umask(0)
        self.addCleanup(os.umask, previous)
        usage_start.get_or_create_start(now=T0)
        self.assertEqual(self.folder.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.start.stat().st_mode & 0o777, 0o600)

    def test_ledger_iteration_reads_the_whole_file_and_skips_bad_rows(self):
        self.rows(*[self.row('2026-10-01T00:00:00+00:00', tokens=i) for i in range(1, 30000)], 'oops')
        self.assertGreater(self.ledger.stat().st_size, 1024 * 1024)
        entries = list(usage_start.iter_ledger_entries(self.ledger))
        self.assertEqual((len(entries), entries[0].tokens, entries[-1].tokens), (29999, 1, 29999))
        self.assertEqual(entries[0].at.tzinfo, timezone.utc)

    def test_a_row_without_a_trailing_newline_still_counts_and_a_truncated_one_does_not(self):
        self.folder.mkdir()
        self.ledger.write_text(json.dumps(self.row('2026-10-01T00:00:00+00:00')) + '\n' + '{"at": "2026-10-02T00:00:00+00:00", "ro')
        self.assertEqual(len(list(usage_start.iter_ledger_entries(self.ledger))), 1)

    def read_ledger_without_blocking(self, path=None, directory=None):
        outcome = []

        def read():
            try:
                outcome.append(list(usage_start.iter_ledger_entries(path or self.ledger, **({} if directory is None else {'dir_fd': directory}))))
            except BaseException as error:  # the test inspects what was raised
                outcome.append(error)
        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        thread.join(5)
        self.assertFalse(thread.is_alive(), 'reading the ledger blocked')
        return outcome[0]

    def test_non_regular_ledger_is_an_error_without_blocking(self):
        import socket
        self.folder.mkdir()
        self.ledger.mkdir()
        self.assertIsInstance(self.read_ledger_without_blocking(), OSError)
        self.ledger.rmdir()
        os.mkfifo(self.ledger)
        self.assertIsInstance(self.read_ledger_without_blocking(), OSError)
        self.ledger.unlink()
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(self.ledger))
            self.assertIsInstance(self.read_ledger_without_blocking(), OSError)

    def test_ledger_replaced_before_open_is_checked_by_fstat(self):
        self.rows(self.row('2026-10-01T00:00:00+00:00'))
        real_open = os.open

        def swapped(path, *args, **kwargs):
            if Path(path) == self.ledger:
                self.ledger.unlink()
                os.mkfifo(self.ledger)  # swapped for a FIFO after the caller decided the file was fine
            return real_open(path, *args, **kwargs)
        with patch.object(usage_start.os, 'open', swapped):
            self.assertIsInstance(self.read_ledger_without_blocking(), OSError)

    def test_ledger_dir_fd_survives_parent_replacement(self):
        self.rows(self.row('2026-10-01T00:00:00+00:00', tokens=7))
        outside = self.root / 'outside'
        outside.mkdir(mode=0o700)
        (outside / 'role-usage.jsonl').write_text(json.dumps(self.row('2026-10-01T00:00:00+00:00', tokens=999)) + '\n')
        directory = usage_start._open_state_dir(self.folder, create=False)
        self.addCleanup(os.close, directory)
        self.folder.rename(self.root / 'moved')
        self.folder.symlink_to(outside)
        self.assertEqual([e.tokens for e in usage_start.iter_ledger_entries('role-usage.jsonl', dir_fd=directory)], [7])
        os.fstat(directory)  # the borrowed descriptor is still open: the iterator never closes it

    def test_ledger_dir_fd_missing_is_empty_and_unsafe_objects_are_errors_without_blocking(self):
        self.folder.mkdir()
        directory = usage_start._open_state_dir(self.folder, create=False)
        self.addCleanup(os.close, directory)
        self.assertEqual(list(usage_start.iter_ledger_entries('role-usage.jsonl', dir_fd=directory)), [])
        target = self.root / 'target'
        target.write_text('')
        self.ledger.symlink_to(target)
        self.assertIsInstance(self.read_ledger_without_blocking('role-usage.jsonl', directory), OSError)
        self.ledger.unlink()
        os.mkfifo(self.ledger)
        self.assertIsInstance(self.read_ledger_without_blocking('role-usage.jsonl', directory), OSError)
        self.ledger.unlink()
        self.ledger.write_text('{}')
        self.ledger.chmod(0)
        self.addCleanup(self.ledger.chmod, 0o600)
        if not os.access(self.ledger, os.R_OK):
            self.assertIsInstance(self.read_ledger_without_blocking('role-usage.jsonl', directory), PermissionError)

    def test_a_missing_ledger_is_empty_but_an_unreadable_one_is_an_error(self):
        self.assertEqual(list(usage_start.iter_ledger_entries(self.ledger)), [])
        self.folder.mkdir()
        self.ledger.write_text('{}')
        self.ledger.chmod(0)
        self.addCleanup(self.ledger.chmod, 0o600)
        if os.access(self.ledger, os.R_OK):
            self.skipTest('running with privileges that ignore file modes')
        with self.assertRaises(PermissionError):
            list(usage_start.iter_ledger_entries(self.ledger))


    @staticmethod
    def open_fds():
        return len(os.listdir('/proc/self/fd'))

    def drain(self, *args, **kwargs):
        with self.assertRaises(OSError) as caught:
            list(usage_start.iter_ledger_entries(*args, **kwargs))
        return caught.exception

    @unittest.skipUnless(os.path.isdir('/proc/self/fd'), 'needs /proc/self/fd')
    def test_directory_ledger_does_not_leak_fds(self):
        self.folder.mkdir()
        self.ledger.mkdir()
        directory = usage_start._open_state_dir(self.folder, create=False)
        self.addCleanup(os.close, directory)
        before = self.open_fds()
        for _ in range(20):
            self.drain(self.ledger)
        self.assertEqual(self.open_fds(), before)
        for _ in range(20):
            self.drain('role-usage.jsonl', dir_fd=directory)
        self.assertEqual(self.open_fds(), before)
        os.fstat(directory)

    @unittest.skipUnless(os.path.isdir('/proc/self/fd'), 'needs /proc/self/fd')
    def test_ledger_open_failures_do_not_leak_fds(self):
        self.rows(self.row('2026-10-01T00:00:00+00:00'))
        for name in ('fstat', 'fdopen'):
            with self.subTest(failing=name):
                synthetic = OSError(5, 'synthetic ' + name)
                before = self.open_fds()
                with patch.object(usage_start.os, name, side_effect=synthetic):
                    for _ in range(20):
                        self.assertIs(self.drain(self.ledger), synthetic)
                self.assertEqual(self.open_fds(), before)

    @unittest.skipUnless(os.path.isdir('/proc/self/fd'), 'needs /proc/self/fd')
    def test_closing_the_generator_closes_the_ledger_once(self):
        self.rows(self.row('2026-10-01T00:00:00+00:00'), self.row('2026-10-02T00:00:00+00:00'))
        before = self.open_fds()
        reader = usage_start.iter_ledger_entries(self.ledger)
        next(reader)
        self.assertEqual(self.open_fds(), before + 1)
        reader.close()
        self.assertEqual(self.open_fds(), before)
        self.assertEqual(len(list(usage_start.iter_ledger_entries(self.ledger))), 2)
        self.assertEqual(self.open_fds(), before)


if __name__ == '__main__':
    unittest.main()

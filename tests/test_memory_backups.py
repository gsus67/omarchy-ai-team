import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import memory_backups


class MemoryBackupTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        env=patch.dict(os.environ,{'HOME':str(self.root),'XDG_DATA_HOME':str(self.root/'data'),'XDG_CONFIG_HOME':str(self.root/'config')})
        env.start();self.addCleanup(env.stop)
        self.destination=self.root/'external drive';self.destination.mkdir()
        self.config={'foreign':'preserve'}
        self.runtime={'version':'2.2.3'}

    def test_missing_destination_never_creates_fallback_backup_or_deletes_last_good(self):
        memory_backups.configure(self.config,str(self.destination),24,7,True)
        marker=self.destination/memory_backups.MARKER
        marker.unlink()  # same mountpoint directory exists, volume marker disappeared
        with patch.object(memory_backups.memory_archive,'export') as export:
            with self.assertRaises(memory_backups.BackupError):memory_backups.run(self.config,self.runtime)
            export.assert_not_called()
        self.assertFalse(memory_backups.state().get('lastSuccess'))
        self.assertTrue(memory_backups.state()['lastError'])
        self.assertEqual(self.config['foreign'],'preserve')

    def test_explicit_directory_link_is_resolved_without_replacing_the_link(self):
        link=self.root/'backup-link';link.symlink_to(self.destination,target_is_directory=True)
        memory_backups.configure(self.config,str(link),24,7,False)
        self.assertEqual(self.config['backup']['destination'],str(self.destination))
        self.assertTrue(link.is_symlink())
        self.assertTrue((self.destination/memory_backups.MARKER).is_file())

    def test_scheduled_runs_skip_when_disabled_or_not_due_without_invoking_ecc(self):
        with patch.object(memory_backups.memory_archive,'export') as export:
            self.assertEqual(memory_backups.run(self.config,self.runtime,scheduled=True)['skipped'],'disabled')
            memory_backups.configure(self.config,str(self.destination),24,7,True)
            import time
            memory_backups.save_state({'lastSuccess':time.time()})
            self.assertEqual(memory_backups.run(self.config,self.runtime,scheduled=True)['skipped'],'not-due')
            export.assert_not_called()

    def test_failed_copy_keeps_success_state_and_does_not_run_retention(self):
        memory_backups.configure(self.config,str(self.destination),24,1,True)
        memory_backups.save_state({'lastSuccess':123,'lastArchive':'last-good.zip'})
        with patch.object(memory_backups.memory_archive,'export',side_effect=memory_backups.memory_archive.ArchiveError('fixture failure')), \
             patch.object(memory_backups,'prune') as prune:
            with self.assertRaises(memory_backups.BackupError):memory_backups.run(self.config,self.runtime)
            prune.assert_not_called()
        result=memory_backups.state()
        self.assertEqual((result['lastSuccess'],result['lastArchive']),(123,'last-good.zip'))
        self.assertIn('fixture failure',result['lastError'])

    def test_retention_removes_only_owned_verified_archives_after_a_new_good_copy(self):
        import memory_archive
        memory_backups.configure(self.config,str(self.destination),24,1,False)
        source=self.root/'vault';name='projects/'+'a'*64+'/project/notes/mem_test.md'
        file=source/name;file.parent.mkdir(parents=True);file.write_bytes(b'synthetic memory')
        old=self.destination/'ai-team-memory-20250101T000000Z-aaaaaaaaaaaa.zip'
        foreign=self.destination/'ai-team-memory-20250101T000000Z-bbbbbbbbbbbb.zip'
        new=self.destination/'ai-team-memory-20250101T000000Z-cccccccccccc.zip'
        with patch.object(memory_archive,'audit',return_value={'memoryCount':1}):
            memory_archive.export(source,old,self.runtime,target_id=self.config['backup']['targetId'])
            memory_archive.export(source,foreign,self.runtime)
            memory_archive.export(source,new,self.runtime,target_id=self.config['backup']['targetId'])
            removed=memory_backups.prune(self.destination,self.runtime,self.config['backup']['targetId'],1,new)
        self.assertEqual(removed,1)
        self.assertFalse(old.exists())
        self.assertTrue(foreign.exists() and new.exists())

    def test_automatic_backups_can_be_disabled_while_the_destination_is_unavailable(self):
        memory_backups.configure(self.config,str(self.destination),24,7,True)
        (self.destination/memory_backups.MARKER).unlink();self.destination.rmdir()
        memory_backups.configure(self.config,str(self.destination),24,7,False)
        self.assertFalse(self.config['backup']['enabled'])
        self.assertFalse(self.destination.exists())

    def test_scheduled_failure_notifies_once_per_reason_and_retention_needs_directory_sync(self):
        memory_backups.configure(self.config,str(self.destination),24,1,True)
        marker=self.destination/memory_backups.MARKER;original=marker.read_bytes();marker.unlink()
        with patch.object(memory_backups,'notify_failure') as notify:
            for _ in range(2):
                with self.assertRaises(memory_backups.BackupError):memory_backups.run(self.config,self.runtime,scheduled=True)
            self.assertEqual(notify.call_count,1)
        marker.write_bytes(original)
        result={'file':str(self.destination/'new.zip'),'sha256':'fixture','memoryCount':1,'directorySynced':False}
        with patch.object(memory_backups.memory_archive,'export',return_value=result),patch.object(memory_backups,'prune') as prune:
            reply=memory_backups.run(self.config,self.runtime)
            prune.assert_not_called()
        self.assertIn('warning',reply)
        self.assertTrue(memory_backups.state()['retentionError'])

    def test_timer_preserves_foreign_units_and_uses_quoted_native_exec_without_shell(self):
        memory_backups.configure(self.config,str(self.destination),24,7,True)
        folder=self.root/'config/systemd/user';folder.mkdir(parents=True)
        foreign=folder/(memory_backups.UNIT+'.service');foreign.write_text('Keep foreign unit.')
        with patch.object(memory_backups.subprocess,'run') as command:
            with self.assertRaises(memory_backups.BackupError):memory_backups.apply_timer(self.config,self.root/'harness.py')
            command.assert_not_called()
        self.assertEqual(foreign.read_text(),'Keep foreign unit.')
        self.assertFalse((folder/(memory_backups.UNIT+'.timer')).exists())
        foreign.unlink()
        with patch.object(memory_backups.subprocess,'run') as command:
            command.return_value.returncode=0
            memory_backups.apply_timer(self.config,self.root/'Plugin $ % with spaces/harness.py')
        service=foreign.read_text();timer=(folder/(memory_backups.UNIT+'.timer')).read_text()
        self.assertIn('memory-backup',service)
        self.assertIn('--scheduled',service)
        self.assertNotIn('/bin/sh',service)
        self.assertIn('Persistent=true',timer)
        self.assertIn('OnCalendar=hourly',timer)
        self.assertIn('%%',service)
        self.config['backup']['enabled']=False
        with patch.object(memory_backups.subprocess,'run') as command:
            command.return_value.returncode=0
            memory_backups.apply_timer(self.config,self.root/'harness.py')
        self.assertFalse(foreign.exists())
        self.assertTrue((self.destination/memory_backups.MARKER).exists())


if __name__=='__main__':unittest.main()

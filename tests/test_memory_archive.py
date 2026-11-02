import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'backend'))
import memory_archive


KEY = 'a'*64
NAME = 'projects/'+KEY+'/project/decisions/mem_test.md'


class MemoryArchiveTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.store = self.root/'source'; self.target = self.root/'restored'
        file = self.store/NAME; file.parent.mkdir(parents=True); file.write_bytes(b'synthetic memory data\n')
        self.archive = self.root/'backup.zip'
        p=patch.object(memory_archive,'audit',return_value={'memoryCount':1});p.start();self.addCleanup(p.stop)
        self.runtime={'version':'2.2.3'}

    def export(self):
        return memory_archive.export(self.store,self.archive,self.runtime)

    def set_project_names(self, names):
        with zipfile.ZipFile(self.archive) as z:
            entries={n:z.read(n) for n in z.namelist()}
        manifest=json.loads(entries['manifest.json'])
        manifest.pop('projectNames',None)
        if names is not None:manifest['projectNames']=names
        entries['manifest.json']=json.dumps(manifest)
        with zipfile.ZipFile(self.archive,'w') as z:
            for name,data in entries.items():z.writestr(name,data)

    def test_project_name_drops_separators_and_bidi_and_trailing_space(self):
        folder=self.store/'projects'/KEY
        folder.mkdir(parents=True,exist_ok=True)
        (folder/'.ai-team-project.json').write_text(json.dumps({'schemaVersion':1,'name':'A\u2028B\u2029C\u202aD\u2067E'+'x'*114+' z'}))
        name=memory_archive.project_name(self.store/'projects'/KEY)
        self.assertEqual(name,'ABCDE'+'x'*114)
        self.assertFalse(name.endswith(' '))
        self.assertEqual(memory_archive.clean_name('ab \u2066 '+' '*130+'c'),'ab')

    def test_export_includes_project_names_only_in_manifest(self):
        metadata=self.store/'projects'/KEY/'.ai-team-project.json'
        metadata.write_text(json.dumps({'schemaVersion':1,'name':'Mi proyecto'}))
        self.export()
        with zipfile.ZipFile(self.archive) as z:
            manifest=json.loads(z.read('manifest.json'))
            self.assertEqual(manifest.get('projectNames'),{KEY:'Mi proyecto'})
            self.assertEqual(set(manifest['files']),{NAME})
            self.assertEqual(set(z.namelist()),{'manifest.json',NAME})

    def test_restore_project_name_into_empty_store(self):
        self.export();self.set_project_names({KEY:'  Mi proyecto  '})
        preview=memory_archive.preview(self.archive,self.target,self.runtime)
        self.assertFalse(self.target.exists())
        memory_archive.restore(self.archive,self.target,self.runtime,preview['token'])
        self.assertEqual(memory_archive.inventory(self.target)[0]['label'],'Mi proyecto · aaaaaaaa')
        metadata=self.target/'projects'/KEY/'.ai-team-project.json'
        self.assertEqual(json.loads(metadata.read_text()),{'schemaVersion':1,'name':'Mi proyecto'})
        self.assertEqual(stat.S_IMODE(metadata.stat().st_mode),0o600)

    def test_restore_preserves_existing_project_metadata(self):
        self.export();self.set_project_names({KEY:'Archive name'})
        preview=memory_archive.preview(self.archive,self.target,self.runtime)
        metadata=self.target/'projects'/KEY/'.ai-team-project.json'
        metadata.parent.mkdir(parents=True)
        original=b'{"schemaVersion":1,"name":"Existing name","extra":true}'
        metadata.write_bytes(original)
        memory_archive.restore(self.archive,self.target,self.runtime,preview['token'])
        self.assertEqual(metadata.read_bytes(),original)

    def test_old_archive_restores_without_project_metadata(self):
        self.export();self.set_project_names(None)
        preview=memory_archive.preview(self.archive,self.target,self.runtime)
        memory_archive.restore(self.archive,self.target,self.runtime,preview['token'])
        self.assertEqual((self.target/NAME).read_bytes(),b'synthetic memory data\n')
        self.assertFalse((self.target/'projects'/KEY/'.ai-team-project.json').exists())
        self.assertEqual(memory_archive.inventory(self.target)[0]['label'],'Proyecto aaaaaaaaaa')

    def test_restore_ignores_invalid_project_names(self):
        self.export()
        for index,name in enumerate(('bad\nname','bad\x00name','bad\x7fname','bad\x85name',42,None,{},' '*3,'x'*121)):
            with self.subTest(name=name):
                self.set_project_names({KEY:name})
                target=self.root/('invalid-'+str(index))
                preview=memory_archive.preview(self.archive,target,self.runtime)
                memory_archive.restore(self.archive,target,self.runtime,preview['token'])
                self.assertTrue((target/NAME).is_file())
                self.assertFalse((target/'projects'/KEY/'.ai-team-project.json').exists())

    def test_project_name_read_from_disk_is_bounded_and_without_control_characters(self):
        directory=self.root/'names';directory.mkdir()
        for raw,expected in (('Mi proyecto ñ','Mi proyecto ñ'),('  ma\x00l\nvado\x7f\x85  ','malvado'),
                             ('x'*500,'x'*120),('\x00\n ',None)):
            with self.subTest(raw=raw):
                (directory/'.ai-team-project.json').write_text(json.dumps({'schemaVersion':1,'name':raw}))
                self.assertEqual(memory_archive.project_name(directory),expected)

    def test_project_names_follow_reassociation(self):
        self.export();self.set_project_names({KEY:'Migrated project','../outside':'Bad'})
        target_key='b'*64
        preview=memory_archive.preview(self.archive,self.target,self.runtime,target_key)
        memory_archive.restore(self.archive,self.target,self.runtime,preview['token'],target_key)
        self.assertEqual(memory_archive.inventory(self.target)[0]['label'],'Migrated project · bbbbbbbb')
        self.assertFalse((self.target/'projects'/KEY).exists())

    def two_projects(self):
        other='b'*64
        file=self.store/'projects'/other/'project/decisions/mem_other.md'
        file.parent.mkdir(parents=True);file.write_bytes(b'other project data\n')
        memory_archive.audit.return_value={'memoryCount':2}
        self.export();self.set_project_names({KEY:'app',other:'app'})
        return other

    def test_several_projects_can_move_to_new_folders(self):
        other=self.two_projects()
        moved={KEY:'c'*64,other:'d'*64}
        preview=memory_archive.preview(self.archive,self.target,self.runtime,key_map=moved)
        self.assertEqual(preview['projectCount'],2)
        self.assertEqual({row['key']:row['newKey'] for row in preview['projects']},moved)
        self.assertFalse(self.target.exists())
        result=memory_archive.restore(self.archive,self.target,self.runtime,preview['token'],key_map=moved)
        self.assertEqual(result['restoredCount'],2)
        self.assertEqual(result['unverifiedProjects'],[])
        self.assertEqual((self.target/'projects'/('c'*64)/'project/decisions/mem_test.md').read_bytes(),b'synthetic memory data\n')
        self.assertEqual((self.target/'projects'/('d'*64)/'project/decisions/mem_other.md').read_bytes(),b'other project data\n')
        self.assertFalse((self.target/'projects'/KEY).exists())
        self.assertEqual(json.loads((self.target/'projects'/('c'*64)/'.ai-team-project.json').read_text())['name'],'app')

    def test_ambiguous_or_invalid_destinations_are_rejected(self):
        other=self.two_projects()
        shared=memory_archive.SHARED_KEY
        for moved in ({KEY:'c'*64,other:'c'*64},   # Two projects into one folder.
                      {KEY:other},                 # Into a project that stays in the archive.
                      {'e'*64:'c'*64},             # A project this archive does not hold.
                      {KEY:'not-a-key'},
                      {KEY:shared}):
            with self.subTest(moved=moved),self.assertRaises(memory_archive.ArchiveError):
                memory_archive.preview(self.archive,self.target,self.runtime,key_map=moved)
        self.assertFalse(self.target.exists())

    def test_single_target_and_map_cannot_be_combined(self):
        self.export()
        with self.assertRaises(memory_archive.ArchiveError):
            memory_archive.preview(self.archive,self.target,self.runtime,'c'*64,key_map={KEY:'d'*64})

    def test_shared_memory_keeps_its_identity(self):
        shared=memory_archive.SHARED_KEY
        file=self.store/'projects'/shared/'project/decisions/mem_shared.md'
        file.parent.mkdir(parents=True);file.write_bytes(b'common rules\n')
        memory_archive.audit.return_value={'memoryCount':2}
        self.export()
        with self.assertRaises(memory_archive.ArchiveError):
            memory_archive.preview(self.archive,self.target,self.runtime,key_map={shared:'c'*64})
        preview=memory_archive.preview(self.archive,self.target,self.runtime)
        self.assertEqual({row['key']:row['shared'] for row in preview['projects']},{KEY:False,shared:True})

    def test_shared_key_matches_the_memory_bridge(self):
        self.assertEqual(memory_archive.SHARED_KEY,hashlib.sha256(b'ai-team-shared-memory').hexdigest())
        bridge=(Path(memory_archive.__file__).with_name('memory_projects.mjs')).read_text()
        self.assertIn("storeFor('ai-team-shared-memory')",bridge)

    def test_conflict_at_the_new_location_blocks_and_keeps_existing_memory(self):
        self.export()
        existing=self.target/'projects'/('c'*64)/'project/decisions/mem_test.md'
        existing.parent.mkdir(parents=True);existing.write_bytes(b'already here\n')
        preview=memory_archive.preview(self.archive,self.target,self.runtime,key_map={KEY:'c'*64})
        self.assertEqual(preview['conflictCount'],1)
        with self.assertRaises(memory_archive.ArchiveError):
            memory_archive.restore(self.archive,self.target,self.runtime,preview['token'],key_map={KEY:'c'*64})
        self.assertEqual(existing.read_bytes(),b'already here\n')

    def test_token_is_bound_to_the_map_and_to_later_changes(self):
        self.export()
        preview=memory_archive.preview(self.archive,self.target,self.runtime,key_map={KEY:'c'*64})
        with self.assertRaises(memory_archive.ArchiveError):
            memory_archive.restore(self.archive,self.target,self.runtime,preview['token'],key_map={KEY:'d'*64})
        with self.assertRaises(memory_archive.ArchiveError):
            memory_archive.restore(self.archive,self.target,self.runtime,preview['token'])
        other=self.target/'projects'/('c'*64)/'project/decisions/mem_late.md'
        other.parent.mkdir(parents=True);other.write_bytes(b'written after the preview\n')
        # Only the token can stop this one: the late document is valid and conflicts with nothing.
        with patch.object(memory_archive,'validate_documents'),self.assertRaisesRegex(memory_archive.ArchiveError,'plan cambió'):
            memory_archive.restore(self.archive,self.target,self.runtime,preview['token'],key_map={KEY:'c'*64})
        self.assertFalse((self.target/'projects'/('c'*64)/'project/decisions/mem_test.md').exists())

    def test_without_a_map_restored_projects_are_reported_as_not_verified_here(self):
        self.export();self.set_project_names({KEY:'app'})
        preview=memory_archive.preview(self.archive,self.target,self.runtime)
        result=memory_archive.restore(self.archive,self.target,self.runtime,preview['token'])
        self.assertEqual(result['unverifiedProjects'],['app'])

    def test_project_name_change_invalidates_archive_token(self):
        self.export();self.set_project_names({KEY:'Before'})
        preview=memory_archive.preview(self.archive,self.target,self.runtime)
        self.set_project_names({KEY:'After'})
        with self.assertRaises(memory_archive.ArchiveError):
            memory_archive.restore(self.archive,self.target,self.runtime,preview['token'])
        self.assertFalse(self.target.exists())

    def test_portable_roundtrip_preserves_documents_and_excludes_other_files(self):
        (self.store/'credentials.json').write_text('Do not export')
        self.export()
        with zipfile.ZipFile(self.archive) as z:
            self.assertEqual(set(z.namelist()), {'manifest.json',NAME})
        preview=memory_archive.preview(self.archive,self.target,self.runtime)
        self.assertEqual((preview['newCount'],preview['conflictCount']),(1,0))
        self.assertFalse(self.target.exists())
        memory_archive.restore(self.archive,self.target,self.runtime,preview['token'])
        self.assertEqual((self.target/NAME).read_bytes(),(self.store/NAME).read_bytes())
        preview=memory_archive.preview(self.archive,self.target,self.runtime)
        self.assertEqual((preview['newCount'],preview['identicalCount']),(0,1))

    def test_conflicts_and_changed_preview_never_overwrite_existing_memory(self):
        self.export();preview=memory_archive.preview(self.archive,self.target,self.runtime)
        file=self.target/NAME;file.parent.mkdir(parents=True);file.write_bytes(b'Keep existing memory')
        with self.assertRaises(memory_archive.ArchiveError):
            memory_archive.restore(self.archive,self.target,self.runtime,preview['token'])
        preview=memory_archive.preview(self.archive,self.target,self.runtime)
        self.assertEqual(preview['conflictCount'],1)
        with self.assertRaises(memory_archive.ArchiveError):
            memory_archive.restore(self.archive,self.target,self.runtime,preview['token'])
        self.assertEqual(file.read_bytes(),b'Keep existing memory')

    def test_source_symlink_is_rejected_without_reading_or_exporting_its_target(self):
        file=self.store/NAME;file.unlink();foreign=self.root/'foreign';foreign.write_text('private')
        file.symlink_to(foreign)
        with self.assertRaises(memory_archive.ArchiveError):self.export()
        self.assertFalse(self.archive.exists())
        self.assertEqual(foreign.read_text(),'private')

    def test_archive_traversal_duplicate_symlink_and_checksum_corruption_are_rejected(self):
        self.export()
        with zipfile.ZipFile(self.archive) as z:original={n:z.read(n) for n in z.namelist()}
        attacks=[('../outside',b'bad',None),(NAME,b'corrupt',None),(NAME,original[NAME],stat.S_IFLNK|0o777)]
        for name,body,mode in attacks:
            with self.subTest(name=name,mode=mode):
                with zipfile.ZipFile(self.archive,'w') as z:
                    for n,b in original.items():
                        if n!=name:z.writestr(n,b)
                    i=zipfile.ZipInfo(name)
                    if mode:i.external_attr=mode<<16
                    z.writestr(i,body)
                with self.assertRaises(memory_archive.ArchiveError):memory_archive.preview(self.archive,self.target,self.runtime)
                self.assertFalse(self.target.exists())
        with zipfile.ZipFile(self.archive,'w') as z:
            for n,b in original.items():z.writestr(n,b)
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter('ignore');z.writestr(NAME,original[NAME])
        with self.assertRaises(memory_archive.ArchiveError):memory_archive.preview(self.archive,self.target,self.runtime)

    def test_invalid_official_audit_or_source_change_does_not_replace_an_existing_backup(self):
        self.export();before=self.archive.read_bytes()
        with patch.object(memory_archive,'audit',side_effect=memory_archive.ArchiveError('invalid ECC memory')):
            with self.assertRaises(memory_archive.ArchiveError):memory_archive.export(self.store,self.root/'invalid.zip',self.runtime)
        self.assertFalse((self.root/'invalid.zip').exists())
        self.assertEqual(self.archive.read_bytes(),before)
        def mutate(*args):
            (self.store/NAME).write_bytes(b'concurrent change')
            return {'memoryCount':1}
        other=self.root/'another.zip'
        with patch.object(memory_archive,'audit',side_effect=mutate):
            with self.assertRaises(memory_archive.ArchiveError):memory_archive.export(self.store,other,self.runtime)
        self.assertFalse(other.exists())
        self.assertEqual(self.archive.read_bytes(),before)

    def test_restore_failure_rolls_back_only_unchanged_documents_created_by_this_operation(self):
        second=self.store/NAME.replace('mem_test','mem_second');second.write_bytes(b'second synthetic document')
        def count(directory,runtime):return {'memoryCount':len(list((directory/'projects').rglob('*.md')))}
        with patch.object(memory_archive,'audit',side_effect=count):
            self.export();preview=memory_archive.preview(self.archive,self.target,self.runtime)
            original_link=os.link;calls=[]
            def fail_second(source,destination):
                if calls:
                    calls[0].write_bytes(b'Concurrent writer changed this document')
                    raise OSError('synthetic full disk')
                original_link(source,destination);calls.append(Path(destination))
            with patch.object(memory_archive.os,'link',side_effect=fail_second):
                with self.assertRaises(memory_archive.ArchiveError):
                    memory_archive.restore(self.archive,self.target,self.runtime,preview['token'])
        self.assertEqual(calls[0].read_bytes(),b'Concurrent writer changed this document')
        self.assertEqual(len(list(self.target.rglob('*.md'))),1)
        self.assertFalse(list(self.target.rglob('.ai-team-restore-*')))

    def test_later_safety_failure_removes_unchanged_new_documents(self):
        (self.store/NAME.replace('mem_test','mem_second')).write_bytes(b'second document')
        def count(directory,runtime):return {'memoryCount':len(list((directory/'projects').rglob('*.md')))}
        with patch.object(memory_archive,'audit',side_effect=count):
            self.export();preview=memory_archive.preview(self.archive,self.target,self.runtime)
            link=os.link;calls=[]
            def fail_later(source,destination):
                if calls:raise memory_archive.ArchiveError('later destination safety check failed')
                link(source,destination);calls.append(destination)
            with patch.object(memory_archive.os,'link',side_effect=fail_later):
                with self.assertRaises(memory_archive.ArchiveError):memory_archive.restore(self.archive,self.target,self.runtime,preview['token'])
        self.assertEqual(list(self.target.rglob('*.md')),[])

    def test_destination_symlink_prevents_restore_outside_the_store(self):
        self.export();preview=memory_archive.preview(self.archive,self.target,self.runtime)
        foreign=self.root/'foreign';foreign.mkdir();self.target.symlink_to(foreign,target_is_directory=True)
        with self.assertRaises(memory_archive.ArchiveError):
            memory_archive.restore(self.archive,self.target,self.runtime,preview['token'])
        self.assertEqual(list(foreign.iterdir()),[])


if __name__=='__main__':unittest.main()

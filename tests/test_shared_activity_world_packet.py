"""Offline packet validation only. No Java, Minecraft server or game connection."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
import zipfile

import importlib.util
_spec = importlib.util.spec_from_file_location('shared_minecraft_world_packet', Path(__file__).resolve().parents[1] / 'scripts/verify_shared_minecraft_world.py')
packet = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(packet)


class PacketChecks(unittest.TestCase):
    def test_frozen_variants_and_bounded_commands(self):
        rows = packet.scenarios()
        self.assertEqual(len({row['variant_sha256'] for row in rows}), 6)
        self.assertEqual([row['family'] for row in rows if row['partition'] == 'baseline'],
                         [row['family'] for row in rows if row['partition'] == 'held_out'])
        for row in rows:
            for command in packet.command_plan(row):
                self.assertIn(command.split()[0], {'fill', 'tp', 'setworldspawn'})
                self.assertLess(len(command), 120)
        self.assertFalse(packet.packet()['downloads_performed'])

    def artifacts(self, root, version='1.21.11'):
        java, jar = root / 'java', root / 'server.jar'
        java.write_bytes(b'NOT EXECUTED fixture executable bytes')
        with zipfile.ZipFile(jar, 'w') as archive:
            archive.writestr('META-INF/versions.list', 'a'*64+'\t'+version+'\tserver.jar\n')
        approval = root / 'approval.json'
        approval.write_text(json.dumps({'temporary_test_owner_approved': True,
            'minecraft_eula_owner_accepted': True, 'java_runtime_use_owner_approved': True,
            'minecraft_version': '1.21.11', 'maximum_seconds': 600,
            'server_jar_sha256': packet.sha(jar), 'java_executable_sha256': packet.sha(java)}))
        return java, jar, approval

    def test_exact_receipt_and_artifact_validation_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            java, jar, approval = self.artifacts(root)
            self.assertTrue(packet.validate_approval(approval, jar, java)['minecraft_eula_owner_accepted'])
            data = json.loads(approval.read_text()); data['minecraft_eula_owner_accepted'] = False
            approval.write_text(json.dumps(data))
            with self.assertRaises(ValueError): packet.validate_approval(approval, jar, java)
            java, jar, approval = self.artifacts(root, '1.21.10')
            with self.assertRaises(ValueError): packet.validate_approval(approval, jar, java)
            java, jar, approval = self.artifacts(root)
            jar.write_bytes(b'changed')
            with self.assertRaises(ValueError): packet.validate_approval(approval, jar, java)

    def valid_probe(self):
        scene = packet.scenarios()[0]
        layout = scene['layout']
        cells = [{'x': x, 'y': y, 'z': 0, 'name': 'stone' if y == 63 else 'air'}
                 for x in range(0, 8) for y in (63, 64, 65)]
        return scene, {'ok': True, 'view': {'scenario_id': scene['id'], 'automated_owner': True,
            'evidence_kind': 'independent_owner_client_server_state', 'at': time.time(),
            'positions': {'owner': layout['owner'], 'companion': layout['start']}, 'cells': cells}}

    def test_independent_probe_requires_complete_fresh_geometry(self):
        scene, value = self.valid_probe()
        self.assertEqual(packet.validate_probe(scene, value, require_start=True)['scenario_id'], scene['id'])
        for mode in ('empty', 'duplicate', 'stale', 'wrong_block'):
            row = copy.deepcopy(value)
            if mode == 'empty': row['view']['cells'] = []
            elif mode == 'duplicate': row['view']['cells'].append(row['view']['cells'][0])
            elif mode == 'stale': row['view']['at'] -= 3
            else: row['view']['cells'][0]['name'] = 'lava'
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                packet.validate_probe(scene, row, require_start=True)

    @unittest.skipUnless(os.name == 'posix', 'Gamma POSIX launcher check')
    def test_actual_child_limits_and_targeted_shutdown(self):
        code = "import os,resource,json,sys;print(json.dumps({'affinity':len(os.sched_getaffinity(0)),'nice':os.getpriority(os.PRIO_PROCESS,0),'cpu':resource.getrlimit(resource.RLIMIT_CPU),'file':resource.getrlimit(resource.RLIMIT_FSIZE)}),flush=True);sys.stdin.readline()"
        with tempfile.TemporaryDirectory() as directory:
            child = packet.Child([sys.executable, '-u', '-c', code], cwd=directory)
            try:
                value = json.loads(child.lines.get(timeout=5))
                self.assertLessEqual(value['affinity'], 2)
                self.assertGreaterEqual(value['nice'], 10)
                self.assertEqual(value['cpu'], [540, 550])
                self.assertEqual(value['file'], [268435456, 268435456])
                self.assertIsNone(child.process.poll())
            finally:
                child.stop('stop')
            self.assertIsNotNone(child.process.poll())

    @unittest.skipUnless(os.name == 'posix', 'Gamma POSIX cleanup check')
    def test_cleanup_never_follows_dependency_reference_or_other_directory(self):
        with tempfile.TemporaryDirectory() as protected:
            target = Path(protected) / 'keep.txt'
            target.write_text('unrelated dependency fixture')
            root = Path(tempfile.mkdtemp(prefix='shana-p6-minecraft-', dir='/tmp'))
            (root / 'node_modules').symlink_to(protected, target_is_directory=True)
            self.assertTrue(packet.cleanup_temp(root))
            self.assertEqual(target.read_text(), 'unrelated dependency fixture')
            with self.assertRaises(ValueError): packet.cleanup_temp(Path(protected))


if __name__ == '__main__': unittest.main()

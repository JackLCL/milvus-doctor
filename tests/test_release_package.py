"""Release builder tests use temporary Git repositories, never live deployments."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile


SCRIPT = Path(__file__).resolve().parents[1]/'scripts/build_release.py'
SPEC = importlib.util.spec_from_file_location('release_builder_under_test', SCRIPT)
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class ReleasePackageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='doctor-release-test-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.repo = self.root/'repo'
        self.repo.mkdir()
        self.git('init', '-q')
        for name in builder.REQUIRED_FILES:
            self.write(name, 'committed fixture\n')
        self.write('scripts/doctorlib/__init__.py', '__version__ = "0.0.1"\n')
        self.write('scripts/doctorlib/local_files.py', 'VALUE = "tracked new module"\n')
        self.commit()

    def git(self, *args):
        env = os.environ.copy()
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_AUTHOR_DATE='2026-09-13T12:00:00+00:00', GIT_COMMITTER_DATE='2026-09-13T12:00:00+00:00')
        return subprocess.check_output(['git','-C',str(self.repo),'-c','user.name=Release Test',
            '-c','user.email=release-test@example.invalid','-c','core.hooksPath=/dev/null',
            '-c','commit.gpgSign=false'] + list(args), env=env, stderr=subprocess.STDOUT).decode().strip()

    def write(self, name, content):
        path = self.repo/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'Isolated release fixture')
        return self.git('rev-parse', 'HEAD')

    def build(self, name='package', ref='HEAD'):
        return builder.build(self.repo, ref, self.root/name)

    def test_version_and_content_come_from_commit_not_dirty_worktree(self):
        expected_commit = self.git('rev-parse', 'HEAD')
        self.write('scripts/doctorlib/__init__.py', '__version__ = "9.9.9"\n')
        self.write('scripts/doctorlib/local_files.py', 'dirty changed content\n')
        self.write('scripts/not_committed.py', 'untracked\n')
        result = self.build()
        self.assertEqual(result['version'], '0.0.1')
        self.assertEqual(result['git_commit'], expected_commit)
        with zipfile.ZipFile(result['archive']) as archive:
            self.assertEqual(archive.read('milvus-doctor/scripts/doctorlib/local_files.py'), b'VALUE = "tracked new module"\n')
            self.assertNotIn('milvus-doctor/scripts/not_committed.py', archive.namelist())
            self.assertEqual(json.loads(archive.read('milvus-doctor/RELEASE.json'))['git_commit'], expected_commit)
        self.assertIn('9.9.9', (self.repo/'scripts/doctorlib/__init__.py').read_text())

    def test_curated_allowlist_excludes_tracked_developer_and_private_paths(self):
        excluded = ['tests/lab/create.py', 'docs/internal.md', 'reports/run.json',
            'scripts/build_release.py', 'scripts/.venv/runtime.py', 'scripts/api-token.txt',
            'references/secrets.json', 'scripts/auth.json', 'scripts/.env',
            'references/raw.kubeconfig', 'scripts/__pycache__/x.pyc', 'private/credential.txt']
        for path in excluded:
            self.write(path, 'must not be distributed\n')
        self.write('LICENSE', 'Maintainer-selected fixture license text\n')
        self.commit()
        result = self.build()
        with zipfile.ZipFile(result['archive']) as archive:
            names = archive.namelist()
            self.assertTrue(all('milvus-doctor/'+path not in names for path in excluded))
            self.assertIn('milvus-doctor/LICENSE', names)
        self.assertEqual(result['warnings'], [])

    def test_absent_license_warns_without_inventing_one(self):
        result = self.build()
        self.assertEqual(len(result['warnings']), 1)
        manifest = json.loads(Path(result['manifest']).read_text())
        self.assertEqual(manifest['license_files'], [])
        self.assertIn('No LICENSE', result['warnings'][0])

    def test_committed_project_license_bytes_and_manifest_are_preserved(self):
        license_bytes = (SCRIPT.parents[1] / 'LICENSE').read_bytes()
        self.write('LICENSE', license_bytes.decode('utf-8'))
        self.commit()
        result = self.build()
        manifest = json.loads(Path(result['manifest']).read_text())
        self.assertEqual(result['warnings'], [])
        self.assertEqual(manifest['license_files'], ['LICENSE'])
        license_entry = next(item for item in manifest['files'] if item['path'] == 'LICENSE')
        self.assertEqual(license_entry['sha256'], hashlib.sha256(license_bytes).hexdigest())
        with zipfile.ZipFile(result['archive']) as archive:
            self.assertEqual(archive.read('milvus-doctor/LICENSE'), license_bytes)

    def test_checksums_and_source_file_manifest_match_actual_bytes(self):
        result = self.build()
        output = Path(result['output_dir'])
        for line in (output/'SHA256SUMS').read_text().splitlines():
            expected, name = line.split('  ', 1)
            self.assertEqual(hashlib.sha256((output/name).read_bytes()).hexdigest(), expected)
        manifest = json.loads(Path(result['manifest']).read_text())
        with zipfile.ZipFile(result['archive']) as archive:
            for entry in manifest['files']:
                content = archive.read('milvus-doctor/'+entry['path'])
                self.assertEqual(len(content), entry['bytes'])
                self.assertEqual(hashlib.sha256(content).hexdigest(), entry['sha256'])

    def test_same_commit_builds_identical_archive(self):
        first = self.build('first')
        second = self.build('second')
        self.assertEqual(Path(first['archive']).read_bytes(), Path(second['archive']).read_bytes())
        self.assertEqual(Path(first['manifest']).read_bytes(), Path(second['manifest']).read_bytes())

    def test_existing_directory_and_dangling_symlink_are_never_replaced(self):
        output = self.root/'existing'
        output.mkdir()
        marker = output/'preserve.txt'
        marker.write_text('keep')
        with self.assertRaises(builder.BuildError):
            self.build('existing')
        self.assertEqual(marker.read_text(), 'keep')
        (self.root/'link').symlink_to(self.root/'does-not-exist', target_is_directory=True)
        with self.assertRaises(builder.BuildError):
            self.build('link')
        self.assertTrue((self.root/'link').is_symlink())
        self.assertFalse((self.root/'does-not-exist').exists())

    def test_symlink_inside_allowed_tree_is_rejected(self):
        (self.repo/'scripts/link.py').symlink_to('../README.md')
        self.commit()
        with self.assertRaisesRegex(builder.BuildError, 'regular tracked files'):
            self.build()
        self.assertFalse((self.root/'package').exists())

    def test_invalid_ref_and_missing_committed_file_leave_no_output(self):
        for ref in ('--help', 'HEAD --other', 'not-a-real-ref'):
            with self.assertRaises(builder.BuildError):
                self.build(ref=ref)
        (self.repo/'SKILL.md').unlink()
        self.commit()
        with self.assertRaisesRegex(builder.BuildError, 'required Skill files'):
            self.build()
        self.assertFalse((self.root/'package').exists())

    def test_version_is_parsed_without_executing_source(self):
        marker = self.root/'must-not-exist'
        self.write('scripts/doctorlib/__init__.py', '__version__ = "0.0.1"\nopen({!r}, "w").write("bad")\n'.format(str(marker)))
        self.commit()
        self.assertEqual(self.build()['version'], '0.0.1')
        self.assertFalse(marker.exists())

    def test_nonliteral_or_path_like_version_is_rejected(self):
        for expression in ('"../bad"', 'str("0.0.1")'):
            self.write('scripts/doctorlib/__init__.py', '__version__ = '+expression+'\n')
            self.commit()
            with self.assertRaisesRegex(builder.BuildError, 'literal semantic'):
                self.build()
        self.assertFalse((self.root/'package').exists())

    def test_explicit_old_commit_remains_selected_after_new_commit(self):
        old = self.git('rev-parse', 'HEAD')
        self.write('scripts/doctorlib/__init__.py', '__version__ = "0.0.2"\n')
        self.commit()
        self.assertEqual(self.build(ref=old)['version'], '0.0.1')


if __name__ == '__main__':
    unittest.main()

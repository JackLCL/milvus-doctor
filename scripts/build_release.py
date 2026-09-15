#!/usr/bin/env python3
"""Build a curated Skill ZIP from one committed Git ref; never publish it.

This maintainer tool is excluded from its own runtime package. It reads Git blobs,
not working-tree files, and refuses an existing output directory.
"""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import zipfile


ROOT_FILES = {'SKILL.md', 'AI-INSTALL.md', 'README.md', 'CHANGELOG.md', 'requirements-optional.txt'}
LICENSE_FILES = {'LICENSE', 'LICENSE.md', 'LICENSE.txt'}
RUNTIME_TREES = {'agents', 'scripts', 'references'}
REQUIRED_FILES = ROOT_FILES | {
    'agents/openai.yaml', 'scripts/doctor.py', 'scripts/doctorlib/__init__.py',
    'references/deployments.md', 'references/access.md', 'references/handoff.md',
}
BLOCKED_PARTS = {
    '.git', '.venv', 'venv', '__pycache__', 'node_modules', '.cache', 'cache',
    'private', '.private', 'internal', 'reports', 'evidence', 'tests', 'docs',
    '.ssh', '.kube', '.aws', '.azure', '.codex', '.config',
}
BLOCKED_NAMES = {'auth.json', 'credentials', 'credentials.json', 'credentials.yaml',
                 'credentials.yml', 'id_rsa', 'id_ed25519', '.env'}
BLOCKED_SUFFIXES = {'.pyc', '.pyo', '.pem', '.key', '.p12', '.pfx', '.kubeconfig',
                    '.zip', '.tar', '.tgz', '.log'}
VERSION_PATTERN = re.compile(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?\Z')
MAX_FILES = 2000
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024


class BuildError(Exception):
    pass


def git(repo, *args):
    env = os.environ.copy()
    env['GIT_OPTIONAL_LOCKS'] = '0'
    result = subprocess.run(['git', '-C', str(repo)] + list(args), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env, timeout=60)
    if result.returncode:
        detail = result.stderr.decode('utf-8', errors='replace').strip()
        raise BuildError('Git read failed: ' + detail)
    return result.stdout


def resolve_commit(repo, ref):
    if not ref or ref.startswith('-') or len(ref) > 240 or any(character.isspace() or ord(character) < 32 for character in ref):
        raise BuildError('Provide a non-option, whitespace-free committed Git ref.')
    commit = git(repo, 'rev-parse', '--verify', ref + '^{commit}').decode('ascii').strip()
    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', commit):
        raise BuildError('Git did not resolve the ref to a full commit object ID.')
    return commit


def selected(path):
    name = PurePosixPath(path)
    if name.is_absolute() or '..' in name.parts or '\\' in path or any(ord(char) < 32 for char in path):
        raise BuildError('Unsafe tracked path; refusing to package it.')
    if path in {'scripts/build_release.py', 'scripts/sync_faq.py'}:
        return False
    if path not in ROOT_FILES | LICENSE_FILES and (len(name.parts) < 2 or name.parts[0] not in RUNTIME_TREES):
        return False
    lower_parts = [part.lower() for part in name.parts]
    if any(part in BLOCKED_PARTS or 'token' in part or 'secret' in part or 'kubeconfig' in part for part in lower_parts):
        return False
    if name.name.lower() in BLOCKED_NAMES or name.name.lower().startswith('.env.') or name.suffix.lower() in BLOCKED_SUFFIXES:
        return False
    return True


def tracked_files(repo, commit):
    result = []
    for entry in git(repo, 'ls-tree', '-r', '-z', commit).split(b'\0'):
        if not entry:
            continue
        try:
            header, raw_path = entry.split(b'\t', 1)
            mode, kind, object_id = header.decode('ascii').split()
            path = raw_path.decode('utf-8')
        except (ValueError, UnicodeError):
            raise BuildError('Unsupported Git tree entry encoding.')
        if not selected(path):
            continue
        if kind != 'blob' or mode not in {'100644', '100755'}:
            raise BuildError('Allowed paths must be regular tracked files, not links/submodules: ' + path)
        if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', object_id):
            raise BuildError('Invalid Git blob object ID.')
        result.append((path, mode, object_id))
    missing = REQUIRED_FILES - {path for path, _, _ in result}
    if missing:
        raise BuildError('The committed ref lacks required Skill files: ' + ', '.join(sorted(missing)))
    if len(result) > MAX_FILES:
        raise BuildError('Selected tree exceeds the package file-count limit.')
    files = []
    total = 0
    for path, mode, object_id in sorted(result):
        size = int(git(repo, 'cat-file', '-s', object_id).strip())
        total += size
        if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise BuildError('Selected committed content exceeds the package size limit.')
        content = git(repo, 'cat-file', 'blob', object_id)
        if len(content) != size:
            raise BuildError('Git blob size mismatch: ' + path)
        files.append({'path': path, 'mode': mode, 'git_blob': object_id,
                      'content': content, 'bytes': size,
                      'sha256': hashlib.sha256(content).hexdigest()})
    return files


def version_from_source(content):
    try:
        tree = ast.parse(content.decode('utf-8'))
        values = []
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == '__version__' for target in node.targets):
                values.append(ast.literal_eval(node.value))
        if len(values) != 1 or not isinstance(values[0], str) or not VERSION_PATTERN.fullmatch(values[0]):
            raise ValueError('invalid version assignment')
        return values[0]
    except (UnicodeError, SyntaxError, ValueError, TypeError):
        raise BuildError('Committed __init__.py must contain one literal semantic __version__ string.')


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def zip_timestamp(commit_timestamp):
    value = min(max(int(commit_timestamp), 315532800), 4354819198)
    date = datetime.fromtimestamp(value, timezone.utc)
    return (date.year, date.month, date.day, date.hour, date.minute, date.second // 2 * 2)


def create_zip(files, release_info, timestamp):
    data = io.BytesIO()
    entries = [(item['path'], item['mode'], item['content']) for item in files]
    entries.append(('RELEASE.json', '100644', json_bytes(release_info)))
    with zipfile.ZipFile(data, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, mode, content in sorted(entries):
            item = zipfile.ZipInfo('milvus-doctor/' + path, date_time=timestamp)
            item.create_system = 3
            item.external_attr = (int(mode, 8) << 16)
            item.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(item, content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return data.getvalue()


def new_file(path, content):
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(content)


def build(repo, ref, output):
    repo = Path(repo).resolve()
    output = Path(output).absolute()
    if os.path.lexists(str(output)):
        raise BuildError('Output already exists; choose a new directory. Nothing was overwritten.')
    if not output.parent.is_dir():
        raise BuildError('Output parent must already exist; choose an approved parent directory.')
    output = output.parent.resolve() / output.name
    commit = resolve_commit(repo, ref)
    files = tracked_files(repo, commit)
    version = version_from_source(next(item['content'] for item in files if item['path'] == 'scripts/doctorlib/__init__.py'))
    commit_timestamp = int(git(repo, 'show', '-s', '--format=%ct', commit).strip())
    license_files = sorted(item['path'] for item in files if item['path'] in LICENSE_FILES)
    warnings = [] if license_files else ['No LICENSE file for Doctor code exists at this ref. No project-code license is asserted. Separately attributed third-party material retains its included license.']
    release_info = {
        'schema_version': 1, 'name': 'milvus-doctor', 'version': version,
        'git_commit': commit, 'git_commit_timestamp': commit_timestamp,
        'license_files': license_files, 'warnings': warnings,
        'source_policy': 'Only allowlisted regular Git blobs at git_commit; no working-tree content.',
    }
    package = create_zip(files, release_info, zip_timestamp(commit_timestamp))
    archive_name = 'milvus-doctor-' + version + '.zip'
    manifest = dict(release_info)
    manifest.update({
        'requested_git_ref': ref, 'archive': {'file': archive_name, 'bytes': len(package), 'sha256': hashlib.sha256(package).hexdigest()},
        'files': [{key: value for key, value in item.items() if key != 'content'} for item in files],
        'generated_archive_entries': ['milvus-doctor/RELEASE.json'],
        'excluded': ['tests/', 'docs/', 'reports/', 'internal evidence', 'credentials/token/secret paths', 'virtual environments/caches', '.git/', 'scripts/build_release.py', 'scripts/sync_faq.py'],
        'review_boundary': 'Path allowlisting is not a complete secret-content audit. Review tracked release content before publishing. SHA256 is an integrity check, not a signing identity.',
    })
    manifest_bytes = json_bytes(manifest)
    checksums = '{}  {}\n{}  release-manifest.json\n'.format(
        hashlib.sha256(package).hexdigest(), archive_name, hashlib.sha256(manifest_bytes).hexdigest()).encode('ascii')
    try:
        output.mkdir(mode=0o700)
        new_file(output / archive_name, package)
        new_file(output / 'release-manifest.json', manifest_bytes)
        new_file(output / 'SHA256SUMS', checksums)
    except FileExistsError:
        raise BuildError('Output appeared during build; refusing to overwrite it. Inspect the selected directory.')
    return {'status': 'built_not_published', 'output_dir': str(output), 'version': version,
            'git_commit': commit, 'archive': str(output/archive_name),
            'manifest': str(output/'release-manifest.json'), 'checksums': str(output/'SHA256SUMS'),
            'source_files': len(files), 'warnings': warnings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--ref', required=True, help='Explicit committed tag, branch or full commit SHA; resolved once to a commit.')
    parser.add_argument('--output-dir', required=True, type=Path, help='New directory under an existing parent; never overwritten.')
    args = parser.parse_args()
    try:
        result = build(args.repo, args.ref, args.output_dir)
    except (BuildError, OSError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({'status': 'error', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())

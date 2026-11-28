#!/usr/bin/env python3
"""Catalog conformance checker (catalog contract, spec 010 R-20).

    python3 tools/check_catalog.py <catalog-dir> [--skip-index-check]

Stdlib only. Without --skip-index-check it also runs the manifest's
`index.check` command through the shell, which executes catalog code: run it
only on catalogs you trust, or pass --skip-index-check. Prints one `<path>: <rule>: <message>` line per
violation and exits 1; exits 0 when the catalog conforms. Rules: manifest,
files, runner, dockerfile, config, metadata, ids, index.

Inputs are untrusted: manifest paths must be relative, free of '..' and
resolve inside the catalog root, and symlinks inside implementation
directories are violations (the runner must be a real copy, R-3, R-5).

The metadata `version` may come from the index entry instead (R-10, R-11).
Directories matched by the glob with no implementation files that are not in
the index (docs/, tests/) are skipped.

Not checked yet: the catalog's own metadata schema (only the base fields of
schema/implementation.base.schema.json are), and the `http` test type.
"""
import collections
import glob
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from toolset.utils.jsonschema_lite import validate  # noqa: E402

SCHEMAS = os.path.join(os.path.dirname(HERE), 'schema')
SUPPORTED_CONTRACT = (1, 1)
ID_RE = re.compile(r'^[a-z0-9][a-z0-9._-]*$')


def _load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _schema(name):
    return _load(os.path.join(SCHEMAS, name))


def _path_problem(root, rel, base=None):
    """Why manifest path `rel` (joined to `base`, default root) is unsafe, or None."""
    if '\0' in rel:
        return f'path {rel!r} must not contain NUL'
    if os.path.isabs(rel) or rel.startswith(('/', '\\')) or re.match(r'^[A-Za-z]:', rel):
        return f'path {rel!r} must be relative'
    if '..' in re.split(r'[/\\]', rel):
        return f"path {rel!r} must not contain '..'"
    if base is not None or '*' not in rel:
        full = os.path.join(base or root, rel)
        if not _inside(root, os.path.realpath(full)):
            return f'path {rel!r} resolves outside the catalog root'
    return None


def _real_file(path):
    return os.path.isfile(path) and not os.path.islink(path)


def _inside(root, path):
    root = os.path.realpath(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def check_dockerfile(path):
    """Violations of R-4 that can be seen without building."""
    problems = []
    with open(path, encoding='utf-8') as f:
        lines = f.read().splitlines()
    stages = set()
    for line in lines:
        m = re.match(r'[ \t]*FROM[ \t]+(?:--\S+[ \t]+)?(\S+)(?:[ \t]+AS[ \t]+(\S+))?', line, re.I)
        if not m:
            continue
        image, stage = m.groups()
        if stage:
            stages.add(stage.lower())
        if image.lower() == 'scratch' or image.lower() in stages or '@sha256:' in image:
            continue
        name = image.rsplit('/', 1)[-1]
        if ':' not in name or name.split(':', 1)[1] in ('latest', ''):
            problems.append(f'base image {image!r} is not pinned to an exact tag')
    text = '\n'.join(lines)
    if re.search(r'https?://', text) and not re.search(r'^[ \t]*ARG[ \t]+\w+_VERSION=', text, re.M):
        problems.append('downloads a file but declares no ARG <NAME>_VERSION=')
    return problems


def _id_for(manifest, rel):
    parts = rel.split('/')
    if manifest['name'] == 'algorithms':
        return f'{parts[-2]}.{parts[-1]}'
    return parts[-1]


def check_index_entries(doc, key):
    """Violations of R-11 in the index document."""
    problems = []
    for field in ('schemaVersion', 'generatedFrom'):
        if field not in doc:
            problems.append(f'{field}: required')
    entries = doc.get(key)
    if not isinstance(entries, list):
        return problems + [f'{key}: must be a list of entries']
    ids = [e['id'] for e in entries if isinstance(e, dict) and isinstance(e.get('id'), str)]
    counts = collections.Counter(ids)
    for dup in sorted(i for i, n in counts.items() if n > 1):
        problems.append(f'{key}: duplicate id {dup!r} ({counts[dup]} entries)')
    for n, e in enumerate(entries):
        if not isinstance(e, dict):
            problems.append(f'{key}[{n}]: must be an object')
            continue
        label = f'{key}[{n}] ({e.get("id")})'
        for field in ('id', 'path', 'version'):
            if not (isinstance(e.get(field), str) and e[field]):
                problems.append(f'{label}: {field} must be a non-empty string')
        status = (e.get('benchmark') or {}).get('status') if isinstance(e.get('benchmark'), dict) else None
        if status not in ('active', 'wip', 'removed'):
            problems.append(f'{label}: benchmark.status must be active, wip or removed')
    return problems


def check(root, skip_index=False):
    out = []

    def bad(path, rule, msg):
        out.append(f'{os.path.relpath(path, root) if path else "."}: {rule}: {msg}')

    manifest_path = os.path.join(root, 'catalog.json')
    if not os.path.isfile(manifest_path):
        bad(manifest_path, 'manifest', 'catalog.json not found')
        return out
    try:
        manifest = _load(manifest_path)
    except ValueError as e:
        bad(manifest_path, 'manifest', f'invalid JSON: {e}')
        return out
    errors = validate(manifest, _schema('catalog.schema.json'))
    for e in errors:
        bad(manifest_path, 'manifest', e)
    if errors:
        return out
    version = manifest['contractVersion']
    if not SUPPORTED_CONTRACT[0] <= version <= SUPPORTED_CONTRACT[1]:
        bad(manifest_path, 'manifest',
            f'contractVersion {version} is outside the supported range '
            f'[{SUPPORTED_CONTRACT[0]}, {SUPPORTED_CONTRACT[1]}]')
        return out

    runner = manifest.get('runner')
    unsafe = False
    for label, rel, base in (
            ('implementations', manifest['implementations'], None),
            ('runner.file', (runner or {}).get('file'), root),
            ('runner.canonical', (runner or {}).get('canonical'), None),
            ('metadata.file', manifest['metadata']['file'], root),
            ('index.file', manifest['index']['file'], None)):
        if not isinstance(rel, str):
            continue
        problem = _path_problem(root, rel, base)
        if problem:
            bad(manifest_path, 'manifest', f'{label}: {problem}')
            unsafe = True
    if unsafe:
        return out

    canonical = None
    if runner:
        canonical_path = os.path.join(root, runner['canonical'])
        if os.path.isfile(canonical_path) and not _inside(root, os.path.realpath(canonical_path)):
            bad(canonical_path, 'runner', 'canonical runner resolves outside the catalog root')
        elif os.path.isfile(canonical_path):
            with open(canonical_path, 'rb') as f:
                canonical = f.read()
        else:
            bad(canonical_path, 'runner', 'canonical runner not found')
    elif 'http' not in manifest['testTypes'] or len(manifest['testTypes']) > 1:
        bad(manifest_path, 'manifest', 'runner is required unless the catalog is http-only')

    config_schema = _schema('benchmark_config.schema.json')
    base_schema = _schema('implementation.base.schema.json')
    seen = {}
    index = manifest['index']
    index_path = os.path.join(root, index['file'])
    entries = []
    index_ok = False
    if not os.path.isfile(index_path):
        bad(index_path, 'index', 'missing')
    else:
        try:
            index_doc = _load(index_path)
        except ValueError as e:
            bad(index_path, 'index', f'invalid JSON: {e}')
        else:
            if not isinstance(index_doc, dict):
                bad(index_path, 'index', 'document must be a JSON object')
            else:
                for msg in check_index_entries(index_doc, index['key']):
                    bad(index_path, 'index', msg)
                if isinstance(index_doc.get(index['key']), list):
                    entries = [e for e in index_doc[index['key']] if isinstance(e, dict)]
                index_ok = True
    listed_paths = {e['path'] for e in entries if isinstance(e.get('path'), str)}
    versions = {}
    for e in entries:
        if isinstance(e.get('version'), str) and e['version']:
            for k in (e.get('id'), e.get('path')):
                if isinstance(k, str):
                    versions[k] = e['version']
    dirs = sorted(d for d in glob.glob(os.path.join(root, manifest['implementations']))
                  if os.path.isdir(d))
    for d in list(dirs):
        if os.path.islink(d) or not _inside(root, os.path.realpath(d)):
            bad(d, 'files', 'implementation directory is a symlink or resolves outside the catalog root')
            dirs.remove(d)
    if not dirs:
        bad(root, 'files', f'no implementation matches {manifest["implementations"]!r}')
    for d in dirs:
        rel = os.path.relpath(d, root)
        expected_id = _id_for(manifest, rel)
        meta_path = os.path.join(d, manifest['metadata']['file'])
        dockerfile = os.path.join(d, expected_id + '.dockerfile')
        markers = [dockerfile, os.path.join(d, 'benchmark_config.json'), meta_path]
        if runner:
            markers.append(os.path.join(d, runner['file']))
        if rel not in listed_paths and not any(os.path.lexists(m) for m in markers):
            continue  # not an implementation (docs/, tests/, ...)
        runner_copy = os.path.join(d, runner['file']) if runner else None
        for base, subdirs, files in os.walk(d):
            for name in sorted(subdirs + files):
                link = os.path.join(base, name)
                if os.path.islink(link):
                    bad(link, 'runner' if link == runner_copy else 'files',
                        'symlinks are not allowed in implementation directories')
        needed = [dockerfile, os.path.join(d, 'benchmark_config.json'), meta_path]
        if runner:
            needed.append(os.path.join(d, runner['file']))
        for path in needed:
            if not os.path.isfile(path) and not os.path.islink(path):
                bad(path, 'files', 'missing')
        if runner and canonical is not None:
            copy = os.path.join(d, runner['file'])
            if os.path.isfile(copy) and not os.path.islink(copy):
                with open(copy, 'rb') as f:
                    if f.read() != canonical:
                        bad(copy, 'runner', f'differs from {runner["canonical"]}')
        if _real_file(dockerfile):
            for msg in check_dockerfile(dockerfile):
                bad(dockerfile, 'dockerfile', msg)
        cfg_path = os.path.join(d, 'benchmark_config.json')
        if _real_file(cfg_path):
            try:
                for e in validate(_load(cfg_path), config_schema):
                    bad(cfg_path, 'config', e)
            except ValueError as e:
                bad(cfg_path, 'config', f'invalid JSON: {e}')
        if _real_file(meta_path):
            try:
                meta = _load(meta_path)
            except ValueError as e:
                bad(meta_path, 'metadata', f'invalid JSON: {e}')
                continue
            for e in validate(meta, base_schema):
                bad(meta_path, 'metadata', e)
            if not isinstance(meta, dict):
                continue
            mid = meta.get('id')
            if not isinstance(mid, str):
                bad(meta_path, 'ids', f'id must be a string, got {type(mid).__name__}')
                continue
            mver = meta.get('version')
            if not (isinstance(mver, str) and mver) and mid not in versions and rel not in versions:
                bad(meta_path, 'metadata', 'version: required in the metadata file or the index entry')
            if mid != expected_id:
                bad(meta_path, 'ids', f'id {mid!r} must be {expected_id!r} (from the directory)')
            if mid in seen:
                bad(meta_path, 'ids', f'duplicate id {mid!r} (also {seen[mid]})')
            seen[mid] = rel
            if not ID_RE.match(str(mid)):
                bad(meta_path, 'ids', f'id {mid!r} does not match {ID_RE.pattern}')

    if index_ok:
        listed = {e['id']: e.get('path') for e in entries if isinstance(e.get('id'), str)}
        for mid, rel in seen.items():
            if listed.get(mid) != rel:
                bad(index_path, 'index', f'{mid!r} is missing or has another path')
        for mid in listed:
            if mid not in seen:
                bad(index_path, 'index', f'{mid!r} has no implementation directory')
    if not skip_index:
        try:
            run = subprocess.run(index['check'], shell=True, cwd=root, timeout=300,
                                 capture_output=True, text=True)
            if run.returncode != 0:
                last = (run.stdout + run.stderr).strip().splitlines()[-1:] or ['']
                bad(index_path, 'index', f'`{index["check"]}` exited {run.returncode}: {last[0]}')
        except subprocess.TimeoutExpired:
            bad(index_path, 'index', f'`{index["check"]}` timed out after 300 s')
    return out


def main(argv):
    args = [a for a in argv if not a.startswith('--')]
    if len(args) != 1:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    problems = check(os.path.abspath(args[0]), '--skip-index-check' in argv)
    for line in problems:
        print(line)
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))

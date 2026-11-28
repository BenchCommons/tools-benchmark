"""Fixture index check: implementations.json lists exactly the meta.json files on disk."""
import glob
import json
import os
import sys

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
index = json.load(open(os.path.join(root, 'implementations.json')))['implementations']
want = {}
for path in glob.glob(os.path.join(root, 'languages', '*', '*', 'meta.json')):
    meta = json.load(open(path))
    want[meta['id']] = os.path.relpath(os.path.dirname(path), root)
have = {e['id']: e['path'] for e in index}
if have != want:
    print('implementations.json is stale: %s != %s' % (have, want))
    sys.exit(1)

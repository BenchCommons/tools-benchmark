"""Small stdlib JSON Schema validator for the catalog contract (spec 010).

Supported keywords: type, enum, const, required, properties,
additionalProperties (bool or schema), items, minItems, minLength, minimum,
pattern, allOf, if/then and $ref to "#/$defs/<name>" or "#/$defs/...".
Unknown keywords are ignored. validate() returns "<json pointer>: <message>"
items; an empty list means valid.
"""
import re

_TYPES = {
    'object': dict,
    'array': list,
    'string': str,
    'boolean': bool,
    'null': type(None),
}


def _is_type(value, name):
    if name == 'integer':
        return isinstance(value, int) and not isinstance(value, bool)
    if name == 'number':
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _TYPES[name])


def _resolve(ref, root):
    node = root
    for part in ref.removeprefix('#/').split('/'):
        node = node[part]
    return node


def _walk(value, schema, root, path, out):
    if '$ref' in schema:
        _walk(value, _resolve(schema['$ref'], root), root, path, out)
    where = path or '/'
    if 'type' in schema and not _is_type(value, schema['type']):
        out.append(f"{where}: expected {schema['type']}")
        return
    if 'enum' in schema and value not in schema['enum']:
        out.append(f"{where}: {value!r} is not one of {schema['enum']}")
    if 'const' in schema and value != schema['const']:
        out.append(f"{where}: expected {schema['const']!r}")
    if isinstance(value, str):
        if len(value) < schema.get('minLength', 0):
            out.append(f"{where}: shorter than {schema['minLength']}")
        if 'pattern' in schema and not re.search(schema['pattern'], value):
            out.append(f"{where}: does not match {schema['pattern']}")
    if _is_type(value, 'number') and value < schema.get('minimum', value):
        out.append(f"{where}: below {schema['minimum']}")
    if isinstance(value, dict):
        for key in schema.get('required', []):
            if key not in value:
                out.append(f"{path}/{key}: required")
        props = schema.get('properties', {})
        extra = schema.get('additionalProperties', True)
        for key, item in value.items():
            if key in props:
                _walk(item, props[key], root, f"{path}/{key}", out)
            elif extra is False:
                out.append(f"{path}/{key}: unexpected property")
            elif isinstance(extra, dict):
                _walk(item, extra, root, f"{path}/{key}", out)
    if isinstance(value, list):
        if len(value) < schema.get('minItems', 0):
            out.append(f"{where}: fewer than {schema['minItems']} items")
        if 'items' in schema:
            for i, item in enumerate(value):
                _walk(item, schema['items'], root, f"{path}/{i}", out)
    for sub in schema.get('allOf', []):
        _walk(value, sub, root, path, out)
    if 'if' in schema and not validate(value, schema['if'], root):
        _walk(value, schema.get('then', {}), root, path, out)


def validate(instance, schema, root=None):
    out = []
    _walk(instance, schema, root or schema, '', out)
    return out

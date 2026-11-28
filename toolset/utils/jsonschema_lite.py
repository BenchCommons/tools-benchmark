"""Small stdlib JSON Schema validator for the catalog contract (spec 010).

Supported keywords: type, enum, const, required, properties,
additionalProperties (bool or schema), items, minItems, minLength, minimum,
pattern, allOf, if/then and $ref to "#/$defs/..." or to an entry of the optional
registry (a catalog schema references the base schema that way, spec 010 R-10).
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
    try:
        for part in ref.removeprefix('#/').split('/'):
            node = node[part]
    except (KeyError, TypeError, IndexError):
        raise ValueError(f'unresolved $ref {ref!r}') from None
    return node


def _walk(value, schema, root, path, out, registry=None):
    if '$ref' in schema:
        ref = schema['$ref']
        if isinstance(ref, str) and ref.startswith('#/'):
            _walk(value, _resolve(ref, root), root, path, out, registry)
        elif isinstance(ref, str) and registry and ref in registry:
            target = registry[ref]
            _walk(value, target, target, path, out, registry)
        else:
            raise ValueError(f'unsupported $ref {ref!r}')
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
                _walk(item, props[key], root, f"{path}/{key}", out, registry)
            elif extra is False:
                out.append(f"{path}/{key}: unexpected property")
            elif isinstance(extra, dict):
                _walk(item, extra, root, f"{path}/{key}", out, registry)
    if isinstance(value, list):
        if len(value) < schema.get('minItems', 0):
            out.append(f"{where}: fewer than {schema['minItems']} items")
        if 'items' in schema:
            for i, item in enumerate(value):
                _walk(item, schema['items'], root, f"{path}/{i}", out, registry)
    for sub in schema.get('allOf', []):
        _walk(value, sub, root, path, out, registry)
    if 'if' in schema and not validate(value, schema['if'], root, registry):
        _walk(value, schema.get('then', {}), root, path, out, registry)


def validate(instance, schema, root=None, registry=None):
    """Messages for `instance` against `schema`; an empty list means valid.

    `registry` maps a non-local $ref (for example the base schema's URL) to a
    schema. A $ref that is neither local ("#/...") nor in the registry raises
    ValueError, so the caller can report it instead of guessing.
    """
    out = []
    _walk(instance, schema, root or schema, '', out, registry)
    return out

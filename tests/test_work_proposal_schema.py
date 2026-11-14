"""The native Ollama grammar follows exact private success and blocker contracts."""
import json

import pytest

from gamma.coordination.work_learner import LocalWorkProposer, work_proposal_schema, proposal_shape_diagnostics
from test_practical_work import spec


def matches(schema, value):
    """Small independent validator for only the standard keywords used here."""
    if 'oneOf' in schema:
        return sum(matches(item, value) for item in schema['oneOf']) == 1
    types = {'object': dict, 'array': list, 'string': str}
    if type(value) is not types[schema['type']]:
        return False
    if 'enum' in schema and value not in schema['enum']:
        return False
    if isinstance(value, str):
        return schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', 10**6)
    if isinstance(value, list):
        return schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', 10**6) and all(matches(schema['items'], item) for item in value)
    if not schema.get('minProperties', 0) <= len(value) <= schema.get('maxProperties', 10**6):
        return False
    props = schema['properties']
    return set(schema.get('required', [])) <= set(value) and (schema.get('additionalProperties') is not False or set(value) <= set(props)) and all(matches(props[key], item) for key, item in value.items())


def test_build_allows_only_admitted_edits_and_typed_blocker_alternative():
    schema = work_proposal_schema(spec())
    assert matches(schema, {'plan': ['Implement the missing name.'], 'edits': {'tool.py': 'print("hello")'}})
    assert matches(schema, {'plan': ['Inspect the missing file.'], 'edits': {}, 'blocker': {'kind': 'missing_input', 'question': 'Which offline file should I inspect?'}})
    for value in [
        {'plan': ['Do work'], 'edits': {}},
        {'plan': ['Do work'], 'edits': {'check_tool.py': 'pass'}},
        {'plan': ['Do work'], 'edits': {'tool.py': 'pass'}, 'blocker': None},
        {'plan': ['Ask'], 'edits': {}, 'blocker': {'kind': 'missing_input', 'question': 'Which file?', 'scope': 'activate'}},
        {'plan': ['Ask'], 'edits': {'tool.py': 'pass'}, 'blocker': {'kind': 'scope_decision', 'question': 'Which scope?'}},
        {'plan': ['Ask'], 'edits': {}, 'blocker': {'kind': 'activate', 'question': 'May I?'}},
        {'plan': [], 'edits': {'tool.py': 'pass'}},
        {'plan': ['x'*501], 'edits': {'tool.py': 'pass'}},
        {'plan': ['x'], 'edits': {'tool.py': 'x'*60001}},
        {'plan': ['Do work'], 'edits': {'tool.py': 'pass'}, 'private-secret-field': 'secret'}]:
        assert not matches(schema, value)


def test_research_schema_cannot_generate_edits_or_null_optional_blocker():
    schema = work_proposal_schema(spec('research'))
    assert matches(schema, {'plan': ['guide.md describes the expected behavior.'], 'edits': {}})
    assert not matches(schema, {'plan': ['Implement'], 'edits': {'tool.py': 'pass'}})
    assert not matches(schema, {'plan': ['Inspect'], 'edits': {}, 'blocker': None})


def test_diagnostics_never_include_raw_values_or_unknown_names():
    value = {'plan': ['RAW_SECRET'], 'edits': {'SECRET_PATH': 'RAW_SOURCE'}, 'blocker': None, 'SECRET_UNKNOWN_NAME': 'RAW_OTHER'}
    diagnostic = proposal_shape_diagnostics(value)
    assert diagnostic == {'top_level_type': 'object', 'known_field_types': {'plan': 'array', 'edits': 'object', 'blocker': 'null'}, 'unknown_field_count': 1}
    assert 'SECRET' not in json.dumps(diagnostic) and 'RAW_' not in json.dumps(diagnostic)


def test_proposer_sends_native_schema_and_records_only_shape(monkeypatch):
    from contextlib import contextmanager
    import gamma.coordination.work_learner as module
    @contextmanager
    def slot(*_args, **_kwargs):
        yield
    monkeypatch.setattr(module, 'backend_slot', slot)
    proposer = LocalWorkProposer(endpoint='http://127.0.0.1:11434', model='fictional:1b')
    captured = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, _): return json.dumps({'done': True, 'message': {'content': json.dumps({'plan': ['Inspect source'], 'edits': {}})}}).encode()
    class Opener:
        def open(self, request, **_kwargs):
            captured.append(json.loads(request.data)); return Response()
    proposer.opener = Opener()
    value = spec('research'); result = proposer(value, {}, [])
    assert result == {'plan': ['Inspect source'], 'edits': {}}
    assert isinstance(captured[0]['format'], dict) and matches(captured[0]['format'], result)
    assert proposer.last_diagnostics['unknown_field_count'] == 0
    assert 'source' not in json.dumps(proposer.last_diagnostics)

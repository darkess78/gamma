"""Constrain private local generation without changing the public turn contract."""
from copy import deepcopy

from .personal_models import PrivateTurnDraft


def private_turn_schema(*, compact: bool = False) -> dict:
    schema = PrivateTurnDraft.model_json_schema()
    definitions = schema.pop('$defs', {})
    properties = schema['properties']
    # These output channels have no authority in this isolated slice.
    for name in ('tool_calls', 'memory_candidates', 'voice_styles', 'motions'):
        properties[name] = {'type': 'array', 'maxItems': 0, 'items': {'type': 'string'}}
    properties['internal_summary']['anyOf'][0]['maxLength'] = 300
    schema['required'] = list(properties)
    schema['additionalProperties'] = False
    state_name = properties['state_updates']['$ref'].rsplit('/', 1)[-1]
    state = definitions[state_name]
    state['additionalProperties'] = False
    state['required'] = list(state['properties'])
    action = state['properties']['activity_action']['anyOf'][0]
    state['properties']['activity_action'] = {'type': 'string', 'enum': action['enum']}
    variants = []
    for actions, delivery, communicating in (
        (['reply', 'acknowledge'], 'text_only', True),
        (['stay_silent'], 'silent', False),
        (['defer'], 'deferred', False),
    ):
        variant = deepcopy(schema)
        variant['properties']['action'] = {'type': 'string', 'enum': actions}
        variant['properties']['requested_delivery'] = {'type': 'string', 'const': delivery}
        variant['properties']['final_text'] = (
            {'type': 'string', 'minLength': 1, 'maxLength': 16000}
            if communicating else {'type': 'string', 'const': ''}
        )
        variants.append(variant)
    result={'oneOf': variants, '$defs': definitions}
    if compact:
        from .compact_schema import compact_private_schema
        return compact_private_schema(result)
    return result

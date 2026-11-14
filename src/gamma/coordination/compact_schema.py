"""Same typed private decisions with optional unchanged serialization fields."""
from copy import deepcopy


def compact_private_schema(schema):
    required=['action','requested_delivery','final_text','emotion','state_updates','reason_code']
    if not isinstance(schema,dict) or not isinstance(schema.get('oneOf'),list) or not schema['oneOf']:
        raise ValueError('private schema requires nonempty decision variants')
    definitions=schema.get('$defs')
    if not isinstance(definitions,dict):raise ValueError('private schema definitions required')
    names=[]
    for variant in schema['oneOf']:
        if not isinstance(variant,dict) or variant.get('additionalProperties') is not False:
            raise ValueError('private decision must reject extra properties')
        properties=variant.get('properties')
        if not isinstance(properties,dict) or any(key not in properties for key in required):
            raise ValueError('private decision properties missing')
        state=properties['state_updates']
        reference=state.get('$ref') if isinstance(state,dict) else None
        if not isinstance(reference,str) or not reference.startswith('#/$defs/'):
            raise ValueError('private state must use a local definition')
        name=reference[len('#/$defs/'):]
        if not name or '/' in name or '~' in name:
            raise ValueError('private state definition name invalid')
        definition=definitions.get(name)
        if (not isinstance(definition,dict) or not isinstance(definition.get('properties'),dict)
                or 'activity_action' not in definition['properties']):
            raise ValueError('private activity decision required')
        names.append(name)
    result=deepcopy(schema)
    for variant in result['oneOf']:variant['required']=list(required)
    for name in names:result['$defs'][name]['required']=['activity_action']
    return result

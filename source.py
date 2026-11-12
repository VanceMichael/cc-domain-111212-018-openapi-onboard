import json

def inspect_bytes(data):
    document = json.loads(data)
    if str(document.get('openapi', '')).split('.')[:2] not in (['3','0'], ['3','1']):
        raise ValueError('只支持 OpenAPI 3.0/3.1')
    result = []
    for path, item in document.get('paths', {}).items():
        for method, operation in item.items():
            if method.lower() in ('get','post','put','patch','delete','head','options','trace'):
                result.append({'path':path,'method':method.upper(),
                               'operation':operation.get('operationId'), 'summary':operation.get('summary','')})
    return result

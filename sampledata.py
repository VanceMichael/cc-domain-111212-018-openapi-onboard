import json


def _doc(document):
    return json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2).encode()


def _supplier_a_v1():
    return {
        'openapi': '3.1.0',
        'info': {'title': 'Supplier A', 'version': '1.0.0'},
        'servers': [
            {'url': 'http://supplier-a.example.test/v1'},
            {'url': 'http://127.0.0.1:0/v1'},
        ],
        'security': [{'apiKeyHeader': []}],
        'paths': {
            '/items': {
                'parameters': [
                    {'name': 'tenant', 'in': 'query', 'schema': {'type': 'string'}},
                ],
                'get': {
                    'operationId': 'listItems',
                    'summary': '列出商品（只读）',
                    'parameters': [
                        {'name': 'ids', 'in': 'query', 'required': True,
                         'style': 'form', 'explode': False,
                         'schema': {'type': 'array', 'items': {'type': 'string'}}},
                        {'name': 'tenant', 'in': 'query',
                         'schema': {'type': 'string', 'enum': ['main', 'sandbox']}},
                    ],
                    'responses': {
                        '200': {'description': 'ok', 'content': {
                            'application/json': {'schema': {'$ref': '#/components/schemas/Page'}}}},
                        '404': {'description': 'missing', 'content': {
                            'application/json': {'schema': {'type': 'object',
                                'properties': {'code': {'type': 'string'}},
                                'required': ['code']}}}},
                    },
                },
                'post': {
                    'operationId': 'createItem',
                    'summary': '创建商品（写操作，带示例也不允许试采）',
                    'requestBody': {'required': True, 'content': {
                        'application/json': {'schema': {'type': 'object',
                            'properties': {'name': {'type': 'string'}},
                            'required': ['name']}}}},
                    'responses': {'201': {'description': 'created'}},
                },
            },
            '/items/{itemId}': {
                'get': {
                    'operationId': 'getItem',
                    'summary': '单个商品（只读，路径参数）',
                    'parameters': [
                        {'name': 'itemId', 'in': 'path', 'required': True,
                         'schema': {'type': 'string'}},
                    ],
                    'responses': {'200': {'description': 'ok', 'content': {
                        'application/json': {'schema': {'$ref': '#/components/schemas/Item'}}}}},
                },
            },
        },
        'components': {
            'securitySchemes': {
                'apiKeyHeader': {'type': 'apiKey', 'in': 'header', 'name': 'X-Key'},
            },
            'schemas': {
                'Page': {'type': 'object', 'required': ['items'],
                         'properties': {'items': {'type': 'array',
                                                   'items': {'$ref': '#/components/schemas/Item'}},
                                        'next': {'type': ['string', 'null']}}},
                'Item': {'type': 'object', 'required': ['id', 'name'],
                         'properties': {'id': {'type': 'string'},
                                        'name': {'type': 'string'},
                                        'category': {'$ref': '../shared/external.json#/Category'},
                                        'tags': {'type': 'array',
                                                 'items': {'type': ['string', 'null']}}}},
            },
        },
    }


def _supplier_a_v2():
    document = _supplier_a_v1()
    document['info'] = {'title': 'Supplier A', 'version': '2.0.0'}
    # 认证变化：保留 apiKey 备选，新增 bearer 备选
    document['security'] = [{'apiKeyHeader': []}, {'bearerAuth': []}]
    document['components']['securitySchemes']['bearerAuth'] = {
        'type': 'http', 'scheme': 'bearer'}
    # 请求编码变化：form+不展开 -> pipeDelimited；tenant 变为必填
    items = document['paths']['/items']
    items['parameters'] = []
    items['get']['parameters'] = [
        {'name': 'ids', 'in': 'query', 'required': True,
         'style': 'pipeDelimited', 'explode': False,
         'schema': {'type': 'array', 'items': {'type': 'string'}}},
        {'name': 'tenant', 'in': 'query', 'required': True,
         'schema': {'type': 'string', 'enum': ['main', 'sandbox']}},
    ]
    # 响应字段变化：id 改类型、新增必填 owner、next 不再可空
    item = document['components']['schemas']['Item']
    item['properties']['id'] = {'type': 'integer'}
    item['properties']['owner'] = {'type': 'string'}
    item['required'] = ['id', 'name', 'owner']
    document['components']['schemas']['Page']['properties']['next'] = {'type': 'string'}
    # 新增只读 operation（非破坏性）
    document['paths']['/vendors'] = {
        'get': {'operationId': 'listVendors', 'summary': '列出供应商（v2 新增）',
                'security': [],
                'responses': {'200': {'description': 'ok', 'content': {
                    'application/json': {'schema': {'type': 'array',
                        'items': {'type': 'string'}}}}}}}}
    # 新增服务器
    document['servers'].append({'url': 'http://127.0.0.1:0/v2'})
    return document


def _supplier_b_v1():
    return {
        'openapi': '3.0.3',
        'info': {'title': 'Supplier B', 'version': '1.0.0'},
        'servers': [
            {'url': 'https://supplier-b.example.test/api'},
            {'url': 'http://127.0.0.1:0/api'},
        ],
        'security': [{'oauth2Flow': []}],
        'paths': {
            '/search': {
                'get': {
                    'operationId': 'search',
                    'summary': '标签样式与 deepObject（部分无法确定，须人工补充映射）',
                    'parameters': [
                        {'name': 'term', 'in': 'path', 'style': 'label',
                         'schema': {'type': 'string'}, 'required': True},
                        {'name': 'filter', 'in': 'query', 'style': 'deepObject',
                         'schema': {'type': 'object',
                                    'additionalProperties': {'type': 'string'}}},
                        {'name': 'region', 'in': 'header',
                         'schema': {'type': 'string', 'nullable': True}},
                    ],
                    'responses': {
                        '200': {'description': 'ok', 'content': {
                            'application/json': {'schema': {
                                'type': 'object',
                                'properties': {'hits': {'type': 'array', 'items': {
                                    'type': 'integer', 'nullable': True}}}}}}},
                    },
                },
            },
            '/external': {
                'get': {
                    'operationId': 'externalRef',
                    'summary': '引用未登记外部文档（闭包之外，阻塞）',
                    'responses': {
                        '200': {'description': 'ok', 'content': {
                            'application/json': {'schema': {
                                '$ref': 'https://vendor.example.test/schemas.json#/Remote'}}}},
                    },
                },
            },
            '/legacy': {
                'get': {
                    'operationId': 'legacyShape',
                    'summary': 'allOf 组合超出契约，须人工映射',
                    'responses': {
                        '200': {'description': 'ok', 'content': {
                            'application/xml': {'schema': {'type': 'string'}},
                            'application/json': {'schema': {
                                'allOf': [
                                    {'type': 'object',
                                     'properties': {'id': {'type': 'string'}}},
                                    {'type': 'object',
                                     'properties': {'zone': {'type': 'string'}}},
                                ]}}}},
                    },
                },
            },
            '/matrix': {
                'get': {
                    'operationId': 'matrixQuery',
                    'summary': 'query 位置声明 matrix 样式（未知样式，阻塞）',
                    'parameters': [
                        {'name': 'window', 'in': 'query', 'style': 'matrix',
                         'schema': {'type': 'string'}},
                    ],
                    'responses': {'200': {'description': 'ok'}},
                },
            },
        },
        'components': {
            'securitySchemes': {
                'oauth2Flow': {'type': 'oauth2',
                               'flows': {'clientCredentials': {
                                   'tokenUrl': 'https://supplier-b.example.test/token',
                                   'scopes': {}}}},
            },
            'schemas': {},
        },
    }


def _shared_external():
    return {'Category': {'type': 'object', 'required': ['code'],
                         'properties': {'code': {'type': 'string'},
                                        'label': {'type': ['string', 'null']}}}}


def samples():
    document = {'openapi': '3.1.0', 'info': {'title': 'Supplier', 'version': '1'},
        'servers': [{'url': 'http://supplier.local'}],
        'paths': {'/items': {'parameters': [{'name': 'ids', 'in': 'query', 'style': 'form', 'explode': False, 'schema': {'type': 'array', 'items': {'type': 'string'}}}],
                          'get': {'operationId': 'items', 'security': [{'apiKey': []}], 'responses': {'200': {'description': 'ok', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/Page'}}}}}},
                          'post': {'operationId': 'create', 'responses': {'201': {'description': 'created'}}}}},
        'components': {'securitySchemes': {'apiKey': {'type': 'apiKey', 'in': 'header', 'name': 'X-Key'}},
                      'schemas': {'Page': {'type': 'object', 'required': ['items'], 'properties': {'items': {'type': 'array', 'items': {'type': ['string', 'null']}}}}}}}
    return {
        'sample.json': json.dumps(document).encode(),
        'invalid.json': b'{"openapi":"2.0","paths":{}}',
        'supplier-a/1.json': _doc(_supplier_a_v1()),
        'supplier-a/2.json': _doc(_supplier_a_v2()),
        'supplier-b/1.json': _doc(_supplier_b_v1()),
        'shared/external.json': _doc(_shared_external()),
    }

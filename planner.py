"""确定性 OpenAPI 3.0/3.1 接入计划。

计划只描述确定的请求/响应形状与阻塞项，不猜测无法确定的结构；不包含任何
凭据值。所有输出按键排序、列表顺序固定，保证同一文档重复构建结果一致。
"""
from urllib.parse import quote

from registry import RegistryError, _escape_pointer

HTTP_METHODS = ('get', 'post', 'put', 'patch', 'delete', 'head', 'options', 'trace')
READ_ONLY_METHODS = ('get', 'head')
JSON_PRIMITIVES = {'string', 'integer', 'number', 'boolean', 'null', 'object', 'array'}
SUPPORTED_SCHEMA_KEYWORDS = {
    '$ref', 'type', 'format', 'items', 'properties', 'required', 'enum',
    'oneOf', 'anyOf', 'nullable', 'description', 'title', 'default',
    'example', 'examples', 'deprecated', 'readOnly', 'writeOnly', '$comment',
}
# 按位置允许的序列化样式（契约声明的 simple/form/space/pipe/deepObject 子集）
SUPPORTED_STYLES = {
    'query': {'form', 'spaceDelimited', 'pipeDelimited', 'deepObject'},
    'path': {'simple'},
    'header': {'simple'},
    'cookie': {'form'},
}
DEFAULT_STYLE = {'query': 'form', 'path': 'simple', 'header': 'simple', 'cookie': 'form'}
DEFAULT_EXPLODE = {'form': True}  # 其余样式默认 False


class Blocked(ValueError):
    """计划无法构建（文档级硬错误，例如非法 OpenAPI 版本）。"""


def _ref_location(current_key, pointer):
    return current_key + ('#' + pointer if pointer else '')


class _Scanner:
    def __init__(self, registry, root_key):
        self.registry = registry
        self.root_key = root_key
        self.blockers = []

    def block(self, code, location, message):
        entry = {'code': code, 'location': location, 'message': message}
        if entry not in self.blockers:
            self.blockers.append(entry)

    def snapshot(self):
        return len(self.blockers)

    def since(self, marker):
        """返回自 marker 之后新增的阻塞项（只属于当前 operation 的局部诊断）。"""
        return [dict(entry) for entry in self.blockers[marker:]]

    def resolve_ref(self, node, location, guard):
        ref = node.get('$ref')
        if not isinstance(ref, str):
            self.block('INVALID_SCHEMA', location, '$ref 必须是字符串')
            return None, None, guard
        if ref in guard:
            # 递归 schema 合法：形状层以 recursive 标记，不产生阻塞
            return None, None, guard
        try:
            key, pointer, target = self.registry.resolve(ref, self.root_key)
        except RegistryError as error:
            self.block('UNRESOLVABLE_REFERENCE', location, str(error))
            return None, None, guard
        return (key, pointer), target, guard | {ref}

    def scan(self, node, location, current_key=None, guard=None):
        """递归扫描 schema，登记所有超出契约范围的用法。返回归一化前的节点。"""
        current_key = current_key or self.root_key
        guard = guard if guard is not None else set()
        if not isinstance(node, dict):
            self.block('INVALID_SCHEMA', location, 'schema 必须是对象')
            return node, current_key
        if '$ref' in node:
            origin, target, new_guard = self.resolve_ref(node, location, guard)
            if target is None:
                return node, current_key
            key, pointer = origin
            return self.scan(target, _ref_location(key, pointer), key, new_guard)
        for keyword in node:
            if keyword not in SUPPORTED_SCHEMA_KEYWORDS:
                self.block('UNSUPPORTED_SCHEMA', location + '/' + _escape_pointer(keyword),
                           '超出契约支持范围的 schema 关键字: ' + keyword)
        if 'allOf' in node:
            self.block('UNSUPPORTED_SCHEMA', location + '/allOf', '不支持 allOf 组合，需要人工补充映射')
        for combinator in ('oneOf', 'anyOf'):
            if combinator in node:
                branches = node[combinator]
                if not isinstance(branches, list) or not branches:
                    self.block('INVALID_SCHEMA', location + '/' + combinator, combinator + ' 必须是非空数组')
                else:
                    for index, branch in enumerate(branches):
                        self.scan(branch, location + '/' + combinator + '/' + str(index),
                                  current_key, set(guard))
        declared = node.get('type')
        if declared is not None:
            types = declared if isinstance(declared, list) else [declared]
            if not types or any(t not in JSON_PRIMITIVES for t in types):
                self.block('INVALID_SCHEMA', location + '/type', '非法 type 取值: ' + repr(declared))
        if node.get('nullable') is not None and not isinstance(node.get('nullable'), bool):
            self.block('INVALID_SCHEMA', location + '/nullable', 'nullable 必须是布尔值')
        if 'properties' in node:
            properties = node['properties']
            if not isinstance(properties, dict):
                self.block('INVALID_SCHEMA', location + '/properties', 'properties 必须是对象')
            else:
                for name in sorted(properties):
                    self.scan(properties[name], location + '/properties/' + _escape_pointer(name),
                              current_key, set(guard))
                required = node.get('required', [])
                if not isinstance(required, list) or any(not isinstance(r, str) for r in required):
                    self.block('INVALID_SCHEMA', location + '/required', 'required 必须是字符串数组')
                else:
                    for name in sorted(set(required) - set(properties)):
                        self.block('INVALID_SCHEMA', location + '/required',
                                   'required 字段未在 properties 声明: ' + name)
        if 'items' in node:
            self.scan(node['items'], location + '/items', current_key, set(guard))
        elif declared == 'array' or (isinstance(declared, list) and 'array' in declared):
            self.block('INVALID_SCHEMA', location + '/items', 'array schema 缺少 items，请求形状无法确定')
        if 'enum' in node and not isinstance(node['enum'], list):
            self.block('INVALID_SCHEMA', location + '/enum', 'enum 必须是数组')
        return node, current_key


def _normalize_types(node):
    declared = node.get('type')
    types = list(declared) if isinstance(declared, list) else ([declared] if isinstance(declared, str) else [])
    if node.get('nullable') is True and 'null' not in types:
        types.append('null')
    return types


def shape_of(node, registry, root_key, current_key=None, guard=None):
    """构建用于计划导出与版本对比的稳定形状（$ref 内联展开）。"""
    current_key = current_key or root_key
    guard = guard if guard is not None else set()
    if not isinstance(node, dict):
        return {'kind': 'unknown'}
    if '$ref' in node:
        ref = node['$ref']
        if ref in guard:
            return {'kind': 'recursive', 'ref': ref}
        try:
            key, pointer, target = registry.resolve(ref, current_key)
        except RegistryError:
            return {'kind': 'unresolved', 'ref': ref}
        return shape_of(target, registry, key, key, guard | {ref})
    for combinator in ('oneOf', 'anyOf'):
        if combinator in node and isinstance(node[combinator], list) and node[combinator]:
            return {'kind': 'union', 'combinator': combinator,
                    'variants': [shape_of(branch, registry, root_key, current_key, set(guard))
                                 for branch in node[combinator]]}
    types = _normalize_types(node)
    if 'array' in types:
        shape = {
            'kind': 'array',
            'items': (shape_of(node['items'], registry, root_key, current_key, set(guard))
                      if isinstance(node.get('items'), dict) else {'kind': 'unknown'}),
        }
        if 'enum' in node:
            shape['enum'] = node['enum']
        return shape
    if 'object' in types:
        properties = node.get('properties', {})
        shape = {
            'kind': 'object',
            'properties': {name: shape_of(schema, registry, root_key, current_key, set(guard))
                           for name, schema in sorted(properties.items())}
            if isinstance(properties, dict) else {},
            'required': sorted(node['required']) if isinstance(node.get('required'), list) else [],
        }
        if 'enum' in node:
            shape['enum'] = node['enum']
        return shape
    shape = {'kind': 'type', 'types': sorted(types)} if types else {'kind': 'untyped'}
    if 'enum' in node:
        shape['enum'] = node['enum']
    if 'format' in node and shape['kind'] == 'type':
        shape['format'] = node['format']
    return shape


def _flatten_nullable_union(shape):
    """oneOf/anyOf 若只是“单类型 + null”，归一化为可确定的 nullable 形状。"""
    if shape.get('kind') != 'union':
        return shape, True
    non_null = [v for v in shape['variants']
                if not (v.get('kind') == 'type' and v.get('types') == ['null'])]
    nulls = [v for v in shape['variants'] if v not in non_null]
    if len(non_null) == 1 and nulls:
        base = dict(non_null[0])
        if base.get('kind') == 'type':
            base['types'] = sorted(set(base.get('types', []) + ['null']))
            return base, True
    return shape, False


def _find_unvalidatable(shape):
    """返回形状树中无法按契约子集验证的节点描述。"""
    kind = shape.get('kind')
    if kind in ('recursive', 'unresolved', 'unknown'):
        return kind
    if kind == 'union':
        for variant in shape.get('variants', []):
            found = _find_unvalidatable(variant)
            if found:
                return found
    elif kind == 'object':
        for nested in shape.get('properties', {}).values():
            found = _find_unvalidatable(nested)
            if found:
                return found
    elif kind == 'array':
        return _find_unvalidatable(shape.get('items', {'kind': 'unknown'}))
    return None


def _validate_style(scanner, param, location):
    where = param['in']
    style = param.get('style', DEFAULT_STYLE[where])
    if style not in SUPPORTED_STYLES.get(where, set()):
        scanner.block('UNSUPPORTED_STYLE', location + '/style',
                      '位置 %s 不支持样式 %s，需要人工确认编码' % (where, style))
        return style, bool(param.get('explode', DEFAULT_EXPLODE.get(style, False)))
    explode = bool(param.get('explode', DEFAULT_EXPLODE.get(style, False)))
    schema = param.get('schema', {})
    types = _normalize_types(schema) if isinstance(schema, dict) else []
    if style == 'deepObject' and 'object' not in types:
        scanner.block('UNSUPPORTED_STYLE', location + '/style',
                      'deepObject 只能用于 object 参数')
    if style in ('spaceDelimited', 'pipeDelimited') and 'array' not in types:
        scanner.block('UNSUPPORTED_STYLE', location + '/style',
                      style + ' 只能用于 array 参数')
    return style, explode


def _plan_parameter(scanner, raw, location):
    param = raw
    if isinstance(raw, dict) and '$ref' in raw:
        try:
            key, pointer, target = scanner.registry.resolve(raw['$ref'], scanner.root_key)
        except RegistryError as error:
            scanner.block('UNRESOLVABLE_REFERENCE', location + '/$ref', str(error))
            return None
        param = target
        location = _ref_location(key, pointer)
    if not isinstance(param, dict) or 'name' not in param or 'in' not in param:
        scanner.block('INVALID_PARAMETER', location, '参数缺少 name/in')
        return None
    where = param['in']
    if where not in SUPPORTED_STYLES:
        scanner.block('INVALID_PARAMETER', location + '/in', '不支持的参数位置: ' + str(where))
        return None
    if 'content' in param:
        scanner.block('UNSUPPORTED_PARAMETER_CONTENT', location + '/content',
                      '参数使用 content 编码，契约不支持，需要人工补充映射')
        return None
    schema = param.get('schema')
    if not isinstance(schema, dict):
        scanner.block('INVALID_PARAMETER', location + '/schema', '参数缺少可确定的 schema')
        return None
    scanner.scan(schema, location + '/schema')
    shape = shape_of(schema, scanner.registry, scanner.root_key)
    shape, deterministic = _flatten_nullable_union(shape)
    if not deterministic:
        scanner.block('INDETERMINATE_UNION', location + '/schema',
                      '参数联合 schema 存在多个非空分支，无法确定请求编码')
    style, explode = _validate_style(scanner, {**param, 'schema': schema}, location)
    return {
        'name': param['name'], 'in': where,
        'required': bool(param.get('required', where == 'path')),
        'style': style, 'explode': explode, 'shape': shape,
        'schema_location': location + '/schema',
    }


def _plan_request_body(scanner, body, location):
    if not isinstance(body, dict) or 'content' not in body or not isinstance(body.get('content'), dict):
        scanner.block('INDETERMINATE_REQUEST_BODY', location, 'requestBody 缺少 content，请求形状无法确定')
        return None
    media_types = sorted(body['content'])
    if len(media_types) != 1:
        scanner.block('AMBIGUOUS_MEDIA_TYPE', location + '/content',
                      'requestBody 声明多个 media type，无法自动选择: ' + ', '.join(media_types))
        return None
    media_type = media_types[0]
    if not (media_type == 'application/json' or media_type.endswith('+json')):
        scanner.block('UNSUPPORTED_MEDIA_TYPE', location + '/content/' + media_type,
                      '试采只支持 JSON 请求编码: ' + media_type)
        return None
    media = body['content'][media_type]
    schema = media.get('schema') if isinstance(media, dict) else None
    if not isinstance(schema, dict):
        scanner.block('INDETERMINATE_REQUEST_BODY', location + '/content/' + media_type,
                      'media type 缺少 schema')
        return None
    scanner.scan(schema, location + '/content/' + media_type + '/schema')
    shape = shape_of(schema, scanner.registry, scanner.root_key)
    shape, deterministic = _flatten_nullable_union(shape)
    if not deterministic:
        scanner.block('INDETERMINATE_UNION', location + '/content/' + media_type + '/schema',
                      '请求体联合 schema 存在多个非空分支，无法确定请求形状')
    return {'required': bool(body.get('required', False)), 'media_type': media_type, 'shape': shape}


def _plan_responses(scanner, responses, location):
    planned = []
    if not isinstance(responses, dict):
        scanner.block('INVALID_SCHEMA', location, 'responses 必须是对象')
        return planned
    for status in sorted(responses, key=lambda s: (s != 'default', str(s))):
        entry = responses[status]
        if not isinstance(entry, dict):
            scanner.block('INVALID_SCHEMA', location + '/' + str(status), 'response 必须是对象')
            continue
        contents = []
        for media_type in sorted(entry.get('content', {})):
            media = entry['content'][media_type]
            schema = media.get('schema') if isinstance(media, dict) else None
            if not isinstance(schema, dict):
                scanner.block('INVALID_SCHEMA',
                              location + '/' + str(status) + '/content/' + media_type,
                              'response media type 缺少 schema')
                continue
            scanner.scan(schema, location + '/' + str(status) + '/content/' + media_type + '/schema')
            if not (media_type == 'application/json' or media_type.endswith('+json')):
                scanner.block('UNSUPPORTED_MEDIA_TYPE',
                              location + '/' + str(status) + '/content/' + media_type,
                              '试采验证只支持 JSON 响应: ' + media_type)
            contents.append({'media_type': media_type,
                             'shape': shape_of(schema, scanner.registry, scanner.root_key)})
        planned.append({'status': str(status), 'content': contents})
    return planned


def _plan_security(scanner, requirements, components, location):
    schemes = components.get('securitySchemes', {}) if isinstance(components, dict) else {}
    alternatives = []
    effective = requirements if isinstance(requirements, list) else []
    for index, requirement in enumerate(effective):
        if not isinstance(requirement, dict) or not requirement:
            scanner.block('INVALID_SECURITY_REF', location + '/' + str(index), '安全需求必须是非空对象')
            continue
        members = []
        for name in sorted(requirement):
            scheme_location = 'components/securitySchemes/' + name
            scheme = schemes.get(name)
            if not isinstance(scheme, dict):
                scanner.block('INVALID_SECURITY_REF', scheme_location, 'securitySchemes 未声明: ' + name)
                members.append({'scheme': name, 'supported': False, 'env': None})
                continue
            kind = scheme.get('type')
            supported = (
                (kind == 'apiKey'
                 and scheme.get('in') in ('header', 'cookie')
                 and bool(scheme.get('name')))
                or (kind == 'http' and scheme.get('scheme') == 'bearer'))
            member = {'scheme': name, 'type': kind, 'supported': supported, 'env': None}
            if kind == 'apiKey':
                member.update({'in': scheme.get('in'), 'name': scheme.get('name')})
                if scheme.get('in') not in ('header', 'query', 'cookie') or not scheme.get('name'):
                    scanner.block('INVALID_SECURITY_REF', scheme_location,
                                  'apiKey 必须声明合法的 in 与 name')
                    member['supported'] = False
                elif scheme.get('in') == 'query':
                    scanner.block('UNSUPPORTED_SECURITY', scheme_location,
                                  '试采不开放 query 位置的 apiKey 凭据注入: ' + name)
            elif kind == 'http':
                member['scheme_name'] = scheme.get('scheme')
            if not supported:
                scanner.block('UNSUPPORTED_SECURITY', scheme_location,
                              '试采不支持的安全方案类型: %s/%s' % (kind, scheme.get('scheme')))
            members.append(member)
        alternatives.append({'members': members})
    return alternatives


def _server_urls(servers):
    urls = []
    for entry in servers or []:
        if isinstance(entry, dict) and isinstance(entry.get('url'), str):
            urls.append(entry['url'])
    return urls


def build_plan(document_key, registry):
    """从登记文档构建确定性接入计划。文档级硬错误抛 Blocked，结构级问题进 blockers。"""
    document = registry.document(document_key)
    if not isinstance(document, dict):
        raise Blocked('OpenAPI 文档必须是 JSON 对象')
    version = str(document.get('openapi', ''))
    if version.split('.')[:2] not in (['3', '0'], ['3', '1']):
        raise Blocked('只支持 OpenAPI 3.0/3.1，实际版本: ' + repr(document.get('openapi')))
    scanner = _Scanner(registry, document_key)
    root_servers = _server_urls(document.get('servers'))
    components = document.get('components', {})
    operations = []
    paths = document.get('paths', {})
    if not isinstance(paths, dict):
        raise Blocked('paths 必须是对象')
    for path in sorted(paths):
        item = paths[path]
        base_location = 'paths/' + _escape_pointer(path)
        if isinstance(item, dict) and '$ref' in item:
            try:
                key, pointer, item = registry.resolve(item['$ref'], document_key)
            except RegistryError as error:
                scanner.block('UNRESOLVABLE_REFERENCE', base_location + '/$ref', str(error))
                continue
            base_location = _ref_location(key, pointer)
        if not isinstance(item, dict):
            scanner.block('INVALID_SCHEMA', base_location, 'path item 必须是对象')
            continue
        path_params = [p for p in item.get('parameters', []) if isinstance(p, dict)]
        for method in HTTP_METHODS:
            if method not in item:
                continue
            operation = item[method]
            op_marker = scanner.snapshot()
            if not isinstance(operation, dict):
                scanner.block('INVALID_SCHEMA', base_location + '/' + method, 'operation 必须是对象')
                continue
            op_location = base_location + '/' + method
            merged = {}
            for raw in path_params + [p for p in operation.get('parameters', []) if isinstance(p, dict)]:
                planned = _plan_parameter(scanner, raw, op_location + '/parameters')
                if planned is not None:
                    merged[(planned['name'], planned['in'])] = planned  # operation 级覆盖 path 级
            parameters = [merged[key] for key in sorted(merged)]
            request_body = None
            if 'requestBody' in operation:
                body = operation['requestBody']
                if isinstance(body, dict) and '$ref' in body:
                    try:
                        _, _, body = registry.resolve(body['$ref'], document_key)
                    except RegistryError as error:
                        scanner.block('UNRESOLVABLE_REFERENCE', op_location + '/requestBody/$ref', str(error))
                        body = None
                if isinstance(body, dict):
                    request_body = _plan_request_body(scanner, body, op_location + '/requestBody')
            responses = _plan_responses(scanner, operation.get('responses', {}), op_location + '/responses')
            for planned_param in parameters:
                bad = _find_unvalidatable(planned_param['shape'])
                if bad:
                    scanner.block('UNVALIDATABLE_SCHEMA',
                                  op_location + '/parameters/' + planned_param['name'] + '/shape',
                                  '参数形状含 %s 节点，无法按契约验证，需要人工补充映射' % bad)
            for response in responses:
                for media in response['content']:
                    bad = _find_unvalidatable(media['shape'])
                    if bad:
                        scanner.block('UNVALIDATABLE_SCHEMA',
                                      op_location + '/responses/' + response['status']
                                      + '/content/' + media['media_type'],
                                      '响应形状含 %s 节点，结构验证无法完成，需要人工补充映射' % bad)
            if request_body is not None:
                bad = _find_unvalidatable(request_body['shape'])
                if bad:
                    scanner.block('UNVALIDATABLE_SCHEMA',
                                  op_location + '/requestBody/shape',
                                  '请求体形状含 %s 节点，无法确定请求形状' % bad)
            if 'security' in operation:
                security = _plan_security(scanner, operation.get('security'), components,
                                          op_location + '/security')
            else:
                security = _plan_security(scanner, document.get('security', []), components, 'security')
            servers = _server_urls(operation.get('servers')) or root_servers
            for url in servers:
                if '{' in url or '}' in url:
                    scanner.block('SERVER_VARIABLE', op_location + '/servers',
                                  'server URL 含未解析变量，试采无法确定目标: ' + url)
            entry = {
                'method': method.upper(), 'path': path,
                'operation_id': operation.get('operationId'),
                'summary': operation.get('summary', ''),
                'read_only': method in READ_ONLY_METHODS,
                'servers': sorted(set(servers)),
                'parameters': parameters,
                'request_body': request_body,
                'security': security,
                'responses': responses,
            }
            entry['blockers'] = scanner.since(op_marker)
            entry['trial_eligible'] = (entry['read_only'] and not entry['blockers'])
            operations.append(entry)
    doc_blockers = sorted(scanner.blockers, key=lambda b: (b['location'], b['code'], b['message']))
    plan = {
        'document': document_key,
        'openapi': version,
        'info': {'title': document.get('info', {}).get('title', ''),
                 'version': str(document.get('info', {}).get('version', ''))},
        'servers': sorted(set(root_servers)),
        'references': registry.closure(document_key),
        'operations': operations,
        'blockers': doc_blockers,
        'trial_eligible_operations': sorted(
            '%s %s' % (op['method'], op['path']) for op in operations if op['trial_eligible']),
    }
    return plan


def bind_auth(plan, auth_env):
    """按授权环境把 security 备选方案与环境变量名绑定；返回新计划，绝不写入变量值。"""
    bound = json_clone(plan)
    for operation in bound['operations']:
        for alternative in operation['security']:
            for member in alternative['members']:
                env_name = auth_env.get(member['scheme'])
                if isinstance(env_name, str) and env_name:
                    member['env'] = env_name
    return bound


def json_clone(value):
    import json
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))


# ---- 试采请求编码（只实现契约支持的样式子集） ------------------------------------

def _quote(value, allow=''):
    return quote(str(value), safe=allow)


def render_query(parameter, value):
    """返回 (查询串, 警告列表)；查询串不含前导 '?'。"""
    name = parameter['name']
    style = parameter['style']
    explode = parameter['explode']
    n, v = _quote(name), _quote(value, '')
    if style == 'form':
        if isinstance(value, list):
            if explode:
                return '&'.join(n + '=' + _quote(item) for item in value), []
            return n + '=' + ','.join(_quote(item) for item in value), []
        if isinstance(value, dict):
            if explode:
                return '&'.join(_quote(k) + '=' + _quote(val) for k, val in value.items()), []
            return n + '=' + ','.join(_quote(x) for pair in value.items() for x in pair), []
        return n + '=' + v, []
    if style == 'spaceDelimited':
        if explode:
            return '&'.join(n + '=' + _quote(item) for item in value), []
        return n + '=' + ' '.join(str(item) for item in value).replace(' ', '%20'), []
    if style == 'pipeDelimited':
        if explode:
            return '&'.join(n + '=' + _quote(item) for item in value), []
        return n + '=' + '|'.join(_quote(item) for item in value), []
    if style == 'deepObject':
        return '&'.join(n + '[' + _quote(k) + ']=' + _quote(val) for k, val in value.items()), []
    raise ValueError('未支持的查询样式: ' + style)


def render_path(parameter, value):
    explode = parameter['explode']
    if isinstance(value, list):
        return (','.join(_quote(item) for item in value) if not explode
                else ','.join(_quote(item) for item in value))
    if isinstance(value, dict):
        if explode:
            return ','.join(_quote(k) + '=' + _quote(val) for k, val in value.items())
        return ','.join(_quote(x) for pair in value.items() for x in pair)
    return _quote(value)


def render_simple_value(parameter, value):
    """header(simple) / cookie 标量编码。"""
    if isinstance(value, list):
        return ','.join(_quote(item) for item in value)
    if isinstance(value, dict):
        if parameter['explode']:
            return ','.join(_quote(k) + '=' + _quote(val) for k, val in value.items())
        return ','.join(_quote(x) for pair in value.items() for x in pair)
    return _quote(value)

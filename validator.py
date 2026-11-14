"""按实际状态码与 content type 对应的 schema 验证试采响应。

HTTP 成功只表示传输完成；结构不符时返回结构性错误，调用方不得据此标记接入完成。
"""
import json


class ResponseValidationError(ValueError):
    """响应无法按声明 schema 解释（结构错误，与网络错误区分）。"""


def _check_type(value, types):
    if not types:  # untyped：契约不约束
        return True
    python_types = {
        'string': (str,), 'integer': (int,), 'number': (int, float),
        'boolean': (bool,), 'null': (type(None),),
    }
    if isinstance(value, bool):
        allowed = {'boolean'}
    elif isinstance(value, int):
        allowed = {'integer', 'number'}
    elif isinstance(value, float):
        allowed = {'number'}
    elif isinstance(value, str):
        allowed = {'string'}
    elif value is None:
        allowed = {'null'}
    elif isinstance(value, list):
        allowed = {'array'}
    elif isinstance(value, dict):
        allowed = {'object'}
    else:
        allowed = set()
    return any(t in allowed for t in types)


def validate_shape(shape, value, location='$'):
    errors = []
    kind = shape.get('kind')
    if kind in ('unknown', 'unresolved', 'recursive'):
        return [location + ': schema ' + kind + '，无法验证结构']
    if kind == 'union':
        combinator = shape.get('combinator', 'oneOf')
        branch_errors = []
        matches = 0
        for index, variant in enumerate(shape.get('variants', [])):
            branch = validate_shape(variant, value, location)
            if not branch:
                matches += 1
            else:
                branch_errors.append(branch)
        if combinator == 'oneOf':
            if matches != 1:
                errors.append('%s: oneOf 需要恰好匹配一个分支，实际匹配 %d 个' % (location, matches))
        elif not matches:
            errors.append('%s: anyOf 没有匹配的分支' % location)
        return errors
    if kind == 'object':
        if not isinstance(value, dict):
            return [location + ': 期望 object，实际 %s' % type(value).__name__]
        properties = shape.get('properties', {})
        for name in shape.get('required', []):
            if name not in value:
                errors.append('%s.%s: 缺少必填字段' % (location, name))
        for name in sorted(value):
            if name in properties:
                errors.extend(validate_shape(properties[name], value[name],
                                             location + '.' + name))
            # 未声明字段按开放对象容忍，不猜测业务含义
        return errors
    if kind == 'array':
        if not isinstance(value, list):
            return [location + ': 期望 array，实际 %s' % type(value).__name__]
        for index, item in enumerate(value):
            errors.extend(validate_shape(shape.get('items', {'kind': 'unknown'}), item,
                                         location + '[' + str(index) + ']'))
        if 'enum' in shape and value not in shape['enum']:
            errors.append(location + ': 值不在 enum 范围内')
        return errors
    types = shape.get('types', [])
    if not _check_type(value, types):
        errors.append('%s: 期望类型 %s，实际 %s' % (location, '/'.join(types) or 'untyped',
                                                   type(value).__name__))
    if 'enum' in shape and value not in shape['enum']:
        errors.append(location + ': 值不在 enum 范围内')
    return errors


def _content_type(headers):
    raw = ''
    for key, value in headers.items():
        if key.lower() == 'content-type':
            raw = value
            break
    return raw.split(';', 1)[0].strip().lower()


def _match_media(declared, actual):
    if declared == actual:
        return True
    if declared.endswith('/*') and actual.startswith(declared[:-1]):
        return True
    return False


def validate_response(operation_plan, status, headers, raw):
    """返回 (通过?, 诊断)。诊断含 status/content_type/schema_errors 等确定信息。"""
    actual_type = _content_type(headers)
    responses = operation_plan.get('responses', [])
    chosen = next((r for r in responses if r['status'] == str(status)), None)
    if chosen is None:
        chosen = next((r for r in responses if r['status'] == 'default'), None)
    if chosen is None:
        return False, {'reason': 'UNDECLARED_STATUS', 'status': status,
                       'declared': [r['status'] for r in responses], 'content_type': actual_type}
    contents = chosen.get('content', [])
    if not contents:
        return True, {'status': status, 'content_type': actual_type, 'validated': False,
                      'note': '该状态未声明响应体，不做结构验证'}
    media = next((c for c in contents if _match_media(c['media_type'].lower(), actual_type)), None)
    if media is None:
        return False, {'reason': 'UNDECLARED_CONTENT_TYPE', 'status': status,
                       'content_type': actual_type,
                       'declared': [c['media_type'] for c in contents]}
    try:
        value = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        return False, {'reason': 'INVALID_JSON', 'status': status, 'content_type': actual_type,
                       'detail': str(error)}
    schema_errors = validate_shape(media['shape'], value)
    if schema_errors:
        return False, {'reason': 'SCHEMA_MISMATCH', 'status': status,
                       'content_type': actual_type, 'schema_errors': schema_errors}
    return True, {'status': status, 'content_type': actual_type, 'validated': True}

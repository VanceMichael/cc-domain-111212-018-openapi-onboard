"""受控自动试采。

硬性规则：
* 只执行批准清单显式列出、且计划判定为只读且无阻塞的 operation；
* server 必须与批准项逐字匹配，并限制在回环地址，不跟随示例自行选 server；
* 凭据只从进程环境按 security 备选方案读取，绝不写入计划、报告或导出；
* 响应按实际状态码与 content type 对应的 schema 验证，HTTP 200 但结构不符即失败。
"""
import ipaddress
from urllib.parse import urlsplit

import planner
from transport import fetch
from validator import validate_response

LOOPBACK_NAMES = {'localhost'}


class TrialConfigError(ValueError):
    """批准清单或授权环境配置不合法（计划本身没有问题，试采不予执行）。"""


def _assert_loopback(url):
    parts = urlsplit(url)
    if parts.scheme != 'http':
        raise TrialConfigError('试采只允许 http 回环目标: ' + url)
    host = parts.hostname or ''
    if host in LOOPBACK_NAMES:
        return
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # 不做 DNS 解析，避免名称解析到非回环地址造成越权访问
        raise TrialConfigError('试采目标必须是回环 IP/localhost，拒绝主机名: ' + host)
    if not address.is_loopback:
        raise TrialConfigError('试采目标必须是回环地址: ' + host)


def _find_operation(plan, operation_id=None, method=None, path=None):
    for operation in plan['operations']:
        if operation_id is not None and operation['operation_id'] != operation_id:
            continue
        if method is not None and operation['method'] != method.upper():
            continue
        if path is not None and operation['path'] != path:
            continue
        return operation
    return None


def _choose_security(operation):
    """选择第一条所有成员都受支持且已绑定环境变量的备选方案。"""
    alternatives = operation.get('security') or []
    if not alternatives:
        return [], None  # 匿名 operation
    for alternative in alternatives:
        members = alternative['members']
        if all(member.get('supported') and member.get('env') for member in members):
            return members, None
    unsupported = sorted({member['scheme']
                          for alternative in alternatives
                          for member in alternative['members']
                          if not member.get('supported')})
    if unsupported:
        return None, ('UNSUPPORTED_SECURITY: 备选方案含试采不支持的 security 方案: '
                      + ', '.join(unsupported))
    required = [member['scheme']
                for alternative in alternatives for member in alternative['members']
                if not member.get('env')]
    return None, 'AUTH_NOT_BOUND: 缺少授权环境绑定的 security 方案: ' + ', '.join(sorted(set(required)))


def _apply_security(members, headers, cookies):
    import os
    for member in members:
        secret = os.environ.get(member['env'], '')
        if secret == '':
            raise TrialConfigError('授权环境变量未提供值: ' + member['env'])
        kind = member.get('type')
        if kind == 'apiKey':
            where, name = member.get('in'), member.get('name')
            if where == 'header':
                headers[name] = secret
            elif where == 'query':
                raise TrialConfigError('apiKey in query 的凭据注入未在试采中开放: ' + member['scheme'])
            elif where == 'cookie':
                cookies[name] = secret
            else:
                raise TrialConfigError('不支持的 apiKey 位置: ' + str(where))
        elif kind == 'http':
            headers['Authorization'] = 'Bearer ' + secret
        else:
            raise TrialConfigError('不支持的 security 方案类型: ' + str(kind))


def _build_request(operation, server, inputs):
    path = operation['path']
    query_parts = []
    headers = {}
    cookies = {}
    inputs = inputs or {}
    declared = {(p['name'], p['in']) for p in operation['parameters']}
    name_to_locations = {}
    for name, where in declared:
        name_to_locations.setdefault(name, set()).add(where)
    unknown = []
    for name in inputs:
        if name not in name_to_locations:
            unknown.append((name, '?'))
    if unknown:
        raise TrialConfigError('批准输入包含计划未声明的参数: '
                               + ', '.join('%s(in=%s)' % (name, where) for name, where in unknown))
    for parameter in operation['parameters']:
        name, where = parameter['name'], parameter['in']
        if name not in inputs:
            if parameter['required']:
                raise TrialConfigError('缺少必填参数: ' + name)
            continue
        value = inputs[name]
        if value is None:
            if parameter['required']:
                raise TrialConfigError('必填参数不能为 null: ' + name)
            continue
        if where == 'path':
            rendered = planner.render_path(parameter, value)
            token = '{' + name + '}'
            if token not in path:
                raise TrialConfigError('路径模板中没有参数: ' + name)
            path = path.replace(token, rendered)
        elif where == 'query':
            rendered, _ = planner.render_query(parameter, value)
            query_parts.append(rendered)
        elif where == 'header':
            headers[name] = planner.render_simple_value(parameter, value)
        elif where == 'cookie':
            cookies[name] = planner.render_simple_value(parameter, value)
        else:
            raise TrialConfigError('不支持的参数位置: ' + where)
    if cookies:
        headers['Cookie'] = '; '.join(k + '=' + v for k, v in sorted(cookies.items()))
    if '{' in path or '}' in path:
        raise TrialConfigError('路径仍含未填充的模板占位符: ' + path)
    url = server.rstrip('/') + path
    if query_parts:
        url += '?' + '&'.join(query_parts)
    return url, headers, cookies


def run_trial(plan, approval, fetcher=None):
    """执行单条批准项，返回不包含任何凭据值的试采结果。"""
    fetcher = fetcher or fetch
    for field in ('server',):
        if not isinstance(approval.get(field), str) or not approval[field]:
            raise TrialConfigError('批准项缺少 ' + field)
    operation = _find_operation(
        plan, operation_id=approval.get('operationId'),
        method=approval.get('method'), path=approval.get('path'))
    result = {'operation_id': approval.get('operationId'),
              'method': approval.get('method'), 'path': approval.get('path'),
              'server': approval['server'], 'executed': False, 'ok': False}
    if operation is None:
        result['block_reason'] = 'NOT_IN_PLAN: 计划中不存在该 operation'
        return result
    result.update(method=operation['method'], path=operation['path'],
                  operation_id=operation['operation_id'])
    if not operation['read_only']:
        result['block_reason'] = 'NOT_READ_ONLY: 非 GET/HEAD 操作一律不自动试采'
        return result
    if operation['blockers']:
        result['block_reason'] = 'PLAN_BLOCKED: 计划存在阻塞项，禁止猜测请求形状'
        result['blockers'] = operation['blockers']
        return result
    if approval['server'] not in operation['servers']:
        result['block_reason'] = ('SERVER_NOT_APPROVED: server 必须与计划声明逐字匹配，实际 '
                                  + approval['server'])
        return result
    _assert_loopback(approval['server'])
    if operation.get('request_body'):
        result['block_reason'] = 'REQUEST_BODY_PRESENT: 只读试采不发送请求体'
        return result
    members, reason = _choose_security(operation)
    if reason:
        result['block_reason'] = reason
        return result
    try:
        url, headers, cookies = _build_request(
            operation, approval['server'], approval.get('inputs', {}))
        _apply_security(members, headers, cookies)
        if cookies:
            headers['Cookie'] = '; '.join(
                k + '=' + v for k, v in sorted(cookies.items()))
        status, response_headers, raw = fetcher(url, headers=headers)
    except TrialConfigError as error:
        result['block_reason'] = str(error)
        return result
    result['executed'] = True
    passed, diagnosis = validate_response(operation, status, response_headers, raw)
    result['http_status'] = status
    result['validation'] = diagnosis
    result['ok'] = passed
    return result


def run_approved_trials(plan, approvals, fetcher=None):
    """逐条执行批准清单；任何一项结构不符，整体不得标记接入完成。"""
    if not isinstance(approvals, list):
        raise TrialConfigError('批准清单必须是数组')
    results = [run_trial(plan, item, fetcher=fetcher) for item in approvals]
    onboarding_complete = bool(results) and all(r['ok'] for r in results)
    return {'document': plan['document'],
            'approved_count': len(approvals),
            'executed_count': sum(1 for r in results if r['executed']),
            'passed_count': sum(1 for r in results if r['ok']),
            'onboarding_complete': onboarding_complete,
            'results': results}

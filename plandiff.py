"""两个版本接入计划的确定性对比。

以 (method, path) 锁定 operation，逐字段比较参数编码、认证备选方案、server 与
响应 schema，标注是否影响已有来源（breaking）。输出不含凭据值，只比较 env 名。
"""
import json


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _shape_diff(a, b, location):
    changes = []
    if not isinstance(a, dict) or not isinstance(b, dict):
        return changes
    kind_a, kind_b = a.get('kind'), b.get('kind')
    if kind_a != kind_b:
        changes.append({'location': location, 'change': 'kind',
                        'from': kind_a, 'to': kind_b, 'breaking': True})
        return changes
    if kind_a == 'type':
        if sorted(a.get('types', [])) != sorted(b.get('types', [])):
            changes.append({'location': location, 'change': 'type',
                            'from': a.get('types', []), 'to': b.get('types', []),
                            'breaking': True})
        if a.get('format') != b.get('format'):
            changes.append({'location': location, 'change': 'format',
                            'from': a.get('format'), 'to': b.get('format'),
                            'breaking': True})
        if a.get('enum') != b.get('enum'):
            changes.append({'location': location, 'change': 'enum',
                            'from': a.get('enum'), 'to': b.get('enum'), 'breaking': True})
    elif kind_a == 'array':
        changes.extend(_shape_diff(a.get('items', {}), b.get('items', {}), location + '[]'))
        if a.get('enum') != b.get('enum'):
            changes.append({'location': location, 'change': 'enum',
                            'from': a.get('enum'), 'to': b.get('enum'), 'breaking': True})
    elif kind_a == 'object':
        props_a, props_b = a.get('properties', {}), b.get('properties', {})
        req_a, req_b = set(a.get('required', [])), set(b.get('required', []))
        for name in sorted(set(props_a) - set(props_b)):
            changes.append({'location': location + '.' + name, 'change': 'field_removed',
                            'from': 'present', 'to': 'absent', 'breaking': True})
        for name in sorted(set(props_b) - set(props_a)):
            changes.append({'location': location + '.' + name, 'change': 'field_added',
                            'from': 'absent', 'to': 'present',
                            'breaking': name in req_b})
        for name in sorted(set(props_a) & set(props_b)):
            was_required, now_required = name in req_a, name in req_b
            if was_required != now_required:
                changes.append({'location': location + '.' + name, 'change': 'required',
                                'from': was_required, 'to': now_required,
                                'breaking': now_required})
            changes.extend(_shape_diff(props_a[name], props_b[name], location + '.' + name))
    elif kind_a == 'union':
        if a.get('combinator') != b.get('combinator'):
            changes.append({'location': location, 'change': 'combinator',
                            'from': a.get('combinator'), 'to': b.get('combinator'),
                            'breaking': True})
        if _canonical(a.get('variants', [])) != _canonical(b.get('variants', [])):
            changes.append({'location': location, 'change': 'union_variants',
                            'from': a.get('variants', []), 'to': b.get('variants', []),
                            'breaking': True})
    return changes


def _security_signature(operation):
    alternatives = []
    for alternative in operation.get('security') or []:
        members = tuple(sorted(
            (m['scheme'], m.get('type'), m.get('env')) for m in alternative['members']))
        alternatives.append(members)
    return alternatives


def _diff_operation(a, b):
    changes = []
    if a.get('operation_id') != b.get('operation_id'):
        changes.append({'location': 'operationId', 'change': 'operation_id',
                        'from': a.get('operation_id'), 'to': b.get('operation_id'),
                        'breaking': True})
    removed_servers = sorted(set(a['servers']) - set(b['servers']))
    if removed_servers:
        changes.append({'location': 'servers', 'change': 'server_removed',
                        'from': removed_servers, 'to': None, 'breaking': True})
    added_servers = sorted(set(b['servers']) - set(a['servers']))
    if added_servers:
        changes.append({'location': 'servers', 'change': 'server_added',
                        'from': None, 'to': added_servers, 'breaking': False})
    if _security_signature(a) != _security_signature(b):
        changes.append({'location': 'security', 'change': 'security_alternatives',
                        'from': _security_signature(a), 'to': _security_signature(b),
                        'breaking': True})
    params_a = {(p['name'], p['in']): p for p in a['parameters']}
    params_b = {(p['name'], p['in']): p for p in b['parameters']}
    for key in sorted(set(params_a) - set(params_b)):
        changes.append({'location': 'parameter/%s(in=%s)' % key, 'change': 'parameter_removed',
                        'from': 'present', 'to': 'absent', 'breaking': True})
    for key in sorted(set(params_b) - set(params_a)):
        param = params_b[key]
        changes.append({'location': 'parameter/%s(in=%s)' % key, 'change': 'parameter_added',
                        'from': 'absent', 'to': 'present',
                        'breaking': param['required']})
    for key in sorted(set(params_a) & set(params_b)):
        pa, pb = params_a[key], params_b[key]
        where = 'parameter/%s(in=%s)' % key
        for field, breaking in (('style', True), ('explode', True), ('required', True)):
            if pa.get(field) != pb.get(field):
                changes.append({'location': where, 'change': field,
                                'from': pa.get(field), 'to': pb.get(field), 'breaking': breaking})
        changes.extend(_shape_diff(pa['shape'], pb['shape'], where + '.shape'))
    rba, rbb = a.get('request_body'), b.get('request_body')
    if bool(rba) != bool(rbb):
        changes.append({'location': 'request_body', 'change': 'request_body_presence',
                        'from': rba is not None, 'to': rbb is not None, 'breaking': True})
    elif rba and rbb:
        if rba['media_type'] != rbb['media_type']:
            changes.append({'location': 'request_body.media_type', 'change': 'media_type',
                            'from': rba['media_type'], 'to': rbb['media_type'], 'breaking': True})
        changes.extend(_shape_diff(rba['shape'], rbb['shape'], 'request_body.shape'))
    resp_a = {r['status']: r for r in a['responses']}
    resp_b = {r['status']: r for r in b['responses']}
    for status in sorted(set(resp_a) | set(resp_b)):
        if status not in resp_b:
            changes.append({'location': 'responses/' + status, 'change': 'response_removed',
                            'from': 'present', 'to': 'absent', 'breaking': True})
            continue
        if status not in resp_a:
            changes.append({'location': 'responses/' + status, 'change': 'response_added',
                            'from': 'absent', 'to': 'present', 'breaking': False})
            continue
        media_a = {c['media_type']: c['shape'] for c in resp_a[status]['content']}
        media_b = {c['media_type']: c['shape'] for c in resp_b[status]['content']}
        for media_type in sorted(set(media_a) | set(media_b)):
            loc = 'responses/%s/%s' % (status, media_type)
            if media_type not in media_b:
                changes.append({'location': loc, 'change': 'content_type_removed',
                                'from': 'present', 'to': 'absent', 'breaking': True})
            elif media_type not in media_a:
                changes.append({'location': loc, 'change': 'content_type_added',
                                'from': 'absent', 'to': 'present', 'breaking': False})
            else:
                changes.extend(_shape_diff(media_a[media_type], media_b[media_type], loc + '.shape'))
    codes_a = sorted(b_['code'] for b_ in a['blockers'])
    codes_b = sorted(b_['code'] for b_ in b['blockers'])
    if codes_a != codes_b:
        changes.append({'location': 'blockers', 'change': 'blockers',
                        'from': codes_a, 'to': codes_b,
                        'breaking': bool(set(codes_b) - set(codes_a))})
    if a.get('trial_eligible') != b.get('trial_eligible'):
        changes.append({'location': 'trial_eligible', 'change': 'trial_eligibility',
                        'from': a['trial_eligible'], 'to': b['trial_eligible'],
                        'breaking': a['trial_eligible'] and not b['trial_eligible']})
    return changes


def diff_plans(old_plan, new_plan):
    old_ops = {(o['method'], o['path']): o for o in old_plan['operations']}
    new_ops = {(o['method'], o['path']): o for o in new_plan['operations']}
    operations = []
    for key in sorted(set(old_ops) - set(new_ops)):
        operations.append({'method': key[0], 'path': key[1], 'change': 'operation_removed',
                           'breaking': True, 'changes': []})
    for key in sorted(set(new_ops) - set(old_ops)):
        operations.append({'method': key[0], 'path': key[1], 'change': 'operation_added',
                           'breaking': False, 'changes': []})
    for key in sorted(set(old_ops) & set(new_ops)):
        changes = _diff_operation(old_ops[key], new_ops[key])
        if changes:
            operations.append({'method': key[0], 'path': key[1],
                               'change': 'operation_changed',
                               'breaking': any(c['breaking'] for c in changes),
                               'changes': changes})
    all_changes = [c for op in operations for c in op['changes']]
    return {
        'from_document': old_plan['document'],
        'to_document': new_plan['document'],
        'summary': {
            'operations_added': sum(1 for o in operations if o['change'] == 'operation_added'),
            'operations_removed': sum(1 for o in operations if o['change'] == 'operation_removed'),
            'operations_changed': sum(1 for o in operations if o['change'] == 'operation_changed'),
            'field_changes': len(all_changes),
            'breaking_changes': sum(1 for c in all_changes if c['breaking']),
            'affects_existing_source': any(op['breaking'] for op in operations),
        },
        'operations': operations,
    }

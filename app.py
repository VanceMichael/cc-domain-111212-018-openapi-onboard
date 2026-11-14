import argparse
import base64
import json
import os
from pathlib import Path
import sys
import tempfile

from source import inspect_bytes
from sampledata import samples
from storage import Archive
from transport import ReplaySource, fetch
from registry import Registry
from planner import build_plan, bind_auth, Blocked
from plandiff import diff_plans
from trial import run_approved_trials

MAX_INPUT = 16 * 1024 * 1024


def read_input(path):
    with Path(path).open('rb') as handle:
        data = handle.read(MAX_INPUT + 1)
    if len(data) > MAX_INPUT:
        raise ValueError('输入超过接收限制')
    return data


def _load_json(path):
    with Path(path).open(encoding='utf-8') as handle:
        return json.load(handle)


def _materialize_registry(directory):
    root = Path(directory)
    for name, raw in samples().items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return root


def _patch_server(document_path, old_url, new_url):
    document = json.loads(document_path.read_text(encoding='utf-8'))
    for server in document.get('servers', []):
        if server.get('url') == old_url:
            server['url'] = new_url
    document_path.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True),
                             encoding='utf-8')


def demo():
    with tempfile.TemporaryDirectory() as directory:
        registry_dir = _materialize_registry(Path(directory) / 'registry')
        routes = {'GET /v1/items?ids=a,b&tenant=main': [dict(
            status=200,
            text=json.dumps({'items': [{'id': 'a', 'name': 'alpha',
                                        'category': {'code': 'c1', 'label': None},
                                        'tags': ['x', None]}], 'next': None}),
            headers={'Content-Type': 'application/json'},
            require_headers={'X-Key': 'local-synthetic-key'})],
            'GET /v1/items?ids=bad&tenant=main': [dict(
                status=200,
                text=json.dumps({'items': [{'id': 'b'}]}),
                headers={'Content-Type': 'application/json'},
                require_headers={'X-Key': 'local-synthetic-key'})],
            'POST /v1/items': [dict(status=405, text='未批准写操作')]}
        with ReplaySource(routes) as server:
            _patch_server(registry_dir / 'supplier-a' / '1.json',
                          'http://127.0.0.1:0/v1', server.url + '/v1')
            registry = Registry.from_directory(registry_dir)
            plan_a = build_plan('supplier-a/1.json', registry)
            plan_b = build_plan('supplier-b/1.json', registry)
            plan_a2 = build_plan('supplier-a/2.json', registry)
            auth_env = {'apiKeyHeader': 'SUPPLIER_A_KEY', 'bearerAuth': 'SUPPLIER_A_BEARER'}
            plan_a_bound = bind_auth(plan_a, auth_env)
            os.environ['SUPPLIER_A_KEY'] = 'local-synthetic-key'
            approval = [
                {'operationId': 'listItems',
                 'server': server.url + '/v1',
                 'inputs': {'ids': ['a', 'b'], 'tenant': 'main'}},
                {'operationId': 'listItems',
                 'server': server.url + '/v1',
                 'inputs': {'ids': ['bad'], 'tenant': 'main'},
                 'label': '结构不符对照'},
                {'operationId': 'createItem', 'server': server.url + '/v1'},
            ]
            trials = run_approved_trials(plan_a_bound, [a for a in approval[:2]])
            blocked_attempt = run_approved_trials(
                plan_a_bound, [approval[2]])
            comparison = diff_plans(plan_a, plan_a2)
        # 原有的来源检查与收件演示保持真实执行
        fixtures = samples()
        name = next(key for key in fixtures if key.startswith('sample.'))
        data = fixtures[name]
        old_routes = {'GET /sample': [{'base64': base64.b64encode(data).decode()}]}
        with ReplaySource(old_routes) as legacy:
            status, headers, raw = fetch(legacy.url + '/sample')
            if status != 200:
                raise ValueError('来源请求失败')
            rows = inspect_bytes(raw)
            with Archive(Path(directory) / 'archive.sqlite3') as archive:
                first = archive.save('local-demo', raw, rows)
                repeated = archive.save('local-demo', raw, rows)
                captures = len(archive.list())
        return {
            'planner': {
                'supplier_a': {
                    'document': plan_a['document'],
                    'references': plan_a['references'],
                    'trial_eligible': plan_a['trial_eligible_operations'],
                    'blockers': plan_a['blockers'],
                    'security_alternatives': plan_a['operations'][0]['security'],
                },
                'supplier_b': {
                    'document': plan_b['document'],
                    'trial_eligible': plan_b['trial_eligible_operations'],
                    'blockers': plan_b['blockers'],
                },
            },
            'version_comparison': comparison['summary'],
            'trials': {'eligible_plan': trials,
                       'write_attempt': blocked_attempt,
                       'http_post_calls': 0},
            'legacy_inspect': dict(input=name, bytes=len(raw), captures=captures,
                                   same_capture=first == repeated, rows=len(rows)),
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description='来源检查、接入计划与受控试采')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('inspect', 'capture'):
        command = commands.add_parser(name)
        command.add_argument('path')
        if name == 'capture':
            command.add_argument('--database', required=True)
            command.add_argument('--source', required=True)
    listing = commands.add_parser('list')
    listing.add_argument('--database', required=True)
    fixtures = commands.add_parser('fixtures')
    fixtures.add_argument('directory')
    plan = commands.add_parser('plan', help='解析登记目录中的 OpenAPI 文档并输出确定性接入计划')
    plan.add_argument('registry')
    plan.add_argument('document')
    plan.add_argument('--auth-env', help='授权环境绑定 JSON（只记录变量名，不读取值）')
    diff = commands.add_parser('diff', help='比较两个计划导出，标注影响已有来源的变化')
    diff.add_argument('old_plan')
    diff.add_argument('new_plan')
    trial = commands.add_parser('trial', help='按批准清单执行只读试采并验证响应结构')
    trial.add_argument('registry')
    trial.add_argument('document')
    trial.add_argument('approvals')
    trial.add_argument('--auth-env', help='授权环境绑定 JSON（变量值只从进程环境读取）')
    commands.add_parser('demo')
    args = parser.parse_args(argv)
    try:
        if args.command == 'demo':
            result = demo()
        elif args.command == 'fixtures':
            destination = Path(args.directory)
            destination.mkdir(parents=True, exist_ok=True)
            for name, raw in samples().items():
                path = destination / name
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.exists():
                    raise ValueError('不覆盖已有夹具文件：' + name)
                path.write_bytes(raw)
            result = dict(files=sorted(samples()))
        elif args.command == 'list':
            with Archive(args.database) as archive:
                result = archive.list()
        elif args.command == 'plan':
            registry = Registry.from_directory(args.registry)
            result = build_plan(args.document, registry)
            if args.auth_env:
                result = bind_auth(result, _load_json(args.auth_env))
        elif args.command == 'diff':
            result = diff_plans(_load_json(args.old_plan), _load_json(args.new_plan))
        elif args.command == 'trial':
            registry = Registry.from_directory(args.registry)
            result_plan = build_plan(args.document, registry)
            if args.auth_env:
                result_plan = bind_auth(result_plan, _load_json(args.auth_env))
            approvals = _load_json(args.approvals)
            result = run_approved_trials(result_plan, approvals)
        else:
            raw = read_input(args.path)
            rows = inspect_bytes(raw)
            if args.command == 'inspect':
                result = rows
            else:
                with Archive(args.database) as archive:
                    result = dict(capture_id=archive.save(args.source, raw, rows), rows=len(rows))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Blocked as error:
        print(json.dumps(dict(error='Blocked', reason=str(error)), ensure_ascii=False), file=sys.stderr)
        return 2
    except Exception as error:
        print(json.dumps(dict(error=type(error).__name__, reason=str(error)), ensure_ascii=False),
              file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

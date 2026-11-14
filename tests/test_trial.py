import json
import os
import tempfile
import unittest
from pathlib import Path

from sampledata import samples
from registry import Registry
from planner import build_plan, bind_auth
from transport import ReplaySource
from trial import run_trial, run_approved_trials, TrialConfigError


def make_registry():
    directory = tempfile.TemporaryDirectory()
    for name, raw in samples().items():
        path = Path(directory.name) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return directory, Path(directory.name)


class TrialTests(unittest.TestCase):
    def setUp(self):
        self.directory, self.root = make_registry()
        self.routes = {
            'GET /v1/items?ids=a,b&tenant=main': [dict(
                status=200,
                text=json.dumps({'items': [{'id': 'a', 'name': 'alpha',
                                            'category': {'code': 'c1', 'label': None},
                                            'tags': ['x', None]}], 'next': None}),
                headers={'Content-Type': 'application/json'},
                require_headers={'X-Key': 'local-synthetic-key'})],
            'GET /v1/items?ids=bad&tenant=main': [dict(
                status=200, text=json.dumps({'items': [{'id': 'b'}]}),
                headers={'Content-Type': 'application/json'},
                require_headers={'X-Key': 'local-synthetic-key'})],
            'GET /v1/items?ids=empty&tenant=main': [dict(
                status=200, text='not-json',
                headers={'Content-Type': 'application/json'},
                require_headers={'X-Key': 'local-synthetic-key'})],
            'GET /v1/items?ids=xml&tenant=main': [dict(
                status=200, text='<x/>', headers={'Content-Type': 'application/xml'},
                require_headers={'X-Key': 'local-synthetic-key'})],
            'POST /v1/items': [dict(status=405, text='未批准写操作')],
        }
        self.server_ctx = ReplaySource(self.routes)
        self.server = self.server_ctx.__enter__()
        self.loopback = self.server.url + '/v1'
        doc_path = self.root / 'supplier-a' / '1.json'
        document = json.loads(doc_path.read_text(encoding='utf-8'))
        for server in document['servers']:
            if server['url'] == 'http://127.0.0.1:0/v1':
                server['url'] = self.loopback
        doc_path.write_text(json.dumps(document), encoding='utf-8')
        self.registry = Registry.from_directory(self.root)
        self.plan = bind_auth(build_plan('supplier-a/1.json', self.registry),
                              {'apiKeyHeader': 'SUPPLIER_A_KEY'})
        os.environ['SUPPLIER_A_KEY'] = 'local-synthetic-key'

    def tearDown(self):
        self.server_ctx.__exit__(None, None, None)
        self.directory.cleanup()
        os.environ.pop('SUPPLIER_A_KEY', None)

    def test_approved_read_only_trial_passes_schema_validation(self):
        report = run_approved_trials(self.plan, [{
            'operationId': 'listItems', 'server': self.loopback,
            'inputs': {'ids': ['a', 'b'], 'tenant': 'main'}}])
        self.assertEqual(report['executed_count'], 1)
        self.assertTrue(report['onboarding_complete'])
        self.assertTrue(report['results'][0]['ok'])
        request = self.server.requests[-1]
        self.assertEqual(request['method'], 'GET')
        self.assertEqual(request['headers']['X-Key'], 'local-synthetic-key')

    def test_http_200_with_bad_structure_fails_onboarding(self):
        report = run_approved_trials(self.plan, [{
            'operationId': 'listItems', 'server': self.loopback,
            'inputs': {'ids': ['bad'], 'tenant': 'main'}}])
        self.assertFalse(report['onboarding_complete'])
        result = report['results'][0]
        self.assertEqual(result['http_status'], 200)
        self.assertEqual(result['validation']['reason'], 'SCHEMA_MISMATCH')
        self.assertIn('缺少必填字段', result['validation']['schema_errors'][0])

    def test_invalid_json_and_undeclared_content_type_fail(self):
        for ids, reason in (('empty', 'INVALID_JSON'), ('xml', 'UNDECLARED_CONTENT_TYPE')):
            result = run_trial(self.plan, {
                'operationId': 'listItems', 'server': self.loopback,
                'inputs': {'ids': [ids], 'tenant': 'main'}})
            self.assertFalse(result['ok'])
            self.assertEqual(result['validation']['reason'], reason)

    def test_write_operation_never_called_even_with_approval_entry(self):
        result = run_trial(self.plan, {
            'operationId': 'createItem', 'server': self.loopback})
        self.assertFalse(result['executed'])
        self.assertIn('NOT_READ_ONLY', result['block_reason'])
        self.assertEqual([r for r in self.server.requests if r['method'] == 'POST'], [])

    def test_server_must_match_declared_url_exactly(self):
        result = run_trial(self.plan, {
            'operationId': 'listItems',
            'server': self.server.url + '/other-version',
            'inputs': {'ids': ['a', 'b']}})
        self.assertFalse(result['executed'])
        self.assertIn('SERVER_NOT_APPROVED', result['block_reason'])

    def test_non_loopback_target_refused(self):
        from trial import _assert_loopback, TrialConfigError
        with self.assertRaises(TrialConfigError):
            _assert_loopback('http://8.8.8.8/x')
        with self.assertRaises(TrialConfigError):
            _assert_loopback('https://127.0.0.1/x')
        with self.assertRaises(TrialConfigError):
            # 主机名不做 DNS 解析，避免解析到非回环地址
            _assert_loopback('http://supplier.example.test/x')
        # 合法回环放行
        _assert_loopback('http://127.0.0.1:8080/x')
        _assert_loopback('http://localhost/x')

    def test_query_apikey_scheme_not_allowed_for_trial(self):
        document = {
            'openapi': '3.1.0', 'info': {'title': 'Q', 'version': '1'},
            'servers': [{'url': self.loopback}],
            'security': [{'qk': []}],
            'components': {'securitySchemes': {
                'qk': {'type': 'apiKey', 'in': 'query', 'name': 'token'}}},
            'paths': {'/q': {'get': {'operationId': 'q-op',
                                     'responses': {'200': {'description': 'ok'}}}}}}
        (self.root / 'qkey.json').write_text(json.dumps(document))
        registry = Registry.from_directory(self.root)
        plan = bind_auth(build_plan('qkey.json', registry), {'qk': 'QK_ENV'})
        # 计划层面该方案不被试采支持
        member = plan['operations'][0]['security'][0]['members'][0]
        self.assertFalse(member['supported'])
        result = run_trial(plan, {'operationId': 'q-op', 'server': self.loopback})
        self.assertFalse(result['executed'])
        # 计划层阻塞优先短路，阻塞项中含不支持的 query apiKey 诊断
        self.assertIn('PLAN_BLOCKED', result['block_reason'])
        self.assertIn('UNSUPPORTED_SECURITY', json.dumps(result['blockers']))

    def test_missing_required_input_blocks(self):
        result = run_trial(self.plan, {
            'operationId': 'listItems', 'server': self.loopback,
            'inputs': {'tenant': 'main'}})
        self.assertIn('缺少必填参数', result['block_reason'])

    def test_undeclared_input_blocks(self):
        result = run_trial(self.plan, {
            'operationId': 'listItems', 'server': self.loopback,
            'inputs': {'ids': ['a'], 'tenant': 'main', 'injected': 'x'}})
        self.assertIn('未声明的参数', result['block_reason'])

    def test_missing_credential_environment_blocks_without_request(self):
        # 未绑定 env 名：计划阶段即拒绝，不发请求
        unbound = build_plan('supplier-a/1.json', self.registry)
        result = run_trial(unbound, {
            'operationId': 'listItems', 'server': self.loopback,
            'inputs': {'ids': ['a', 'b'], 'tenant': 'main'}})
        self.assertFalse(result['executed'])
        self.assertIn('AUTH_NOT_BOUND', result['block_reason'])
        # 已绑定但环境变量缺失/为空：同样阻塞，且不产生新请求
        requests_before = len(self.server.requests)
        os.environ.pop('SUPPLIER_A_KEY')
        result = run_trial(self.plan, {
            'operationId': 'listItems', 'server': self.loopback,
            'inputs': {'ids': ['a', 'b'], 'tenant': 'main'}})
        self.assertFalse(result['executed'])
        self.assertIn('授权环境变量未提供值', result['block_reason'])
        os.environ['SUPPLIER_A_KEY'] = ''
        result = run_trial(self.plan, {
            'operationId': 'listItems', 'server': self.loopback,
            'inputs': {'ids': ['a', 'b'], 'tenant': 'main'}})
        self.assertFalse(result['executed'])
        self.assertEqual(len(self.server.requests), requests_before)

    def test_operation_with_plan_blockers_never_tried(self):
        blocked_plan = build_plan('supplier-b/1.json', self.registry)
        result = run_trial(blocked_plan, {
            'operationId': 'matrixQuery', 'server': 'http://127.0.0.1:0/api'})
        self.assertFalse(result['executed'])
        self.assertIn('PLAN_BLOCKED', result['block_reason'])

    def test_report_contains_no_secret_values(self):
        report = run_approved_trials(self.plan, [{
            'operationId': 'listItems', 'server': self.loopback,
            'inputs': {'ids': ['a', 'b'], 'tenant': 'main'}}])
        self.assertNotIn('local-synthetic-key', json.dumps(report))

    def test_unknown_operation_reported_not_executed(self):
        result = run_trial(self.plan, {
            'operationId': 'ghost', 'server': self.loopback})
        self.assertIn('NOT_IN_PLAN', result['block_reason'])

    def test_path_parameter_trial_and_missing_template_value(self):
        self.routes['GET /v1/items/abc'] = [dict(
            status=200,
            text=json.dumps({'id': 'abc', 'name': 'alpha'}),
            headers={'Content-Type': 'application/json'},
            require_headers={'X-Key': 'local-synthetic-key'})]
        result = run_trial(self.plan, {
            'operationId': 'getItem', 'server': self.loopback,
            'inputs': {'itemId': 'abc'}})
        self.assertTrue(result['executed'], result.get('block_reason'))
        self.assertTrue(result['ok'])
        self.assertEqual(self.server.requests[-1]['path'], '/v1/items/abc')
        # 缺少必填路径参数：阻塞且不发请求
        missing = run_trial(self.plan, {
            'operationId': 'getItem', 'server': self.loopback})
        self.assertFalse(missing['executed'])
        self.assertIn('缺少必填参数', missing['block_reason'])
        # 路径值中的特殊字符被百分号编码
        encoded = run_trial(self.plan, {
            'operationId': 'getItem', 'server': self.loopback,
            'inputs': {'itemId': 'a/b'}})
        # 路由未登记会返回 404，但 URL 编码本身已发生
        self.assertTrue(encoded['executed'])
        self.assertEqual(self.server.requests[-1]['path'], '/v1/items/a%2Fb')

    def test_cookie_apikey_and_cookie_parameter_merged_in_one_header(self):
        document = {
            'openapi': '3.1.0', 'info': {'title': 'C', 'version': '1'},
            'servers': [{'url': self.loopback}],
            'security': [{'sessionCookie': []}],
            'components': {'securitySchemes': {
                'sessionCookie': {'type': 'apiKey', 'in': 'cookie', 'name': 'session'}}},
            'paths': {'/c': {'get': {'operationId': 'cookie-op', 'parameters': [
                {'name': 'tracking', 'in': 'cookie',
                 'schema': {'type': 'string'}}],
                'responses': {'200': {'description': 'ok', 'content': {
                    'application/json': {'schema': {
                        'type': 'object', 'required': ['ok'],
                        'properties': {'ok': {'type': 'boolean'}}}}}}}}}},
        }
        (self.root / 'cookie.json').write_text(json.dumps(document))
        registry = Registry.from_directory(self.root)
        plan = bind_auth(build_plan('cookie.json', registry),
                         {'sessionCookie': 'SESSION_ENV'})
        os.environ['SESSION_ENV'] = 'session-secret'
        self.routes['GET /v1/c'] = [dict(
            status=200, text=json.dumps({'ok': True}),
            headers={'Content-Type': 'application/json'})]
        result = run_trial(plan, {
            'operationId': 'cookie-op', 'server': self.loopback,
            'inputs': {'tracking': 't1'}})
        self.assertTrue(result['executed'], result.get('block_reason'))
        self.assertTrue(result['ok'])
        cookie_header = self.server.requests[-1]['headers']['Cookie']
        self.assertIn('session=session-secret', cookie_header)
        self.assertIn('tracking=t1', cookie_header)
        os.environ.pop('SESSION_ENV', None)


if __name__ == '__main__':
    unittest.main()

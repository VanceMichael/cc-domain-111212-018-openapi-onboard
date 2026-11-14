import json
import tempfile
import unittest
from pathlib import Path

from sampledata import samples
from registry import Registry, RegistryError
from planner import build_plan, bind_auth, Blocked, render_query, render_path


def make_registry():
    directory = tempfile.TemporaryDirectory()
    for name, raw in samples().items():
        path = Path(directory.name) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return directory, Registry.from_directory(directory.name)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.directory, self.registry = make_registry()

    def tearDown(self):
        self.directory.cleanup()

    def test_allowed_relative_closure_resolves(self):
        key, pointer, node = self.registry.resolve(
            '../shared/external.json#/Category', 'supplier-a/1.json')
        self.assertEqual(key, 'shared/external.json')
        self.assertEqual(pointer, '/Category')
        self.assertEqual(node['required'], ['code'])
        self.assertIn('../shared/external.json#/Category',
                      self.registry.closure('supplier-a/1.json'))

    def test_external_and_absolute_refs_rejected(self):
        for ref in ('https://x.example.test/s.json#/R', '//x.example.test/s.json',
                    '/etc/passwd.json', '../outside.json#/R'):
            with self.assertRaises(RegistryError):
                self.registry.resolve(ref, 'supplier-a/1.json')

    def test_bad_pointer_diagnosed(self):
        with self.assertRaises(RegistryError):
            self.registry.resolve('#/components/schemas/Missing', 'supplier-a/1.json')

    def test_unknown_document_rejected(self):
        with self.assertRaises(RegistryError):
            Registry.from_directory(tempfile.mkdtemp())

    def test_path_traversal_key_rejected_at_registration(self):
        for bad in ('/etc/passwd.json', '../escape.json', 'a/../../b.json',
                    'file:///x.json'):
            with self.assertRaises(RegistryError):
                self.registry.add(bad, {'openapi': '3.1.0'})

    def test_pointer_array_bounds_and_scalar_penetration(self):
        with self.assertRaises(RegistryError):
            self.registry.resolve('#/servers/9', 'supplier-a/1.json')
        with self.assertRaises(RegistryError):
            self.registry.resolve('#/info/title/x', 'supplier-a/1.json')


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.directory, self.registry = make_registry()

    def tearDown(self):
        self.directory.cleanup()

    def test_unsupported_openapi_version_blocks_hard(self):
        with self.assertRaises(Blocked):
            build_plan('invalid.json', self.registry)

    def test_plan_is_deterministic(self):
        first = json.dumps(build_plan('supplier-a/1.json', self.registry),
                           sort_keys=True, ensure_ascii=False)
        _, second_registry = make_registry()
        second = json.dumps(build_plan('supplier-a/1.json', second_registry),
                            sort_keys=True, ensure_ascii=False)
        self.assertEqual(first, second)

    def test_path_and_operation_parameters_merged_with_override(self):
        plan = build_plan('supplier-a/1.json', self.registry)
        operation = next(o for o in plan['operations'] if o['operation_id'] == 'listItems')
        names = {p['name']: p for p in operation['parameters']}
        self.assertEqual(set(names), {'ids', 'tenant'})
        # operation 级 tenant 覆盖 path 级（operation 声明带 enum）
        self.assertIn('enum', names['tenant']['shape'])
        self.assertTrue(names['ids']['required'])
        self.assertEqual(names['ids']['style'], 'form')
        self.assertFalse(names['ids']['explode'])

    def test_nullable_and_type_arrays_flattened(self):
        plan = build_plan('supplier-a/1.json', self.registry)
        page = next(o for o in plan['operations'] if o['operation_id'] == 'listItems')
        shape = page['responses'][0]['content'][0]['shape']
        item_shape = shape['properties']['items']['items']
        self.assertEqual(item_shape['properties']['tags']['items']['types'],
                         ['null', 'string'])
        self.assertEqual(shape['properties']['next']['types'], ['null', 'string'])

    def test_3_0_nullable_keyword_accepted(self):
        plan = build_plan('supplier-b/1.json', self.registry)
        search = next(o for o in plan['operations'] if o['operation_id'] == 'search')
        region = next(p for p in search['parameters'] if p['name'] == 'region')
        self.assertEqual(region['shape']['types'], ['null', 'string'])

    def test_indeterminate_union_blocks_request_shape(self):
        doc = {
            'openapi': '3.1.0', 'info': {'title': 'U', 'version': '1'},
            'servers': [{'url': 'http://127.0.0.1:9'}],
            'paths': {'/u': {'get': {'operationId': 'u', 'parameters': [
                {'name': 'm', 'in': 'query', 'schema': {'oneOf': [
                    {'type': 'string'}, {'type': 'integer'}]}}],
                'responses': {'200': {'description': 'ok'}}}}}}
        self.registry.add('u.json', doc)
        plan = build_plan('u.json', self.registry)
        operation = plan['operations'][0]
        self.assertFalse(operation['trial_eligible'])
        self.assertIn('INDETERMINATE_UNION', [b['code'] for b in operation['blockers']])

    def test_supplier_b_every_operation_blocked_and_catalogued(self):
        plan = build_plan('supplier-b/1.json', self.registry)
        self.assertEqual(plan['trial_eligible_operations'], [])
        codes = {b['code'] for b in plan['blockers']}
        self.assertIn('UNRESOLVABLE_REFERENCE', codes)   # 外链
        self.assertIn('UNSUPPORTED_STYLE', codes)        # label / matrix
        self.assertIn('UNSUPPORTED_SCHEMA', codes)       # allOf / additionalProperties
        self.assertIn('UNSUPPORTED_MEDIA_TYPE', codes)   # application/xml
        self.assertIn('UNSUPPORTED_SECURITY', codes)     # oauth2

    def test_only_read_only_unblocked_operations_eligible(self):
        plan = build_plan('supplier-a/1.json', self.registry)
        create = next(o for o in plan['operations'] if o['operation_id'] == 'createItem')
        self.assertFalse(create['read_only'])
        self.assertFalse(create['trial_eligible'])

    def test_auth_binding_records_env_names_only(self):
        plan = build_plan('supplier-a/1.json', self.registry)
        bound = bind_auth(plan, {'apiKeyHeader': 'SUPPLIER_A_KEY'})
        member = bound['operations'][0]['security'][0]['members'][0]
        self.assertEqual(member['env'], 'SUPPLIER_A_KEY')
        exported = json.dumps(bound)
        self.assertNotIn('local-synthetic-key', exported)
        # 原计划不被修改
        self.assertIsNone(plan['operations'][0]['security'][0]['members'][0]['env'])

    def test_multiple_servers_kept_and_not_auto_selected(self):
        plan = build_plan('supplier-a/1.json', self.registry)
        operation = plan['operations'][0]
        self.assertEqual(len(operation['servers']), 2)

    def test_query_and_path_rendering_styles(self):
        array_param = {'name': 'ids', 'in': 'query', 'style': 'form', 'explode': False}
        self.assertEqual(render_query(array_param, ['a', 'b'])[0], 'ids=a,b')
        pipe = {'name': 'ids', 'in': 'query', 'style': 'pipeDelimited', 'explode': False}
        self.assertEqual(render_query(pipe, ['a', 'b'])[0], 'ids=a|b')
        space = {'name': 'ids', 'in': 'query', 'style': 'spaceDelimited', 'explode': False}
        self.assertEqual(render_query(space, ['a', 'b'])[0], 'ids=a%20b')
        deep = {'name': 'f', 'in': 'query', 'style': 'deepObject', 'explode': True}
        self.assertEqual(render_query(deep, {'k': 'v'})[0], 'f[k]=v')
        path_param = {'name': 'id', 'in': 'path', 'style': 'simple', 'explode': False}
        self.assertEqual(render_path(path_param, 'a/b'), 'a%2Fb')
        # 空格在 pipe 分隔的值内仍被转义，避免歧义
        self.assertEqual(render_query(pipe, ['a b'])[0], 'ids=a%20b')

    def test_request_body_ambiguous_media_types_block(self):
        doc = {
            'openapi': '3.1.0', 'info': {'title': 'M', 'version': '1'},
            'paths': {'/m': {'post': {'operationId': 'm', 'requestBody': {'content': {
                'application/json': {'schema': {'type': 'object'}},
                'application/x-www-form-urlencoded': {'schema': {'type': 'object'}}}},
                'responses': {'201': {'description': 'ok'}}}}}}
        self.registry.add('m.json', doc)
        plan = build_plan('m.json', self.registry)
        self.assertIn('AMBIGUOUS_MEDIA_TYPE',
                      [b['code'] for b in plan['operations'][0]['blockers']])

    def test_parameter_and_pathitem_refs_resolve_inside_closure(self):
        doc = {
            'openapi': '3.1.0', 'info': {'title': 'X', 'version': '1'},
            'servers': [{'url': 'http://127.0.0.1:1/x'}],
            'paths': {'/p': {'$ref': '#/components/pathItems/P'}},
            'components': {
                'pathItems': {'P': {'get': {
                    'operationId': 'p',
                    'parameters': [{'$ref': '#/components/parameters/Q'}],
                    'responses': {'200': {'description': 'ok', 'content': {
                        'application/json': {'schema': {
                            'type': 'object',
                            'properties': {'id': {'type': 'string'}}}}}}}}}},
                'parameters': {'Q': {'name': 'q', 'in': 'query',
                                     'schema': {'type': 'string'}}}},
        }
        self.registry.add('xref.json', doc)
        plan = build_plan('xref.json', self.registry)
        operation = plan['operations'][0]
        self.assertEqual(operation['parameters'][0]['name'], 'q')
        self.assertTrue(operation['trial_eligible'])

    def test_recursive_schema_blocks_trial_with_manual_mapping_diagnostic(self):
        doc = {
            'openapi': '3.1.0', 'info': {'title': 'R', 'version': '1'},
            'servers': [{'url': 'http://127.0.0.1:1/r'}],
            'paths': {'/r': {'get': {'operationId': 'r', 'responses': {'200': {
                'description': 'ok', 'content': {'application/json': {'schema': {
                    '$ref': '#/components/schemas/Node'}}}}}}}},
            'components': {'schemas': {'Node': {'type': 'object', 'properties': {
                'id': {'type': 'string'},
                'child': {'$ref': '#/components/schemas/Node'}}}}},
        }
        self.registry.add('rec.json', doc)
        plan = build_plan('rec.json', self.registry)
        operation = plan['operations'][0]
        self.assertFalse(operation['trial_eligible'])
        self.assertIn('UNVALIDATABLE_SCHEMA', [b['code'] for b in operation['blockers']])

    def test_head_is_read_only_and_bearer_alternative_supported(self):
        doc = {
            'openapi': '3.1.0', 'info': {'title': 'H', 'version': '1'},
            'servers': [{'url': 'http://127.0.0.1:1/h'}],
            'security': [{'k1': []}, {'bearer': []}],
            'components': {'securitySchemes': {
                'k1': {'type': 'apiKey', 'in': 'header', 'name': 'X-1'},
                'bearer': {'type': 'http', 'scheme': 'bearer'}}},
            'paths': {'/h': {'head': {'operationId': 'head-h',
                                      'responses': {'200': {'description': 'ok'}}}}}}
        self.registry.add('head.json', doc)
        bound = bind_auth(build_plan('head.json', self.registry), {'bearer': 'BEARER_ENV'})
        operation = bound['operations'][0]
        self.assertTrue(operation['read_only'])
        self.assertTrue(operation['trial_eligible'])
        self.assertEqual(operation['security'][1]['members'][0]['env'], 'BEARER_ENV')

    def test_unregistered_relative_ref_is_blocker_not_crash(self):
        doc = {
            'openapi': '3.1.0', 'info': {'title': 'Z', 'version': '1'},
            'paths': {'/z': {'get': {'operationId': 'z', 'responses': {'200': {
                'description': 'ok', 'content': {'application/json': {'schema': {
                    '$ref': 'missing.json#/R'}}}}}}}}}
        self.registry.add('z.json', doc)
        plan = build_plan('z.json', self.registry)
        codes = [b['code'] for b in plan['operations'][0]['blockers']]
        self.assertIn('UNRESOLVABLE_REFERENCE', codes)
        self.assertEqual(plan['references'], [])


if __name__ == '__main__':
    unittest.main()

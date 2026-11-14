import tempfile
import unittest
from pathlib import Path

from sampledata import samples
from registry import Registry
from planner import build_plan
from plandiff import diff_plans


def make_registry():
    directory = tempfile.TemporaryDirectory()
    for name, raw in samples().items():
        path = Path(directory.name) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return directory, Registry.from_directory(directory.name)


class PlanDiffTests(unittest.TestCase):
    def setUp(self):
        self.directory, self.registry = make_registry()
        self.old = build_plan('supplier-a/1.json', self.registry)
        self.new = build_plan('supplier-a/2.json', self.registry)
        self.delta = diff_plans(self.old, self.new)

    def tearDown(self):
        self.directory.cleanup()

    def test_summary_flags_impact_to_existing_source(self):
        summary = self.delta['summary']
        self.assertTrue(summary['affects_existing_source'])
        self.assertEqual(summary['operations_added'], 1)
        self.assertGreaterEqual(summary['breaking_changes'], 1)

    def test_field_type_and_required_changes_detected(self):
        items = next(o for o in self.delta['operations']
                     if o['path'] == '/items' and o['method'] == 'GET')
        changes = items['changes']
        locations = {c['location']: c for c in changes}
        # 请求编码变化
        style_change = next(c for c in changes if c['change'] == 'style')
        self.assertTrue(style_change['breaking'])
        self.assertEqual((style_change['from'], style_change['to']),
                         ('form', 'pipeDelimited'))
        # 必填性变化
        required_change = next(c for c in changes if c['change'] == 'required')
        self.assertTrue(required_change['breaking'])
        # id 类型 string -> integer
        type_changes = [c for c in changes if c['change'] == 'type']
        self.assertTrue(any(c['from'] == ['string'] and c['to'] == ['integer']
                            for c in type_changes))
        # 新增必填 owner
        owner = locations.get('responses/200/application/json.shape.items[].owner')
        self.assertIsNotNone(owner)
        self.assertTrue(owner['breaking'])

    def test_security_alternative_change_is_breaking(self):
        items = next(o for o in self.delta['operations']
                     if o['path'] == '/items' and o['method'] == 'GET')
        security = next(c for c in items['changes'] if c['change'] == 'security_alternatives')
        self.assertTrue(security['breaking'])
        # 新版本含 bearer 备选
        flattened = repr(security['to'])
        self.assertIn('bearerAuth', flattened)

    def test_new_operation_is_non_breaking(self):
        vendors = next(o for o in self.delta['operations'] if o['path'] == '/vendors')
        self.assertEqual(vendors['change'], 'operation_added')
        self.assertFalse(vendors['breaking'])

    def test_identical_plans_have_no_changes(self):
        delta = diff_plans(self.old, build_plan('supplier-a/1.json', self.registry))
        self.assertEqual(delta['summary']['field_changes'], 0)
        self.assertFalse(delta['summary']['affects_existing_source'])

    def test_nullability_change_detected(self):
        # next 由 [string,null] 变为 [string]
        items = next(o for o in self.delta['operations']
                     if o['path'] == '/items' and o['method'] == 'GET')
        next_change = next(c for c in items['changes']
                           if c['location'] == 'responses/200/application/json.shape.next'
                           and c['change'] == 'type')
        self.assertTrue(next_change['breaking'])


if __name__ == '__main__':
    unittest.main()

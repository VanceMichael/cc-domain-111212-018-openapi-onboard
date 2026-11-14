import unittest

from validator import validate_shape, validate_response


class ValidatorTests(unittest.TestCase):
    def test_required_and_nested_properties(self):
        shape = {'kind': 'object', 'required': ['id'],
                 'properties': {'id': {'kind': 'type', 'types': ['string']},
                                'child': {'kind': 'object', 'required': [],
                                          'properties': {'n': {'kind': 'type', 'types': ['integer']}}}}}
        self.assertEqual(validate_shape(shape, {'id': 'x', 'child': {'n': 1}}), [])
        errors = validate_shape(shape, {'child': {'n': 'bad'}})
        self.assertTrue(any('缺少必填字段' in e for e in errors))
        errors = validate_shape(shape, {'id': 'x', 'child': {'n': 'bad'}})
        self.assertTrue(any('期望类型' in e for e in errors))

    def test_nullable_union_accepts_null(self):
        shape = {'kind': 'type', 'types': ['null', 'string']}
        self.assertEqual(validate_shape(shape, None), [])
        self.assertEqual(validate_shape(shape, 'x'), [])
        self.assertTrue(validate_shape(shape, 1))

    def test_one_of_requires_exactly_one_branch(self):
        shape = {'kind': 'union', 'combinator': 'oneOf', 'variants': [
            {'kind': 'type', 'types': ['string']},
            {'kind': 'type', 'types': ['integer']}]}
        self.assertEqual(validate_shape(shape, 'x'), [])
        self.assertEqual(validate_shape(shape, 1), [])
        self.assertTrue(validate_shape(shape, True))  # boolean 不匹配任何分支

    def test_boolean_not_counted_as_integer(self):
        shape = {'kind': 'type', 'types': ['integer']}
        self.assertTrue(validate_shape(shape, True))
        self.assertEqual(validate_shape(shape, 3), [])

    def test_enum_and_array_items(self):
        shape = {'kind': 'array',
                 'items': {'kind': 'type', 'types': ['string']}}
        self.assertEqual(validate_shape(shape, ['a', 'b']), [])
        self.assertTrue(validate_shape(shape, ['a', 1]))

    def test_response_uses_actual_status_and_content_type(self):
        operation = {'responses': [
            {'status': '200', 'content': [
                {'media_type': 'application/json',
                 'shape': {'kind': 'object', 'required': ['items'],
                           'properties': {'items': {'kind': 'array',
                                                    'items': {'kind': 'type', 'types': ['string']}}}}}]},
            {'status': '404', 'content': [
                {'media_type': 'application/json',
                 'shape': {'kind': 'object', 'required': ['code'],
                           'properties': {'code': {'kind': 'type', 'types': ['string']}}}}]}]}
        ok, diagnosis = validate_response(
            operation, 200, {'Content-Type': 'application/json'},
            b'{"items": ["a"]}')
        self.assertTrue(ok)
        ok, diagnosis = validate_response(
            operation, 200, {'Content-Type': 'application/json'},
            b'{"items": [1]}')
        self.assertFalse(ok)
        self.assertEqual(diagnosis['reason'], 'SCHEMA_MISMATCH')
        ok, diagnosis = validate_response(
            operation, 404, {'Content-Type': 'application/json'}, b'{"code":"missing"}')
        self.assertTrue(ok)
        ok, diagnosis = validate_response(
            operation, 500, {'Content-Type': 'application/json'}, b'{}')
        self.assertFalse(ok)
        self.assertEqual(diagnosis['reason'], 'UNDECLARED_STATUS')
        ok, diagnosis = validate_response(
            operation, 200, {'Content-Type': 'text/csv'}, b'a,b')
        self.assertFalse(ok)
        self.assertEqual(diagnosis['reason'], 'UNDECLARED_CONTENT_TYPE')

    def test_default_response_is_fallback(self):
        operation = {'responses': [
            {'status': 'default', 'content': [
                {'media_type': 'application/json',
                 'shape': {'kind': 'object', 'required': ['code'],
                           'properties': {'code': {'kind': 'type', 'types': ['string']}}}}]}]}
        ok, _ = validate_response(operation, 503, {'Content-Type': 'application/json'},
                                  b'{"code":"down"}')
        self.assertTrue(ok)

    def test_no_content_response_not_structured(self):
        operation = {'responses': [{'status': '204', 'content': []}]}
        ok, diagnosis = validate_response(operation, 204, {}, b'')
        self.assertTrue(ok)
        self.assertFalse(diagnosis['validated'])


if __name__ == '__main__':
    unittest.main()

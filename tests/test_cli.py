import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

import app
from sampledata import samples
from transport import ReplaySource


class CliWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        for name, raw in samples().items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)

    def tearDown(self):
        self.directory.cleanup()
        os.environ.pop('SUPPLIER_A_KEY', None)

    def _run(self, *argv):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = app.main(list(argv))
        self.assertEqual(code, 0, output.getvalue())
        return json.loads(output.getvalue())

    def _run_error(self, *argv):
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            code = app.main(list(argv))
        self.assertEqual(code, 2)
        return json.loads(error.getvalue())

    def test_plan_diff_and_trial_cli_workflow(self):
        routes = {
            'GET /v1/items?ids=a,b&tenant=main': [dict(
                status=200,
                text=json.dumps({'items': [{'id': 'a', 'name': 'alpha',
                                            'category': {'code': 'c1', 'label': None},
                                            'tags': ['x', None]}], 'next': None}),
                headers={'Content-Type': 'application/json'},
                require_headers={'X-Key': 'local-synthetic-key'})],
            'POST /v1/items': [dict(status=405, text='blocked')],
        }
        with ReplaySource(routes) as server:
            base = server.url + '/v1'
            doc_path = self.root / 'supplier-a' / '1.json'
            document = json.loads(doc_path.read_text(encoding='utf-8'))
            for entry in document['servers']:
                if '127.0.0.1' in entry['url'] or 'localhost' in entry['url']:
                    entry['url'] = base
            doc_path.write_text(json.dumps(document), encoding='utf-8')
            auth_path = self.root / 'auth.json'
            auth_path.write_text(json.dumps({'apiKeyHeader': 'SUPPLIER_A_KEY'}))
            approvals_path = self.root / 'approvals.json'
            approvals_path.write_text(json.dumps([{
                'operationId': 'listItems', 'server': base,
                'inputs': {'ids': ['a', 'b'], 'tenant': 'main'}}]))

            old_plan = self._run('plan', str(self.root), 'supplier-a/1.json',
                                 '--auth-env', str(auth_path))
            new_plan = self._run('plan', str(self.root), 'supplier-a/2.json',
                                 '--auth-env', str(auth_path))
            self.assertNotIn('local-synthetic-key', json.dumps(old_plan))
            old_path = self.root / 'old.json'
            new_path = self.root / 'new.json'
            old_path.write_text(json.dumps(old_plan))
            new_path.write_text(json.dumps(new_plan))
            delta = self._run('diff', str(old_path), str(new_path))
            self.assertTrue(delta['summary']['affects_existing_source'])

            os.environ['SUPPLIER_A_KEY'] = 'local-synthetic-key'
            report = self._run('trial', str(self.root), 'supplier-a/1.json',
                               str(approvals_path), '--auth-env', str(auth_path))
            self.assertTrue(report['onboarding_complete'])
            self.assertNotIn('local-synthetic-key', json.dumps(report))
            self.assertEqual(
                [r for r in server.requests if r['method'] == 'POST'], [])

    def test_plan_hard_block_on_unsupported_version(self):
        error = self._run_error('plan', str(self.root), 'invalid.json')
        self.assertEqual(error['error'], 'Blocked')

    def test_blocked_supplier_plan_exported_with_diagnostics(self):
        plan = self._run('plan', str(self.root), 'supplier-b/1.json')
        self.assertEqual(plan['trial_eligible_operations'], [])
        self.assertTrue(plan['blockers'])

    def test_fixtures_command_never_overwrites(self):
        target = self.root / 'fixtures'
        self._run('fixtures', str(target))
        error = self._run_error('fixtures', str(target))
        self.assertEqual(error['error'], 'ValueError')


if __name__ == '__main__':
    unittest.main()

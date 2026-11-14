"""Check browser API paths against the backend's published route schema."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / 'src/webui/frontend'
sys.path.insert(0, str(ROOT / 'src'))


def normalize(path):
    path = re.sub(r'\$\{[^}]*\}', '{}', path).split('?')[0]
    return re.sub(r'\{[^}]*\}', '{}', path)


class FrontendContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from webui.backend.server import create_app
        cls.app = create_app()
        cls.routes = {
            (method.upper(), normalize(path))
            for path, operations in cls.app.openapi()['paths'].items()
            for method in operations
        }

    def test_shared_api_methods_exist_on_backend(self):
        source = (FRONTEND / 'core/api.js').read_text()
        calls = re.findall(
            r'request\("(GET|POST|PUT|DELETE)",\s*(?:withQuery\()?(["`])(.+?)\2', source
        )
        self.assertTrue(calls, 'Must discover client API calls before checking their routes')
        for method, _, path in calls:
            with self.subTest(method=method, path=path):
                self.assertIn((method, normalize(path)), self.routes)

    def test_serving_api_methods_exist_on_backend(self):
        source=(FRONTEND/'components/lens/lens.js').read_text()
        calls=re.findall(r"request\((['\"`])(/.+?)\1\s*([,)])",source)
        self.assertTrue(calls, 'Must discover frontend API calls before checking their routes')
        for _,path,ending in calls:
            method='POST' if ending==',' else 'GET'
            self.assertIn((method,normalize('/api'+path)),self.routes)

    def test_navigation_routes_and_no_removed_pages(self):
        source=(FRONTEND/'components/rail/rail.html').read_text()
        routes=re.findall(r'data-route="([^"]+)"',source)
        self.assertEqual(routes,['dashboard','screen','factors','strategy-research','backtest','portfolio','trash','settings'])
        app=(FRONTEND/'app.js').read_text()
        self.assertNotIn("'models'",app)
        self.assertNotIn("'assistant'",app.split('initTheme();')[0])

    def test_components_only_call_defined_shared_api_methods(self):
        source = (FRONTEND / 'core/api.js').read_text()
        methods = set(re.findall(r'^\s*(\w+):', source, re.M))
        for file in (FRONTEND / 'components').rglob('*.js'):
            for method in re.findall(r'ctx\.api\.(\w+)\(', file.read_text()):
                with self.subTest(file=str(file.relative_to(FRONTEND)), method=method):
                    self.assertIn(method, methods)

    def test_removed_records_endpoints_and_assets_are_unavailable(self):
        from fastapi.testclient import TestClient

        client = TestClient(self.app)
        self.addCleanup(client.close)
        endpoints = [
            ('POST', '/api/pipeline'),
            ('GET', '/api/tasks/removed'),
            ('GET', '/api/proposals'),
            ('GET', '/api/proposals/removed'),
            ('POST', '/api/proposals/removed/transition'),
            ('GET', '/api/memory/status'),
            ('GET', '/api/memory/candidates'),
            ('POST', '/api/memory/candidates'),
            ('GET', '/api/memory/candidates/removed'),
            ('POST', '/api/memory/candidates/removed/commit'),
            ('POST', '/api/memory/candidates/removed/discard'),
            *[('GET', f'/components/review/review.{ext}') for ext in ('html', 'js', 'css')],
        ]
        for method, path in endpoints:
            with self.subTest(method=method, path=path):
                kwargs = {'json': {}} if method == 'POST' else {}
                response = client.request(method, path, **kwargs)
                self.assertNotIn((method, normalize(path)), self.routes)
                self.assertEqual(response.status_code, 404, response.text)

    def test_local_frontend_assets_exist(self):
        for file in FRONTEND.rglob('*'):
            if file.suffix not in {'.js', '.html'}:
                continue
            source = file.read_text()
            paths = re.findall(r'(?:from\s+|import\()(["\'])([^"\']+)\1', source)
            paths += re.findall(r'(?:src|href)=(["\'])(/[^"\']+)\1', source) if file.suffix == '.html' else []
            for _, path in paths:
                if path.startswith('/'):
                    target = FRONTEND / path.lstrip('/')
                elif path.startswith('.'):
                    target = file.parent / path
                else:
                    continue
                with self.subTest(file=str(file.relative_to(FRONTEND)), asset=path):
                    self.assertTrue(target.is_file(), path)


if __name__ == '__main__':
    unittest.main()

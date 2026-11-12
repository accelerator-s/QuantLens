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
        cls.routes = {
            (method.upper(), normalize(path))
            for path, operations in create_app().openapi()['paths'].items()
            for method in operations
        }

    def test_shared_api_methods_exist_on_backend(self):
        source = (FRONTEND / 'core/api.js').read_text()
        calls = re.findall(
            r'request\("(GET|POST|PUT|DELETE)",\s*(?:withQuery\()?(["`])(.+?)\2', source
        )
        self.assertGreater(len(calls), 100)
        for method, _, path in calls:
            with self.subTest(method=method, path=path):
                self.assertIn((method, normalize(path)), self.routes)

    def test_lens_api_methods_exist_on_backend(self):
        source = (FRONTEND / 'components/lens/lens.js').read_text()
        calls = re.findall(r"\bapi\((['\"`])(/.+?)\1\s*([,)])", source)
        self.assertGreater(len(calls), 25)
        for _, path, ending in calls:
            method = 'POST' if ending == ',' else 'GET'
            with self.subTest(method=method, path=path):
                self.assertIn((method, normalize('/api/lens' + path)), self.routes)

    def test_components_only_call_defined_shared_api_methods(self):
        source = (FRONTEND / 'core/api.js').read_text()
        methods = set(re.findall(r'^\s*(\w+):', source, re.M))
        for file in (FRONTEND / 'components').rglob('*.js'):
            for method in re.findall(r'ctx\.api\.(\w+)\(', file.read_text()):
                with self.subTest(file=str(file.relative_to(FRONTEND)), method=method):
                    self.assertIn(method, methods)

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

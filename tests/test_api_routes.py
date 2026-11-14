"""Keep the shipped client's API calls aligned with the registered routes."""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def test_frontend_api_methods_use_registered_routes():
    from webui.backend.server import create_app
    paths = create_app().openapi()['paths']
    def shape(path):
        return re.sub(r'\{[^}]*\}', '{}', path)
    registered = {(method.upper(), shape(path)) for path, methods in paths.items() for method in methods}
    source = (ROOT / 'src/webui/frontend/core/api.js').read_text()
    calls = re.findall(r'request\("(GET|POST|PUT|DELETE)",\s*(?:withQuery\()?(["`])(/api/[^"`]+)\2', source)
    assert calls, 'No client API calls were discovered'
    for method, _, path in calls:
        normalized = shape(re.sub(r'\$\{[^}]*\}', '{}', path).split('?')[0])
        assert (method, normalized) in registered, (method, path)


def test_evolution_entry_requires_only_a_factor_and_routes_models_on_the_server():
    from unittest.mock import patch
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from webui.backend.routes import factor_evolution

    app = FastAPI()
    app.include_router(factor_evolution.router, prefix='/api')
    with TestClient(app) as client, patch.object(factor_evolution, 'start_factor_evolution', return_value={'research_id':'E-test'}) as start:
        response = client.post('/api/factor-evolution', json={'base_factor':'probe'})
        assert response.status_code == 200 and response.json()['research_id'] == 'E-test'
        start.assert_called_once_with('probe')
        start.reset_mock()
        assert client.post('/api/factor-evolution', json={'base_factor':'probe','model_name':'manual-model'}).status_code == 422
        assert client.post('/api/factor-evolution', json={'base_factor':''}).status_code == 422
        assert client.post('/api/factor-evolution/batch', json={'base_factors':['probe']}).status_code in {404,405}
        start.assert_not_called()

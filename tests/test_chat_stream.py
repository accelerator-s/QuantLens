"""Real provider SSE decoding and downstream streaming contracts."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ai.transport import stream_completion
from exceptions import ModelRouteError
from webui.backend.ai_chat import chat_stream, _SignatureFilter


def model(protocol='openai'):
    return SimpleNamespace(name='test', api_type=protocol, api_model_id='test-model', provider_endpoint='https://models.test')


def sse(*events):
    return ''.join('data: ' + (event if isinstance(event, str) else json.dumps(event)) + '\n\n' for event in events)


def test_openai_deltas_arrive_before_provider_finishes_and_connection_closes():
    closed = []
    class Body(httpx.SyncByteStream):
        def __iter__(self):
            yield sse({'choices': [{'delta': {'reasoning_content': '思考'}}]}).encode()
            closed.append('second-chunk')
            yield sse({'choices': [{'delta': {'content': '回答'}}]}, '[DONE]').encode()

        def close(self):
            closed.append('closed')

    def handler(request):
        assert json.loads(request.content)['stream'] is True
        assert request.url.path == '/v1/chat/completions'
        return httpx.Response(200, stream=Body())

    with patch('ai.transport.create_http_client', side_effect=lambda **_: httpx.Client(transport=httpx.MockTransport(handler))):
        iterator = stream_completion(model(), 'secret', [])
        assert next(iterator) == {'channel': 'reasoning', 'text': '思考'}
        assert closed == []  # First token is available before the provider finishes.
        assert list(iterator) == [{'channel': 'content', 'text': '回答'}]
    assert closed == ['second-chunk', 'closed']


def test_anthropic_stream_skips_signatures_and_redacted_blocks():
    body = sse(
        {'type': 'content_block_delta', 'delta': {'type': 'thinking_delta', 'thinking': '思考'}},
        {'type': 'content_block_delta', 'delta': {'type': 'signature_delta', 'signature': 'private'}},
        {'type': 'content_block_start', 'content_block': {'type': 'redacted_thinking', 'data': 'private'}},
        {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': '回答'}},
        {'type': 'message_stop'},
    )
    with patch('ai.transport.create_http_client', side_effect=lambda **_: httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body)))):
        assert list(stream_completion(model('anthropic'), 'secret', [])) == [
            {'channel': 'reasoning', 'text': '思考'}, {'channel': 'content', 'text': '回答'},
        ]


@pytest.mark.parametrize('status,body', [(200, sse({'choices': [{'delta': {'content': 'partial'}}]})), (200, 'data: not-json\n\n'), (401, 'private-key'), (200, sse({'error': {'message': 'private-key'}}))])
def test_broken_or_invalid_provider_streams_raise_sanitized_errors(status, body):
    with patch('ai.transport.create_http_client', side_effect=lambda **_: httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, text=body)))):
        with pytest.raises(ModelRouteError) as caught:
            list(stream_completion(model(), 'private-key', []))
    assert 'private-key' not in str(caught.value)


def test_signature_tags_split_at_every_boundary_are_never_emitted():
    source = '开始<signature>private\nsignature</signature>结束<signature>unfinished'
    for size in range(1, len(source)):
        scrubber = _SignatureFilter()
        result = ''.join(scrubber.feed(source[i:i + size]) for i in range(0, len(source), size))
        assert result + scrubber.feed('', final=True) == '开始结束'


def test_downstream_stream_has_deltas_done_and_no_full_reply_duplication():
    async def collect(response):
        return [json.loads(chunk) async for chunk in response.body_iterator]
    deltas = [{'channel': 'reasoning', 'text': '思考'}, {'channel': 'content', 'text': '回答'}]
    with patch('ai.transport.stream_completion', return_value=iter(deltas)), patch('webui.backend.ai_chat._api_key_for', return_value='secret'):
        response = chat_stream(SimpleNamespace(request_timeout=20), model(), [])
        events = asyncio.run(collect(response))
    assert [event['type'] for event in events] == ['start', 'delta', 'delta', 'done']
    assert response.headers['x-accel-buffering'] == 'no'
    assert events[1]['text'] == '思考'


def test_downstream_error_is_an_event_and_never_done():
    async def collect(response):
        return [json.loads(chunk) async for chunk in response.body_iterator]
    with patch('ai.transport.stream_completion', side_effect=ModelRouteError(code='TEST', message='模型中断')), patch('webui.backend.ai_chat._api_key_for', return_value='secret'):
        events = asyncio.run(collect(chat_stream(SimpleNamespace(request_timeout=20), model(), [])))
    assert events[-1] == {'type': 'error', 'message': '模型中断'}


@pytest.mark.parametrize('kind,path', [('factor_chat', '/factors/chat'), ('strategy_chat', '/strategies/chat')])
def test_both_chat_routes_use_streaming_and_keep_the_current_conversation(kind, path):
    import importlib
    from contextlib import ExitStack
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    module = importlib.import_module('webui.backend.routes.' + kind)
    app = FastAPI()
    app.include_router(module.router)
    captured = []
    def deltas(selected_router, selected_model, messages):
        captured.extend(messages)
        return chat_stream(selected_router, selected_model, messages)
    with ExitStack() as stack:
        stack.enter_context(patch.object(module, 'AIRouter', return_value=SimpleNamespace(request_timeout=20)))
        stack.enter_context(patch.object(module, 'pick_chat_model' if kind == 'factor_chat' else '_pick_model', return_value=model()))
        if kind == 'factor_chat':
            stack.enter_context(patch.object(module, '_factor_context', return_value='已登记因子'))
        else:
            stack.enter_context(patch.object(module, 'build_static_context', return_value='策略背景'))
        stack.enter_context(patch.object(module, 'chat_stream', side_effect=deltas))
        stack.enter_context(patch('webui.backend.ai_chat._api_key_for', return_value='secret'))
        stack.enter_context(patch('ai.transport.stream_completion', return_value=iter([{'channel': 'content', 'text': '回答'}])))
        response = TestClient(app).post(path, json={'model': 'test', 'messages': [
            {'role': 'user', 'content': '当前对话里的研究问题'},
        ]})
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('application/x-ndjson')
    assert [json.loads(line)['type'] for line in response.text.splitlines()] == ['start', 'delta', 'done']
    assert captured[-1] == {'role': 'user', 'content': '当前对话里的研究问题'}

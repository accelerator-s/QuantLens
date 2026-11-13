"""Check the real chat renderer without AI calls or changing chat files.

Requires the web UI and Chrome CDP (default ports 8610 and 9237).
"""
import json
import os
import time
from urllib.request import Request, urlopen

import websocket

BASE = os.environ.get('QUANTLENS_UI_URL', 'http://127.0.0.1:8610')
CDP = os.environ.get('QUANTLENS_CDP_PORT', '9237')
REPLY = '''这个现象的背景很清楚：中期是“温和向上”，同时日间波动较小，你猜测是资金分批流入、预期还没有被拉满。
我先提示一点，避免重复研究：单纯的“低波动”在已有因子里研究过（60日收益波动，被当作防御性暴露）。所以你这个想法的增量不在“低波”本身，而在**“温和上涨 + 低波”这个组合**——如果只是低波动在起作用，那么去掉“上涨”条件后，效果应该差不多；如果两者缺一不可，才说明是“资金稳步流入、尚未透支”的传导链在起作用。这个区别会决定后面怎么检验。
先问一个问题：你觉得这种优势大概多久能体现出来——是几周内就显现，还是需要持有一个季度左右？'''

page = json.load(urlopen(Request(f'http://127.0.0.1:{CDP}/json/new?about:blank', method='PUT')))
ws = websocket.create_connection(page['webSocketDebuggerUrl'], suppress_origin=True, timeout=10)
serial = 0


def call(method, params=None):
    global serial
    serial += 1
    ws.send(json.dumps({'id': serial, 'method': method, 'params': params or {}}))
    while True:
        response = json.loads(ws.recv())
        if response.get('id') == serial:
            assert 'error' not in response, response
            return response.get('result', {})


def js(expression):
    result = call('Runtime.evaluate', {
        'expression': expression, 'returnByValue': True,
        'awaitPromise': True, 'replMode': True,
    })
    assert 'exceptionDetails' not in result, result
    return result.get('result', {}).get('value')


try:
    call('Page.enable')
    call('Page.navigate', {'url': BASE})
    for _ in range(100):
        if js('location.origin') == BASE.rstrip('/'):
            break
        time.sleep(.05)
    else:
        raise AssertionError('Web UI did not load')

    js("window.md=await import('/components/chat-stream/chat-markdown.js');"
       "window.host=document.createElement('div');")
    js('window.originalReply=' + json.dumps(REPLY))
    result = js('''(() => {
      md.renderAssistant(host, originalReply);
      return {strong:[...host.querySelectorAll('strong')].map(el=>el.textContent),
        text:host.querySelector('.chat__answer').textContent.trim()};
    })()''')
    assert result['strong'] == ['“温和上涨 + 低波”这个组合'], result
    assert result['text'] == REPLY.replace('**', '').replace('\n', ''), result

    checks = js(r'''(() => {
      const checks = {};
      const render = text => md.renderMarkdown(host, text);
      render('这是**“中文”**的内容，**（括号）**也适用。');
      checks.punctuation = host.querySelectorAll('strong').length === 2 && !host.textContent.includes('**');
      render('**粗体 *斜体* 中文**，***粗斜体***，**中文 *斜体***');
      checks.nested = host.querySelectorAll('strong').length === 3 && host.querySelectorAll('em').length === 3;
      render(['`**“代码”**`', '', '```text', '**“代码块”**', '```'].join(String.fromCharCode(10)));
      checks.code = !host.querySelector('strong') && host.querySelectorAll('code').length === 2;
      render('\\*\\*“不加粗”\\*\\*');
      checks.escaped = !host.querySelector('strong') && host.textContent.trim() === '**“不加粗”**';
      render('未完成**“粗体');
      checks.incomplete = !host.querySelector('strong') && host.textContent.trim() === '未完成**“粗体';
      render('** 两边空格 **');
      checks.whitespace = !host.querySelector('strong');
      render('**“公式 $x^{**}$ 与 `**`”**正文');
      checks.inlineMath = host.querySelectorAll('strong .katex').length === 1 && host.querySelector('strong code').textContent === '**';
      render('$$\\sum_{i=1}^n w_i=1$$');
      checks.blockMath = host.querySelectorAll('.chat-math--display .katex').length === 1;
      render('[**“链接”**](https://example.com)');
      checks.link = host.querySelector('a strong').textContent === '“链接”' && host.querySelector('a').rel.includes('noopener');
      render('**“<img src=x onerror=alert(1)>”**');
      checks.html = !host.querySelector('img') && host.querySelector('strong').textContent.includes('<img');
      md.renderAssistant(host, '<think>先看**“温和上涨”**的原因。</think>再检验**“上涨 + 低波”**的组合。');
      checks.thinking = host.querySelector('.chat-thinking strong').textContent === '“温和上涨”' &&
        host.querySelector('.chat__answer strong').textContent === '“上涨 + 低波”';
      return checks;
    })()''')
    assert all(checks.values()), checks
    print(f'PASS: exact reported reply and {len(checks)} Markdown/math/code/thinking checks.')
finally:
    ws.close()
    urlopen(f'http://127.0.0.1:{CDP}/json/close/{page["id"]}').close()

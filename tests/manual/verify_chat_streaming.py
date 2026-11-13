"""Streaming chat UI checks with mocked AI deltas; no user chat files are changed."""
import json
import os
import time
from urllib.request import Request, urlopen
import websocket

BASE = os.environ.get('QUANTLENS_UI_URL', 'http://127.0.0.1:8610')
CDP = os.environ.get('QUANTLENS_CDP_PORT', '9237')
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

    js('''window.waitPaint=()=>new Promise(resolve=>setTimeout(resolve,180));
      window.module=await import('/components/chat-stream/chat-stream.js');
      document.body.replaceChildren();
      window.mount=document.createElement('div');document.body.append(mount);
      window.openChat=()=>{
        window.chat=module.createChat(mount,{send:(messages,options)=>{
          window.messages=messages;window.emit=options.onDelta;
          return new Promise(resolve=>window.complete=resolve);
        }});
        mount.querySelector('textarea').value='研究温和上涨';
        mount.querySelector('form').requestSubmit();
      };
      openChat();''')
    assert js("!mount.querySelector('.chat-thinking') && chat.busy")
    js("emit({channel:'content',text:'<thi'});await waitPaint()")
    assert js("!mount.querySelector('.chat-thinking') && !mount.textContent.includes('<thi')")
    js("emit({channel:'content',text:'nk>先检查**“温和上涨”**的原因。'});await waitPaint()")
    assert js("mount.querySelector('.chat-thinking').open && mount.querySelector('.chat-thinking strong').textContent==='“温和上涨”'")
    js("window.panel=mount.querySelector('.chat-thinking');panel.querySelector('summary').click();emit({channel:'content',text:'再检查低波动。'});await waitPaint()")
    assert js("mount.querySelector('.chat-thinking')===panel && !panel.open && chat.busy")
    js("panel.querySelector('summary').click();await waitPaint()")
    assert js("panel.open && panel.textContent.includes('再检查低波动。')")
    js("emit({channel:'content',text:'</think>这个现象需要**“上涨 + 低波”**同时成立。'});await waitPaint()")
    assert js("panel.open && mount.querySelector('.chat__answer strong').textContent==='“上涨 + 低波”' && chat.busy")
    js("complete({text:'<think>先检查**“温和上涨”**的原因。再检查低波动。</think>这个现象需要**“上涨 + 低波”**同时成立。',model:'test'});await waitPaint()")
    assert js("!chat.busy && mount.querySelector('.chat-thinking')===panel && panel.open && chat.history.length===2")
    assert js("!chat.context[1].content.includes('先检查') && !!mount.querySelector('[data-chat-action=regenerate]')")

    # Separate reasoning deltas also appear before completion and can be folded.
    js("chat.destroy();openChat();emit({channel:'reasoning',text:'分批检查原因'});await waitPaint()")
    assert js("mount.querySelector('.chat-thinking').open && chat.busy")
    js("emit({channel:'content',text:'回答'});await waitPaint()")
    assert js("!mount.querySelector('.chat-thinking').open && mount.querySelector('.chat__answer').textContent.includes('回答')")
    js("complete({text:'回答',reasoning:'分批检查原因'});await waitPaint()")
    assert js("chat.history[1].reasoning==='分批检查原因'")

    # Ordinary models must never show a vacant thinking panel.
    js("chat.destroy();openChat();emit({channel:'content',text:'没有思考字段的普通回答'});await waitPaint()")
    assert js("!mount.querySelector('.chat-thinking') && mount.querySelector('.chat__answer').textContent.includes('普通回答') && chat.busy")
    js("complete({text:'没有思考字段的普通回答'});await waitPaint()")
    assert js("!mount.querySelector('.chat-thinking') && !chat.busy")

    # Even a reply completed before its preview paint keeps the completed state.
    js("chat.destroy();openChat();complete({text:'快速回答',reasoning:'快速思考'});await waitPaint()")
    assert js("!mount.querySelector('.chat-thinking').open && mount.querySelector('.chat__answer').textContent==='快速回答' + String.fromCharCode(10)")

    # Long replies append bounded batches, leaving earlier DOM nodes in place.
    perf = js('''window.md=await import('/components/chat-stream/chat-markdown.js');
      window.performanceHost=document.createElement('div');mount.append(performanceHost);
      window.paints=0;
      window.live=md.createAssistantStream(performanceHost,paint=>{paints++;paint()});
      const start=performance.now();
      live.push({channel:'content',text:'已显示的开头。'});await waitPaint();
      window.prefix=performanceHost.querySelector('p');
      for(let i=0;i<2000;i++)live.push({channel:'content',text:'温和上涨的现象需要逐步验证。'.repeat(10)});
      const ingest=performance.now()-start-180;await waitPaint();
      window.tail=performanceHost.querySelector('.chat__stream-text').firstChild;
      live.push({channel:'content',text:'末尾新增内容'});await waitPaint();
      ({paints,ingest,stable:performanceHost.querySelector('p')===prefix,
        sameTail:performanceHost.querySelector('.chat__stream-text').firstChild===tail,
        tail:performanceHost.textContent.endsWith('末尾新增内容')});''')
    assert perf['paints'] <= 4 and perf['stable'] and perf['sameTail'] and perf['tail'], perf
    assert perf['ingest'] < 1500, perf
    final = js('''const fullReply=performanceHost.textContent;
      const finishStart=performance.now();live.finish(fullReply);
      ({duration:performance.now()-finishStart,
        rich:!performanceHost.querySelector('.chat__stream-text'),
        complete:performanceHost.textContent.trim().endsWith('末尾新增内容')});''')
    assert final['rich'] and final['complete'], final
    js('live.destroy();chat.destroy()')
    print('PASS: visible live thinking/answers, native collapse state, no-thinking mode, context and long-reply batching.', perf, final)
finally:
    ws.close()
    urlopen(f'http://127.0.0.1:{CDP}/json/close/{page["id"]}').close()


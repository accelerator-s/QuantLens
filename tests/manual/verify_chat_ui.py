"""Chat UI regression checks using a running web UI and a headless Chrome CDP port.

Run Chrome with --headless=new --remote-debugging-port=9237, then run this file.
Only mocked AI calls are used. Screenshots are saved outside the repository.
"""
import base64
import json
import os
import time
from pathlib import Path
from urllib.request import urlopen

import websocket

base = os.environ.get('QUANTLENS_UI_URL', 'http://127.0.0.1:8610')
port = os.environ.get('QUANTLENS_CDP_PORT', '9237')
output = Path(os.environ.get('QUANTLENS_CHAT_OUTPUT', '/tmp/quantlens-chat-ui'))
output.mkdir(parents=True, exist_ok=True)
pages = json.load(urlopen(f'http://127.0.0.1:{port}/json'))
page = next(item for item in pages if item['type'] == 'page')
ws = websocket.create_connection(page['webSocketDebuggerUrl'], suppress_origin=True)
serial = 0
exceptions = []


def call(method, params=None):
    global serial
    serial += 1
    ws.send(json.dumps({'id': serial, 'method': method, 'params': params or {}}))
    while True:
        response = json.loads(ws.recv())
        if response.get('method') == 'Runtime.exceptionThrown':
            exceptions.append(response)
        if response.get('id') == serial:
            assert 'error' not in response, response
            return response.get('result', {})


def js(expression):
    result = call('Runtime.evaluate', {'expression': expression, 'returnByValue': True, 'awaitPromise': True, 'replMode': True})
    assert 'exceptionDetails' not in result, result
    return result.get('result', {}).get('value')


def wait(expression):
    for _ in range(100):
        if js(f'Boolean({expression})'):
            return
        time.sleep(.05)
    raise AssertionError(expression)


def screenshot(name):
    shot = call('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': False})
    (output / f'{name}.png').write_bytes(base64.b64decode(shot['data']))


def size(width, height):
    call('Emulation.setDeviceMetricsOverride', {'width': width, 'height': height, 'deviceScaleFactor': 1, 'mobile': False})
    js('await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')


def check_layout():
    result = js('''(() => {
      const d=document.querySelector('.chat-dialog'), list=d.querySelector('.chat__list');
      const r=d.getBoundingClientRect(), f=d.querySelector('.chat__form').getBoundingClientRect();
      const a=d.querySelector('.chat-dialog__actions').getBoundingClientRect();
      return {width:innerWidth,height:innerHeight,left:r.left,right:r.right,top:r.top,bottom:r.bottom,
        overflow:d.scrollWidth>d.clientWidth, scrolls:list.scrollHeight>list.clientHeight,
        formTop:f.top,formBottom:f.bottom,actionTop:a.top,actionBottom:a.bottom,listHeight:list.clientHeight};
    })()''')
    assert 0 <= result['left'] and result['right'] <= result['width'], result
    assert 0 <= result['top'] and result['bottom'] <= result['height'], result
    assert not result['overflow'] and result['listHeight'] > 40, result
    assert result['formBottom'] <= result['actionTop'] + 1, result
    assert result['actionBottom'] <= result['height'], result
    return result


def check_history_resize():
    assert js("getComputedStyle(document.querySelector('.chat-history__heading')).fontSize") == '18px'
    assert js("getComputedStyle(document.querySelector('.chat-history__heading')).fontWeight") == '700'
    rect = js("(()=>{const r=document.querySelector('.chat-dialog__divider').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()")
    initial = js("document.querySelector('.chat-history').getBoundingClientRect().width")
    call('Input.dispatchMouseEvent', {'type': 'mouseMoved', **rect})
    assert js("getComputedStyle(document.querySelector('.chat-dialog__divider')).cursor") == 'col-resize'
    wait("getComputedStyle(document.querySelector('.chat-dialog__divider'),'::before').backgroundColor===getComputedStyle(document.querySelector('.chat-dialog__divider'),'::after').backgroundColor")
    call('Input.dispatchMouseEvent', {'type': 'mousePressed', 'button': 'left', 'buttons': 1, 'clickCount': 1, **rect})
    call('Input.dispatchMouseEvent', {'type': 'mouseMoved', 'button': 'left', 'buttons': 1, 'x': rect['x'] + 100, 'y': rect['y']})
    wait(f"document.querySelector('.chat-history').getBoundingClientRect().width==={initial + 100}")
    assert js("document.querySelector('.chat-dialog-backdrop').classList.contains('is-resizing')")
    call('Input.dispatchMouseEvent', {'type': 'mouseMoved', 'button': 'left', 'buttons': 1, 'x': rect['x'] + 900, 'y': rect['y']})
    assert js("(()=>{const d=document.querySelector('.chat-dialog__divider');return Number(d.getAttribute('aria-valuenow'))===Number(d.getAttribute('aria-valuemax'))})()")
    call('Input.dispatchMouseEvent', {'type': 'mouseMoved', 'button': 'left', 'buttons': 1, 'x': rect['x'] - 900, 'y': rect['y']})
    assert js("document.querySelector('.chat-history').getBoundingClientRect().width") == 180
    call('Input.dispatchMouseEvent', {'type': 'mouseMoved', 'button': 'left', 'buttons': 1, **rect})
    call('Input.dispatchMouseEvent', {'type': 'mouseReleased', 'button': 'left', 'buttons': 0, 'clickCount': 1, **rect})
    assert js("!document.querySelector('.chat-dialog-backdrop').classList.contains('is-resizing')")
    assert js("document.querySelector('.chat-history').getBoundingClientRect().width") == initial
    call('Input.dispatchKeyEvent', {'type': 'keyDown', 'key': 'ArrowRight', 'code': 'ArrowRight'})
    call('Input.dispatchKeyEvent', {'type': 'keyUp', 'key': 'ArrowRight', 'code': 'ArrowRight'})
    assert js("document.querySelector('.chat-history').getBoundingClientRect().width") == initial + 10
    call('Input.dispatchKeyEvent', {'type': 'keyDown', 'key': 'ArrowLeft', 'code': 'ArrowLeft'})
    call('Input.dispatchKeyEvent', {'type': 'keyUp', 'key': 'ArrowLeft', 'code': 'ArrowLeft'})
    size(700, 800)
    assert js("getComputedStyle(document.querySelector('.chat-dialog__divider')).display") == 'none'
    size(1366, 900)
    check_layout()


call('Runtime.enable')
call('Page.enable')
size(1366, 900)
call('Page.navigate', {'url': base})
wait("document.querySelector('[data-assistant-open]')")
js('''window.historyApi=(await import('/core/api.js')).api;
window.realHistory={list:historyApi.chatHistory,save:historyApi.chatHistorySave,remove:historyApi.chatHistoryDelete};
window.fileScope='ui-test:'+crypto.randomUUID()+':';window.fileIds=new Set();window.titleCalls=[];
historyApi.chatHistoryTitle=async payload=>{titleCalls.push(payload);return {title:'财报后的收益变化'}};
historyApi.chatHistory=scope=>realHistory.list(fileScope+scope);
historyApi.chatHistorySave=async(id,payload)=>{const result=await realHistory.save(id,{...payload,scope:payload.scope.startsWith(fileScope)?payload.scope:fileScope+payload.scope});fileIds.add(id);return result};
window.historyItems=async scope=>(await historyApi.chatHistory(scope)).items;
window.chatTest={calls:[],final:[],errors:[]};
window.testBus={on(){},emit(name,data){if(name==='toast')chatTest.errors.push(data)}};
window.longReply=('先把现象整理成可验证的假设。财报披露后，市场可能分批消化新信息。\\n\\n').repeat(35);
window.testApi={
 jobs:async()=>({items:[]}),
 aiModels:async()=>({items:[{name:'deepseek-cyber',enabled:true,kind:'api_model'}]}),
 factors:async()=>({statuses:[],items:[]}),
 factorEvolutionModelRecommendations:async()=>({recommended:'deepseek-cyber',items:[{name:'deepseek-cyber',selectable:true,recommended:true,reason:'具备深度推理、结构化输出、代码生成、长上下文与风险识别能力。'}]}),
 strategyChat:args=>{chatTest.calls.push(structuredClone(args));return new Promise(resolve=>chatTest.resolve=resolve)},
 strategyChatFinalize:async args=>{chatTest.final.push(structuredClone(args));throw Error('模拟生成失败')},
 factorResearchChat:args=>{chatTest.calls.push(structuredClone(args));return new Promise(resolve=>chatTest.resolve=resolve)},
 factorResearchStart:async args=>{chatTest.final.push(structuredClone(args));throw Error('模拟启动失败')}
};''')
report = []
for kind in ['strategy', 'factor']:
    js("chatTest.final=[]")
    if kind == 'strategy':
        js("await (await import('/components/strategy/strategy-chat.js')).openStrategyChat({api:testApi,bus:testBus},{onYaml(){}})")
    else:
        js('''window.factorHost=document.createElement('section');document.body.append(factorHost);
        factorHost.innerHTML=await (await fetch('/components/factors/factors.html')).text();
        await (await import('/components/factors/factors.js')).mount(factorHost,{api:testApi,bus:testBus});
        factorHost.querySelector('#factors-tab-research').click();''')
        wait("factorHost.querySelector('#factors-panel-research [data-ai-research]:not(:disabled)')")
        js("factorHost.querySelector('#factors-panel-research [data-ai-research]').click()")
    wait("document.querySelector('.chat-dialog') && !document.querySelector('.chat__input').disabled")
    assert js("document.querySelector('.chat__welcome')!==null")
    assert js("document.querySelector('.chat__send').disabled")
    assert js("document.querySelector('.chat-dialog__actions .btn--primary').disabled")
    scope = 'strategy:new' if kind == 'strategy' else 'factor-research'
    assert js(f"(await historyItems('{scope}')).length") == 1
    assert js(f"(await historyItems('{scope}'))[0].messages.length") == 0
    js("document.querySelector('.chat-dialog__actions .btn--primary').click()")
    assert js('chatTest.final.length') == 0
    size(1366, 900)
    assert js("document.querySelectorAll('.chat__suggestion').length") == 0
    assert js("document.querySelector('.chat-history__notice').hidden")
    assert js("document.querySelector('.chat-dialog__close').getBoundingClientRect().left>document.querySelector('.chat-dialog__heading').getBoundingClientRect().right")
    check_history_resize()
    for width, height in [(1366,900),(390,844),(320,568),(844,390),(390,420)]:
        size(width,height)
        assert js("document.querySelector('.chat__list').scrollHeight<=document.querySelector('.chat__list').clientHeight+1"), (kind,width,height)
        assert js("(() => {const list=document.querySelector('.chat__list').getBoundingClientRect();return ['.chat__welcome-mark','.chat__welcome-title','.chat__welcome-desc'].every(selector=>{const r=document.querySelector(selector).getBoundingClientRect();return r.top>=list.top-1 && r.bottom<=list.bottom+1})})()"), (kind,width,height)
        assert js("(() => {const w=document.querySelector('.chat__welcome'),m=document.querySelector('.chat__welcome-mark'),t=document.querySelector('.chat__welcome-title'),d=document.querySelector('.chat__welcome-desc');const center=e=>{const r=e.getBoundingClientRect();return (r.left+r.right)/2};return [m,t,d].every(e=>Math.abs(center(e)-center(w))<1)})()")
    size(1366,900)
    js("document.documentElement.dataset.theme='light'")
    screenshot(f'{kind}-welcome')
    js("chatTest.calls=[];chatTest.final=[]")
    js('''const input=document.querySelector('.chat__input');input.value='财报之后是否存在收益漂移？';
    input.dispatchEvent(new Event('input'));
    input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',isComposing:true,bubbles:true,cancelable:true}));''')
    assert js('chatTest.calls.length') == 0, 'IME confirmation must not submit'
    assert js("document.querySelector('.chat-dialog__actions .btn--primary').disabled"), 'An unsent draft is not a discussion'
    for turn in range(2):
        js(f'''document.querySelector('.chat__input').value={json.dumps('财报之后是否存在收益漂移？' if turn == 0 else '只研究业绩超预期的公司，观察一个月。')};
        document.querySelector('.chat__input').dispatchEvent(new Event('input'));
        document.querySelector('.chat__form').requestSubmit();''')
        assert js("document.querySelector('.chat-dialog__actions .btn--primary').disabled")
        assert js("document.querySelector('.chat-dialog [data-select-trigger]').disabled")
        assert js('chatTest.calls.at(-1).messages.length') == 1 + turn * 2
        js("chatTest.resolve({reply:longReply,model:'deepseek-cyber'})")
        wait("!document.querySelector('.chat').getAttribute('aria-busy').includes('true')")
    assert js("document.querySelectorAll('.chat__item--assistant').length") == 2
    # Edit beside a user message, cancel without truncation, then resend in place.
    assert js("document.querySelector('.chat__tools [data-chat-action]')===null")
    js("document.querySelectorAll('[data-chat-action=edit]')[1].click()")
    assert js("document.querySelector('.chat__edit-input').value") == '只研究业绩超预期的公司，观察一个月。'
    js("document.querySelector('.chat__edit-cancel').click()")
    assert js("document.querySelectorAll('.chat__item').length") == 4
    js("document.querySelectorAll('[data-chat-action=edit]')[1].click();document.querySelector('.chat__edit-input').value='修改后只观察两周。';document.querySelector('.chat__edit-form').requestSubmit()")
    assert js('chatTest.calls.at(-1).messages.length') == 3
    assert js('chatTest.calls.at(-1).messages[2].content') == '修改后只观察两周。'
    assert js("[...document.querySelectorAll('[data-chat-action]')].every(button=>button.disabled)")
    js("chatTest.resolve({reply:longReply,model:'deepseek-cyber'})")
    wait("!document.querySelector('.chat').getAttribute('aria-busy').includes('true')")
    wait("document.querySelector('.chat-history__title').textContent==='财报后的收益变化'")
    js("document.querySelector('.chat-history__rename').click();document.querySelector('.chat-history__rename-input').value='我自己的财报研究';document.querySelector('.chat-history__rename-form').requestSubmit()")
    assert js("document.querySelector('.chat-history__title').textContent") == '我自己的财报研究'
    for width, height in [(1366, 900), (768, 1024), (390, 844), (320, 568), (844, 390), (390, 420)]:
        size(width, height)
        for theme in ['light', 'dark']:
            js(f"document.documentElement.dataset.theme='{theme}'")
            layout = check_layout()
            assert layout['scrolls'], layout
            report.append({'kind': kind, 'theme': theme, **layout})
            if (width, height) in [(1366, 900), (390, 844)]:
                screenshot(f'{kind}-{width}-{theme}')
    js("document.querySelector('.chat-dialog__actions .btn--primary').click()")
    wait('chatTest.final.length===1')
    assert js('chatTest.final[0].messages[2].content') == '修改后只观察两周。'
    assert js('chatTest.final[0].messages.length') == 4, 'Finalization must receive both user and assistant turns'
    wait("!document.querySelector('.chat__input').disabled")
    # History survives closing and supports independent new conversations.
    size(1366, 900)
    assert js("document.querySelectorAll('.chat-history__row').length") == 1
    js("document.querySelector('.chat-history__new').click()")
    assert js("document.querySelector('.chat__welcome')!==null")
    assert js("document.querySelector('.chat-dialog__actions .btn--primary').disabled")
    wait(f"(await historyItems('{scope}')).length===2")
    assert js(f"(await historyItems('{scope}')).some(item=>item.messages.length===0)")
    js("document.querySelectorAll('.chat-history__open')[1].click()")
    assert js("document.querySelector('.chat-dialog__actions .btn--primary').disabled") is False
    js("document.querySelectorAll('.chat-history__open')[0].click()")
    assert js("document.querySelector('.chat-dialog__actions .btn--primary').disabled")
    js("document.querySelector('.chat__input').value='第二段独立对话';document.querySelector('.chat__form').requestSubmit()")
    assert js('chatTest.calls.at(-1).messages.length') == 1
    assert js("[...document.querySelectorAll('.chat-history button')].filter(button=>button.getClientRects().length).every(button=>button.disabled)")
    js("chatTest.resolve({reply:'独立回复',model:'deepseek-cyber'})")
    wait("document.querySelectorAll('.chat__item--assistant').length===1")
    assert js("document.querySelectorAll('.chat-history__row').length") == 2
    js("document.querySelectorAll('.chat-history__open')[1].click()")
    assert js("document.querySelectorAll('.chat__item').length") == 4
    js("document.querySelector('.chat__input').value='接着之前的讨论';document.querySelector('.chat__form').requestSubmit()")
    assert js('chatTest.calls.at(-1).messages.length') == 5
    assert js('chatTest.calls.at(-1).messages[2].content') == '修改后只观察两周。'
    js("chatTest.resolve({reply:'继续之前的上下文',model:'deepseek-cyber'})")
    wait("document.querySelectorAll('.chat__item').length===6")
    js("document.querySelector('.chat__input').value='尚未发出的草稿';document.querySelector('.chat__input').dispatchEvent(new Event('input'))")
    js("document.querySelector('.chat-dialog__close').click()")
    if kind == 'strategy':
        js("await (await import('/components/strategy/strategy-chat.js')).openStrategyChat({api:testApi,bus:testBus},{onYaml(){}})")
    else:
        js("factorHost.querySelector('#factors-panel-research [data-ai-research]').click()")
    wait("document.querySelector('.chat-dialog') && !document.querySelector('.chat__input').disabled")
    assert js("document.querySelectorAll('.chat__item').length") == 6
    assert js("document.querySelector('.chat__input').value") == '尚未发出的草稿'
    # Research/finalization uses the restored active chat, excluding an unsent draft.
    js("chatTest.final=[];document.querySelector('.chat-dialog__actions .btn--primary').click()")
    wait('chatTest.final.length===1')
    assert js('chatTest.final[0].messages.length') == 6
    assert js('chatTest.final[0].messages[4].content') == '接着之前的讨论'
    assert js("chatTest.final[0].messages.some(message=>message.content==='尚未发出的草稿')") is False
    wait("!document.querySelector('.chat__input').disabled")
    # Delete an inactive session, then the active session; closing must not recreate either.
    js("document.querySelectorAll('.chat-history__delete')[1].click()")
    wait("document.querySelectorAll('.chat-history__row').length===1 && !document.querySelector('.chat__input').disabled")
    assert js("document.querySelectorAll('.chat__item').length") == 6
    size(390, 844)
    js("document.querySelector('.chat-dialog__history-toggle').click()")
    assert js("getComputedStyle(document.querySelector('.chat-history')).display") == 'flex'
    screenshot(f'{kind}-mobile-history')
    js("document.querySelector('.chat-history__delete').click()")
    wait("document.querySelectorAll('.chat-history__row').length===0 && !document.querySelector('.chat__input').disabled")
    assert js("document.querySelector('.chat__welcome')!==null")
    assert js("document.querySelector('.chat-dialog__actions .btn--primary').disabled")
    js("document.querySelector('.chat-dialog__close').click()")
    assert js("document.querySelector('.chat-dialog')===null")
    assert js("document.body.style.overflow!=='hidden'")
# Shared component: failure/retry must preserve the exact pending conversation.
js('''window.retryShell=(await import('/components/chat-stream/chat-dialog.js')).createChatDialog({title:'重试检查'});
window.attempts=[];window.chat=(await import('/components/chat-stream/chat-stream.js')).createChat(retryShell.chatHost,{
 send:async history=>{attempts.push(history);if(attempts.length===1)throw Error('模拟连接失败');return '已恢复';}
});document.querySelector('.chat__input').value='重试同一个问题';document.querySelector('.chat__form').requestSubmit();''')
wait("document.querySelector('.chat__error button')")
js("document.querySelector('.chat__error button').click()")
wait('chat.history.length===2')
assert js('JSON.stringify(attempts[0])===JSON.stringify(attempts[1])')
assert js("document.querySelectorAll('.chat__item--user').length") == 1
js("document.querySelector('[data-chat-action=edit]').click();document.querySelector('.chat__edit-input').value='修改后的重试问题';document.querySelector('.chat__edit-form').requestSubmit()")
wait("chat.history.length===2 && chat.history[0].content==='修改后的重试问题'")
js("document.querySelector('[data-chat-action=regenerate]').click()")
wait('chat.history.length===2 && attempts.length===4')
assert js('attempts.at(-1).length') == 1
js("document.querySelector('.chat-dialog').dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")
assert js("document.querySelector('.chat-dialog')===null")
# Rich text, math, thinking, title races and centered close animation.
rich_reply = r"""<think>标签里的思考内容，先确认 **财报时间** 和 $t_0$。</think>
## 检验方法
**先固定观察区间**，再计算收益，参考 [研究说明](https://example.com/research)。

- 对齐公告日期
- 排除停牌样本

| 指标 | 数值 |
| --- | --- |
| 持有期 | 20 天 |

行内公式 $r_t = \frac{P_t}{P_{t-1}} - 1$，以及 \(\alpha + \beta\)。

$$
\operatorname{IC} = \frac{\operatorname{Cov}(f,r)}{\sigma_f\sigma_r}
$$

\[ \sum_{i=1}^{n} w_i = 1 \]

```python
print("<think>这是代码，不是思考标签</think>")
```

<img src=x onerror="window.chatInjected=1">
<script>window.chatInjected=1</script>
[危险链接](javascript:alert(1))
"""
rich_reply = rich_reply.replace(chr(92)*2, chr(92))
js('window.richReply='+json.dumps(rich_reply))
js('''window.md=(await import('/components/chat-stream/chat-markdown.js'));
window.probe=document.createElement('div');
md.renderAssistant(probe,'`<think>字面标签</think>`');
window.parseChecks={literal:!probe.querySelector('details')&&probe.querySelector('code').textContent.includes('<think>')};
md.renderAssistant(probe,'<think>只有思考，没有最终答案');
parseChecks.unclosed=probe.querySelector('details').open&&!probe.querySelector('.chat__answer');
md.renderAssistant(probe,'代码中的公式：`$x$`，费用 $100 和 $200。');
parseChecks.codeMath=!probe.querySelector('.katex');
md.renderAssistant(probe,'公式错误 $x_{$');
parseChecks.invalidMath=!!probe.querySelector('.katex-error');
window.richCalls=[];window.resolveTitle=null;
historyApi.chatHistoryTitle=()=>new Promise(resolve=>window.resolveTitle=resolve);
window.openRich=async()=>{
 let instance;
 window.richShell=(await import('/components/chat-stream/chat-dialog.js')).createChatDialog({title:'研究新因子',history:true,onClose:()=>instance.destroy()});
 instance=(await import('/components/chat-stream/chat-stream.js')).createChat(richShell.chatHost,{
 historyHost:richShell.historyHost,historyKey:'rich-check',getMetadata:()=>({model:'test-model'}),
 send:async messages=>{richCalls.push(messages);return {text:richReply,reasoning:'独立字段里的思考内容',model:'test-model'}}});
 window.richChat=instance;await instance.ready;
};await openRich();
document.querySelector('.chat__input').value='检验财报后的收益';document.querySelector('.chat__form').requestSubmit();''')
assert all(js('parseChecks').values()), js('parseChecks')
wait('richChat.history.length===2 && !richChat.busy && resolveTitle')
assert js("document.querySelector('.chat__answer h2').textContent") == '检验方法'
assert js("document.querySelector('.chat__answer strong').textContent") == '先固定观察区间'
assert js("document.querySelectorAll('.chat__answer .katex').length") == 4
assert js("document.querySelector('.chat-markdown__table table')!==null")
assert js("document.querySelector('.chat__answer pre code').textContent.includes('<think>')")
assert js("document.querySelector('.chat-thinking').open") is False
assert js("document.querySelector('.chat-thinking__body').textContent.includes('独立字段里的思考内容')")
assert js("document.querySelector('.chat-thinking__body').textContent.includes('标签里的思考内容')")
assert js("document.querySelector('.chat__answer script, .chat__answer img, .chat__answer a[href^=javascript]')===null && !window.chatInjected")
# A title request in flight does not block file saves, and a manual title wins.
js("document.querySelector('.chat-history__rename').click();document.querySelector('.chat-history__rename-input').value='手动命名的财报研究';document.querySelector('.chat-history__rename-form').requestSubmit()")
js("document.querySelector('.chat__input').value='继续检验';document.querySelector('.chat__form').requestSubmit()")
wait('richChat.history.length===4 && !richChat.busy')
wait("(await historyItems('rich-check'))[0]?.messages.length===4")
assert js("richCalls[1][1].content.includes('标签里的思考内容')") is False
assert js("richCalls[1][1].content.includes('独立字段里的思考内容')") is False
assert js("richCalls[1][1].reasoning===undefined")
assert js("(await historyItems('rich-check'))[0].title_manual")
js("resolveTitle({title:'不能覆盖人工标题'});await new Promise(resolve=>setTimeout(resolve,30))")
assert js("(await historyItems('rich-check'))[0].title") == '手动命名的财报研究'
assert js("(await historyItems('rich-check'))[0].messages[1].reasoning") == '独立字段里的思考内容'
size(1366,900)
js("document.documentElement.dataset.theme='light';document.querySelector('.chat__list').scrollTop=0")
screenshot('rich-markdown-light')
js("document.querySelector('.chat-thinking').open=true")
screenshot('rich-thinking-light')
close_rect = js("(()=>{const r=document.querySelector('.chat-dialog__close').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()")
call('Input.dispatchMouseEvent', {'type':'mouseMoved',**close_rect})
js('await new Promise(resolve=>setTimeout(resolve,90))')
assert js("(()=>{const b=document.querySelector('.chat-dialog__close').getBoundingClientRect(),s=document.querySelector('.chat-dialog__close svg').getBoundingClientRect();return Math.abs((b.left+b.right-s.left-s.right)/2)<.2&&Math.abs((b.top+b.bottom-s.top-s.bottom)/2)<.2})()")
for width,height in [(1366,900),(390,844),(320,568)]:
    size(width,height)
    check_layout()
    js("document.documentElement.dataset.theme='dark'")
    screenshot(f'rich-{width}-dark')
js("richShell.close();await openRich()")
assert js('richChat.history.length') == 4
assert js("document.querySelector('.chat-history__title').textContent") == '手动命名的财报研究'
assert js("document.querySelector('.chat-thinking__body').textContent.includes('独立字段里的思考内容')")
js("richShell.close();historyApi.chatHistoryTitle=async()=>({title:'新的研究对话'})")
# Existing untitled conversations generate a title on restore; failures offer retry.
js('''window.titleAttempts=0;
historyApi.chatHistoryTitle=async()=>{titleAttempts++;if(titleAttempts===1)throw Error('模拟标题服务暂时失败');return {title:'恢复后补上的标题'}};
window.untitledId=crypto.randomUUID();
await historyApi.chatHistorySave(untitledId,{scope:'untitled-restore',messages:[{role:'user',content:'检验财报后的收益'},{role:'assistant',content:'先对齐公告日期。'}],draft:'',metadata:{model:'test-model'},revision:0});
let restored;
window.untitledShell=(await import('/components/chat-stream/chat-dialog.js')).createChatDialog({title:'恢复标题检查',history:true,onClose:()=>restored.destroy()});
restored=(await import('/components/chat-stream/chat-stream.js')).createChat(untitledShell.chatHost,{historyHost:untitledShell.historyHost,historyKey:'untitled-restore',send:async()=>''});
await restored.ready;''')
wait("document.querySelector('.chat-history__notice').textContent.includes('模拟标题服务暂时失败')")
assert js("[...document.querySelectorAll('.chat-history > button')].some(button=>!button.hidden&&button.textContent==='重试生成标题')")
js("[...document.querySelectorAll('.chat-history > button')].find(button=>button.textContent==='重试生成标题').click()")
wait("document.querySelector('.chat-history__title').textContent==='恢复后补上的标题'")
assert js('titleAttempts') == 2
assert js("(await historyItems('untitled-restore'))[0].title") == '恢复后补上的标题'
js("untitledShell.close();historyApi.chatHistoryTitle=async()=>({title:'新的研究对话'})")
# Late replies may complete after closing; deletion and newer turns must win.
js('''window.openLateChat=async()=>{
 const {createChatDialog}=await import('/components/chat-stream/chat-dialog.js');
 const {createChat}=await import('/components/chat-stream/chat-stream.js');
 let instance;
 const shell=createChatDialog({title:'后台回复检查',history:true,onClose:()=>instance.destroy()});
 instance=createChat(shell.chatHost,{historyHost:shell.historyHost,historyKey:'late-reply-check',
 send:()=>new Promise(resolve=>window.lateResolve=resolve)});
 window.lateShell=shell;await instance.ready;
};await openLateChat();
window.sendLate=text=>{document.querySelector('.chat__input').value=text;document.querySelector('.chat__form').requestSubmit()};
sendLate('关闭后完成');lateShell.close();lateResolve('后台回复已保存');''')
wait("(await historyItems('late-reply-check'))[0]?.messages.length===2")
js("await openLateChat()")
assert js("document.querySelectorAll('.chat__item').length") == 2
js("sendLate('删除前的请求');lateShell.close();await openLateChat();document.querySelector('.chat-history__delete').click()")
wait("document.querySelector('.chat-history__empty') && !document.querySelector('.chat__input').disabled")
js("lateResolve('这条回复不能恢复已删历史')")
wait("(await historyItems('late-reply-check')).length===0")
js("lateShell.close()")
assert js("(await historyItems('late-reply-check')).length") == 0
js("await openLateChat();sendLate('旧请求');window.oldResolve=lateResolve;lateShell.close();await openLateChat();sendLate('重新打开后继续');lateResolve('新回复')")
wait("document.querySelectorAll('.chat__item').length===3")
js("oldResolve('过期回复');await new Promise(resolve=>setTimeout(resolve,20))")
assert js("(await historyItems('late-reply-check'))[0].messages.at(-1).content") == '新回复'
refresh_scope = js("fileScope+'late-reply-check'")
created_ids = js("[...fileIds]")
js("lateShell.close()")
wait("(await historyItems('late-reply-check'))[0]?.messages.at(-1).content==='新回复'")
# Reload discards all module state; the conversation must come from its JSON file.
call('Page.reload', {'ignoreCache': True})
wait("document.querySelector('[data-assistant-open]')")
js(f'''window.reloadShell=(await import('/components/chat-stream/chat-dialog.js')).createChatDialog({{title:'文件恢复检查',history:true}});
window.reloadChat=(await import('/components/chat-stream/chat-stream.js')).createChat(reloadShell.chatHost,{{
 historyHost:reloadShell.historyHost,historyKey:{json.dumps(refresh_scope)},send:async()=>''
}});await reloadChat.ready;''')
assert js('reloadChat.history.at(-1).content') == '新回复'
assert js("document.querySelectorAll('.chat__item').length") == 3
js("reloadChat.destroy();reloadShell.close()")
js(f"await new Promise(resolve=>setTimeout(resolve,50));for(const id of {json.dumps(created_ids)})await (await import('/core/api.js')).api.chatHistoryDelete(id)")
assert not exceptions, exceptions
(output / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(f'PASS: two chat flows, multi-turn finalization, IME, pending locks, message editing/regeneration, retry, file history restore/continue/delete, empty-chat persistence/action locks, active-chat research, title recovery/retry, draft persistence, centered welcome, close, and {len(report)} theme/viewport layouts. Screenshots: {output}')
ws.close()

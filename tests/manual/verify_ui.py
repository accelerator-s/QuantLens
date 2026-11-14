"""Check real browser navigation, tabs, assistant and mobile layout over CDP."""
import base64,json,time
from pathlib import Path
import httpx,websocket

pages=httpx.get('http://127.0.0.1:9229/json',trust_env=False).json()
page=next(p for p in pages if p['type']=='page')
ws=websocket.create_connection(page['webSocketDebuggerUrl'],suppress_origin=True)
serial=0;events=[]
def call(method,params=None):
    global serial
    serial+=1;ident=serial;ws.send(json.dumps({'id':ident,'method':method,'params':params or {}}))
    while True:
        response=json.loads(ws.recv())
        if response.get('id')==ident:
            if 'error' in response:raise RuntimeError(response['error'])
            return response.get('result',{})
        events.append(response)
def js(expression):
    result=call('Runtime.evaluate',{'expression':expression,'returnByValue':True,'awaitPromise':True})
    if 'exceptionDetails' in result:raise AssertionError(result['exceptionDetails'])
    return result.get('result',{}).get('value')
def wait(expression,timeout=15):
    started=time.monotonic()
    while time.monotonic()-started<timeout:
        value=js("Boolean("+expression+")")
        if value:return value
        time.sleep(.1)
    raise AssertionError('Timed out: '+expression)
call('Runtime.enable');call('Page.enable');call('Network.enable')
call('Emulation.setDeviceMetricsOverride',{'width':1280,'height':900,'deviceScaleFactor':1,'mobile':False})
call('Page.navigate',{'url':'http://127.0.0.1:8611/'})
wait("document.querySelector('[data-route=dashboard]') && document.querySelector('[data-view=dashboard] h2')")
assert js("document.querySelectorAll('[data-route]').length")==8
report={'navigation':[],'layouts':[]}
for width in [1280,400]:
    call('Emulation.setDeviceMetricsOverride',{'width':width,'height':900,'deviceScaleFactor':1,'mobile':False})
    for route in ['dashboard','screen','factors','strategy-research','backtest','portfolio','trash','settings']:
        js(f"document.querySelector('[data-route={route}]').click()")
        wait(f"document.querySelector('[data-view={route}]') && !document.querySelector('[data-view={route}]').hidden && document.querySelector('[data-view={route}]').innerText.length>10")
        time.sleep(.3)
        size=js("({body:document.documentElement.scrollWidth,viewport:innerWidth})")
        assert size['body']<=size['viewport'],(width,route,size)
        report['layouts'].append({'width':width,'route':route,**size})
        if width==1280:report['navigation'].append(route)
    screenshot=call('Page.captureScreenshot',{'format':'png','captureBeyondViewport':False})
    Path(f'docs/course/evidence/ui-{width}.png').write_bytes(base64.b64decode(screenshot['data']))
# Open each internal tab at least once and exercise keyboard navigation.
for route in ['factors','strategy-research','settings']:
    js(f"document.querySelector('[data-route={route}]').click()")
    wait(f"document.querySelector('[data-view={route}] [role=tab]')")
    count=js(f"document.querySelectorAll('[data-view={route}] > [role=tablist] > [role=tab]').length")
    for i in range(count):
        js(f"document.querySelectorAll('[data-view={route}] > [role=tablist] > [role=tab]')[{i}].click()")
        time.sleep(.3)
    assert js(f"document.querySelectorAll('[data-view={route}] > [role=tablist] > [aria-selected=true]').length")==1
    js(f"document.querySelector('[data-view={route}] > [role=tablist] > [role=tab]').dispatchEvent(new KeyboardEvent('keydown',{{key:'ArrowRight',bubbles:true}}))")
js("document.querySelector('[data-assistant-open]').click()")
wait("document.querySelector('.assistant-drawer') && !document.querySelector('.assistant-drawer').hidden")
js("document.querySelector('[data-route=dashboard]').click()")
assert js("document.querySelector('.assistant-drawer').hidden")
js("document.querySelector('[data-assistant-open]').click()")
wait("!document.querySelector('.assistant-drawer').hidden")
js("document.querySelector('.assistant-drawer').dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")
assert js("document.querySelector('.assistant-drawer').hidden")
report['business_views']={}
js("document.querySelector('[data-route=screen]').click()")
wait("document.querySelector('[data-view=screen] [data-stock]')")
report['business_views']['screen_rows']=js("document.querySelectorAll('[data-view=screen] tbody tr').length")
js("document.querySelector('[data-view=screen] [data-stock]').click()")
wait("document.querySelector('[data-view=stocks] [data-detail] svg')")
assert js("document.querySelector('[data-view=stocks]').hidden===false")
report['business_views']['stock_drilldown']=True
js("document.querySelector('[data-route=factors]').click()")
wait("document.querySelector('[data-view=factors] [data-name=reversal_5]')")
js("document.querySelector('[data-view=factors] [data-name=reversal_5]').click()")
wait("document.querySelector('[data-view=factors] [data-detail] tbody tr')")
js("document.querySelector('[data-view=factors] [data-detail] tbody tr').click()")
wait("document.querySelector('[data-view=factors] [data-eval-run-detail]')")
report['business_views']['factor_evaluation']=True
js("document.querySelector('[data-route=backtest]').click()")
wait("document.querySelector('[data-view=backtest] [data-detail] .metric')")
report['business_views']['backtest_metrics']=js("document.querySelectorAll('[data-view=backtest] .metric').length")
assert js("document.documentElement.scrollWidth<=innerWidth")
js("document.querySelector('[data-route=portfolio]').click()")
wait("document.querySelector('#portfolio-tab-attribution')")
js("document.querySelector('#portfolio-tab-attribution').click()")
wait("document.querySelector('[data-attribution-select] [data-select-trigger]')")
js("document.querySelector('[data-attribution-select] [data-select-trigger]').click();document.querySelector('[data-attribution-select] [data-value=research_reversal_5]').click();document.querySelector('[data-attribution]').requestSubmit()")
wait("document.querySelector('[data-attribution-result] tbody tr')")
report['business_views']['attribution_rows']=js("document.querySelectorAll('[data-attribution-result] tbody tr').length")
report['themes']=[]
for theme in ['dark','light']:
    js(f"localStorage.setItem('quant-theme','{theme}');document.documentElement.dataset.theme='{theme}'")
    js("document.querySelector('[data-route=screen]').click()")
    time.sleep(.3)
    assert js("document.documentElement.scrollWidth<=innerWidth")
    report['themes'].append({'theme':theme,'overflow':False})
    shot=call('Page.captureScreenshot',{'format':'png','captureBeyondViewport':False})
    Path(f'docs/course/evidence/ui-{theme}.png').write_bytes(base64.b64decode(shot['data']))
exceptions=[e for e in events if e.get('method')=='Runtime.exceptionThrown']
assert not exceptions,exceptions
report['javascript_exceptions']=len(exceptions)
http_errors=[{'url':e['params']['response']['url'],'status':e['params']['response']['status']}
             for e in events if e.get('method')=='Network.responseReceived' and e['params']['response']['status']>=400]
assert not http_errors,http_errors
report['http_errors']=http_errors
Path('docs/course/evidence/ui-verification.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
print(json.dumps(report,ensure_ascii=False))
ws.close()

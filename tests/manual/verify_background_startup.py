"""Inspect startup UI against a running app and Chrome on CDP port 9241."""
import base64
import json
import os
from pathlib import Path
import time
from urllib.request import urlopen
import websocket

port=os.environ.get('QUANTLENS_CDP_PORT','9241')
base=os.environ.get('QUANTLENS_UI_URL','http://127.0.0.1:8610')
page=next(p for p in json.load(urlopen(f'http://127.0.0.1:{port}/json')) if p['type']=='page')
ws=websocket.create_connection(page['webSocketDebuggerUrl'],suppress_origin=True)
serial=0

def call(method,params=None):
    global serial
    serial+=1
    ws.send(json.dumps({'id':serial,'method':method,'params':params or {}}))
    while True:
        result=json.loads(ws.recv())
        if result.get('id')==serial:
            assert 'error' not in result,result
            return result.get('result',{})

def js(source):
    result=call('Runtime.evaluate',{'expression':source,'returnByValue':True,'awaitPromise':True,'replMode':True})
    assert 'exceptionDetails' not in result,result
    return result.get('result',{}).get('value')

def wait(source):
    for _ in range(150):
        if js(f'Boolean({source})'):return
        time.sleep(.1)
    raise AssertionError(source)

call('Page.enable')
call('Emulation.setDeviceMetricsOverride',{'width':1366,'height':900,'deviceScaleFactor':1,'mobile':False})
call('Page.navigate',{'url':base})
wait("document.querySelector('[data-health-label]')?.textContent==='服务启动中'")
js("document.querySelector('[data-health]').click()")
wait("document.querySelector('[data-service-summary]')?.textContent.includes('启动中')")
assert js("document.querySelectorAll('[data-pane=cluster] .ql-card').length")==1
assert js("document.querySelector('[data-service-log]')") is None
assert js("document.querySelector('[data-start]').disabled")
assert js("document.querySelector('[data-stop]').disabled")
assert js("document.querySelector('[data-service-task]')") is None
assert js("location.hash")=='#settings/cluster'
output=Path('/tmp/quantlens-background-ui');output.mkdir(exist_ok=True)
for width in [1366,390]:
    call('Emulation.setDeviceMetricsOverride',{'width':width,'height':900,'deviceScaleFactor':1,'mobile':False})
    js('await new Promise(requestAnimationFrame)')
    assert js('document.documentElement.scrollWidth<=innerWidth')
    shot=call('Page.captureScreenshot',{'format':'png','captureBeyondViewport':False})
    (output/f'startup-{width}.png').write_bytes(base64.b64decode(shot['data']))
assert js("document.querySelector('.ql-task-dialog')") is None
# Drive the same health subscription used by the live app to verify failure and ready labels.
js("window.startupFixtureBus=(await import('/core/bus.js')).createBus();(await import('/core/health.js')).initHealth(startupFixtureBus);window.startupRealHealth=(await import('/core/health.js')).getHealth()")
js("startupFixtureBus.emit('health',{...startupRealHealth,service_startup:{status:'failed',error:'Hive 启动失败'}})")
assert js("document.querySelector('[data-health-label]').textContent")=='服务启动失败'
assert js("document.querySelector('[data-health]').title")=='Hive 启动失败'
js("startupFixtureBus.emit('health',{...startupRealHealth,service_startup:{status:'completed'}})")
assert js("document.querySelector('[data-health-label]').textContent")=='运行正常'
js("startupFixtureBus.emit('health',startupRealHealth)")
print('PASS: startup label, compact cluster card, no task detail controls or dialog, disabled startup controls, failure/ready labels, desktop/mobile layouts')
ws.close()

"""Capture the running localhost service UIs through an isolated headless browser."""
import base64,json,time
from pathlib import Path
import httpx,websocket
page=next(p for p in httpx.get('http://127.0.0.1:9229/json',trust_env=False).json() if p['type']=='page')
ws=websocket.create_connection(page['webSocketDebuggerUrl'],suppress_origin=True);serial=0

def call(method,params=None):
    global serial
    serial+=1;ident=serial;ws.send(json.dumps({'id':ident,'method':method,'params':params or {}}))
    while True:
        result=json.loads(ws.recv())
        if result.get('id')==ident:
            if 'error' in result:raise RuntimeError(result['error'])
            return result.get('result',{})

call('Page.enable');call('Emulation.setDeviceMetricsOverride',{'width':1440,'height':960,'deviceScaleFactor':1,'mobile':False})
for name,port in [('hdfs',9870),('yarn',8088),('hbase',16010),('spark-history',18080)]:
    call('Page.navigate',{'url':f'http://127.0.0.1:{port}/'})
    time.sleep(2)
    image=call('Page.captureScreenshot',{'format':'png','captureBeyondViewport':False})
    Path(f'docs/course/evidence/{name}.png').write_bytes(base64.b64decode(image['data']))
ws.close()

"""Bound request bodies before JSON/multipart parsing, including chunked requests."""
from starlette.responses import JSONResponse

class HttpSafety:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        async def safe_send(message):
            if message['type']=='http.response.start':
                headers=list(message.get('headers',[]))
                names={k.lower() for k,v in headers}
                headers += [(k,v) for k,v in [(b'x-content-type-options',b'nosniff'),(b'referrer-policy',b'no-referrer'),(b'cache-control',b'no-store')] if k not in names]
                message={**message,'headers':headers}
            await send(message)
        limit=1150000 if scope['path'].endswith('/voice') else 65536
        length=next((v for k,v in scope.get('headers',[]) if k==b'content-length'),b'0')
        try:too_large=int(length)>limit
        except ValueError:too_large=True
        if too_large:return await JSONResponse({'detail':'请求内容过大'},status_code=413)(scope,receive,safe_send)
        chunks=[];size=0
        while True:
            message=await receive()
            if message['type']=='http.disconnect':return
            size+=len(message.get('body',b''))
            if size>limit:return await JSONResponse({'detail':'请求内容过大'},status_code=413)(scope,receive,safe_send)
            chunks.append(message)
            if not message.get('more_body',False):break
        index=0
        async def replay():
            nonlocal index
            if index<len(chunks):
                message=chunks[index];index+=1;return message
            return await receive()
        await self.app(scope,replay,safe_send)

"""Loopback-only, same-origin HTTP transport for the tablet processing engine."""
from __future__ import annotations
import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
import mimetypes
import os
from pathlib import Path
import secrets
import signal
import shutil
import socket
import sys
from urllib.parse import unquote, urlsplit
import webbrowser
from .engine import Engine, Conflict, MAX_UPLOAD, file_title

ROOT = Path(__file__).resolve().parents[2]
DESIGN = ROOT/'android-tablet'/'design'


class Server(HTTPServer):
    request_queue_size=64
    # Windows SO_REUSEADDR allows another listener to bind our live port.
    # Retain the normal HTTPServer restart behavior on other platforms.
    allow_reuse_address = not hasattr(socket, 'SO_EXCLUSIVEADDRUSE')

    def __init__(self, address, engine):
        self.engine=engine; self.token=secrets.token_urlsafe(32);self.stopping=False
        super().__init__(address, Handler)

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def get_request(self):
        connection,address=super().get_request()
        connection.settimeout(2)
        return connection,address


class Handler(BaseHTTPRequestHandler):
    # Native Qt processing stays on one owner thread. Closing each connection
    # prevents an idle keep-alive socket from blocking subsequent asset loads.
    protocol_version='HTTP/1.0'

    def log_message(self, fmt, *args):
        # Do not log note names, request payloads or document identifiers.
        pass

    def respond(self, status, data, mime='application/json; charset=utf-8', filename=None):
        if not isinstance(data,bytes):data=json.dumps(data,ensure_ascii=False,allow_nan=False).encode()
        try:
            self.payload_headers(status,len(data),mime,filename)
            self.wfile.write(data)
        except (ConnectionError,TimeoutError):
            # A browser may disappear after committing a pagehide journal.
            # The save is already durable; a closed response socket is benign.
            self.close_connection=True

    def payload_headers(self,status,length,mime,filename=None):
        self.send_response(status)
        self.send_header('Content-Type',mime);self.send_header('Content-Length',str(length))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        if filename:
            from urllib.parse import quote
            self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+quote(filename))
        self.end_headers()

    def respond_file(self,path,mime,filename):
        # Open before sending headers, so missing/unreadable files still get
        # a normal error response. Never buffer an entire NTB or PDF.
        with Path(path).open('rb') as source:
            length=os.fstat(source.fileno()).st_size
            try:
                self.payload_headers(200,length,mime,filename)
                shutil.copyfileobj(source,self.wfile,length=1024*1024)
            except OSError:
                # Headers may already be sent; don't append a JSON response
                # to a partial download. A short body remains detectable.
                self.close_connection=True

    def origin(self):
        host=self.headers.get('Host','')
        expected=f'127.0.0.1:{self.server.server_port}'
        if host != expected:raise PermissionError('仅接受本机服务入口')
        origin=self.headers.get('Origin')
        if origin and origin != 'http://'+expected:raise PermissionError('不允许跨站请求')
        if self.headers.get('Sec-Fetch-Site') in ('cross-site','same-site'):raise PermissionError('不允许跨站请求')

    def body(self):
        if self.headers.get('Transfer-Encoding'):raise ValueError('不支持分块请求')
        length=int(self.headers.get('Content-Length','0'))
        if not 0<length<=MAX_UPLOAD:raise ValueError('请求为空或超过大小限制')
        self.connection.settimeout(30)
        data=self.rfile.read(length)
        if len(data)!=length:raise ValueError('请求不完整')
        return data

    def do_POST(self):
        try:
            self.origin()
            if urlsplit(self.path).path=='/api/finish':
                obj=json.loads(self.body())
                if not isinstance(obj,dict) or not secrets.compare_digest(str(obj.get('token','')),self.server.token):
                    raise PermissionError('退出收尾凭据无效')
                self.server.engine.resume(obj.get('id',''))
                self.respond(200,{'saved':True});return
            if not secrets.compare_digest(self.headers.get('X-Tablet-Token',''),self.server.token):
                raise PermissionError('处理服务凭据失效，请刷新页面')
            route=urlsplit(self.path).path.split('/')
            engine=self.server.engine
            if route==['','api','open']:
                if self.headers.get('Transfer-Encoding'):raise ValueError('不支持分块请求')
                length=int(self.headers.get('Content-Length','0'));self.connection.settimeout(60)
                result=engine.open_stream(unquote(self.headers.get('X-Filename','')),self.rfile,length)
            else:
                data=self.body()
                obj=json.loads(data,parse_constant=lambda x:(_ for _ in ()).throw(ValueError('不允许非有限数字')))
                if not isinstance(obj,dict):raise ValueError('请求结构无效')
                if route==['','api','new']:
                    result=engine.new(obj.get('title'),obj.get('background','plain'))
                elif route==['','api','shutdown']:
                    failures=engine.flush_snapshots()
                    if failures:raise ValueError('仍有快照未成功写入，后台继续运行，请重试保存')
                    self.server.stopping=True;result={'saved':True,'stopping':True}
                elif len(route)==4 and route[1]=='api':
                    action,ident=route[2:]
                    if action=='snapshot':
                        engine.snapshot(ident,obj.get('note'),obj.get('revision')); result={'saved':True}
                    elif action=='save':
                        result=engine.save(ident,obj.get('note'),obj.get('revision'))
                    elif action=='resume':
                        result=engine.resume(ident)
                    else:raise ValueError('未知处理操作')
                else:raise ValueError('未知处理操作')
            self.respond(200,result)
        except PermissionError as exc:self.respond(403,{'error':str(exc)})
        except Conflict as exc:self.respond(409,{'error':str(exc)})
        except (ValueError,OSError,KeyError,TypeError) as exc:self.respond(400,{'error':str(exc)})
        except Exception:
            self.respond(500,{'error':'后台处理失败，原文件和快照保留，请重试'})

    def do_GET(self):
        try:
            self.origin(); path=urlsplit(self.path).path
            if path=='/api/bootstrap':
                self.respond(200,dict(token=self.server.token,version='0.7.9-Beta',formats=['NTB']));return
            route=path.split('/')
            if len(route)>=4 and route[1]=='api':
                doc=self.server.engine.get(route[3])
                if route[2]=='background' and len(route)==6:
                    self.respond(200,self.server.engine.background(doc.ident,route[4],int(route[5])),'image/png');return
                if route[2]=='download' and len(route)==4:
                    self.respond_file(doc.path,'application/octet-stream',file_title(doc.model['title'])+'.ntb');return
                if route[2]=='pdf' and len(route)==4:
                    self.respond_file(self.server.engine.export_pdf_path(doc.ident),'application/pdf',file_title(doc.model['title'])+'.pdf');return
                raise ValueError('未知资源')
            if path in ('/','/index.html'):
                self.send_response(302);self.send_header('Location','/home/index.html');self.send_header('Content-Length','0');self.end_headers();return
            if path=='/favicon.ico':
                self.respond(204,b'','image/x-icon');return
            if path.startswith('/rewritten/ui/'):
                base=ROOT/'rewritten'/'ui'; relative=path[len('/rewritten/ui/'):]
            else:
                base=DESIGN;relative=path.lstrip('/')
            target=(base/unquote(relative)).resolve()
            if not target.is_relative_to(base.resolve()) or target.suffix not in ('.html','.js','.css') or not target.is_file():
                self.respond(404,{'error':'资源不存在'});return
            self.respond(200,target.read_bytes(),mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
        except PermissionError as exc:self.respond(403,{'error':str(exc)})
        except Conflict as exc:self.respond(409,{'error':str(exc)})
        except (ValueError,OSError,KeyError,TypeError) as exc:self.respond(400,{'error':str(exc)})
        except Exception:self.respond(500,{'error':'后台资源读取失败'})


def main():
    parser=argparse.ArgumentParser(description='NotabilityEditor 本地 NTB 处理服务')
    parser.add_argument('--port',type=int,default=8767)
    parser.add_argument('--data-dir',type=Path,default=ROOT/'.tablet-data')
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    engine=Engine(args.data_dir)
    server=None
    try:
        try:
            server=Server(('127.0.0.1',args.port),engine)
        except OSError as exc:
            parser.exit(1,f'无法启动本地服务（端口 {args.port}）：{exc}\n')
        url=f'http://127.0.0.1:{server.server_port}/home/index.html'
        print(f'NotabilityEditor 0.7.9-Beta\n{url}\nLocal NTB workspace: {args.data_dir.resolve()}',flush=True)
        if not args.no_browser:webbrowser.open(url)
        stopping=False
        def stop(signum,frame):
            nonlocal stopping
            stopping=True
        signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
        if hasattr(signal,'SIGBREAK'):signal.signal(signal.SIGBREAK,stop)
        server.timeout=.3
        # handle_request runs on the Qt owner thread. Signal handlers request a
        # stop instead of aborting an active native write half way through.
        while True:
            while not stopping and not server.stopping:
                server.handle_request();engine.app.processEvents()
            failures=engine.flush_snapshots()
            if not failures:break
            print(f'{len(failures)} pending NTB snapshot(s) could not be written; service stays running.',file=sys.stderr,flush=True)
            stopping=False;server.stopping=False
    except KeyboardInterrupt:pass
    finally:
        try:
            # A rejected second instance must not flush the active instance's
            # journal or otherwise write documents from the shared data path.
            if server is not None:
                failures=engine.flush_snapshots()
                if failures:print(f'{len(failures)} pending NTB snapshot(s) retained for recovery.',file=sys.stderr,flush=True)
        finally:
            try:
                if server is not None:server.server_close()
            finally:
                engine.close()


if __name__=='__main__':main()


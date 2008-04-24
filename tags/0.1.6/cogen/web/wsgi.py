"""
This wsgi server is a single threaded, single process server that interleaves 
the iterations of the wsgi apps - I could add a threadpool for blocking apps in 
the future.

If you don't return iterators from apps and return lists you'll get, at most,
the performance of a server that processes requests sequentialy.

On the other hand this server has coroutine extensions that suppose to support 
use of middleware in your application. 

Example app with coroutine extensions:

{{{
def wait_app(environ, start_response):
  start_response('200 OK', [('Content-type','text/html')])
  yield "I'm waiting for some signal"
  yield environ['cogen'].core.events.WaitForSignal("abc", timeout=1)
  if isinstance(environ['cogen'].result, Exception):
    yield "Your time is up !"
  else:
    yield "Someone signaled me: %s" % environ['cogen'].result
}}}

  * `environ['cogen'].core` is actualy a wrapper that sets 
  `environ['cogen'].operation` with the called object and returns a empty 
  string. This should penetrate most of the middleware - according to the wsgi 
  spec, middleware should pass a empty string if it doesn't have anything to 
  return on that specific iteration point, or, in other words, the length of the
  app iter returned by middleware should be at least that of the app.
  
  * the wsigi server will set `environ['cogen'].result` with the result of the 
  operation and `environ['cogen'].exception` with the details of the 
  exception - if any: `(exc_type, exc_value, traceback_object)`.

HTTP handling code taken from the CherryPy WSGI server.
"""

# TODO: better application error reporting for the coroutine extensions

from __future__ import with_statement
__all__ = ['WSGIFileWrapper', 'WSGIServer', 'WSGIConnection']
from contextlib import closing

import base64
import Queue
import os
import re
import rfc822
import socket
import errno
try:                
  import cStringIO as StringIO
except ImportError: 
  import StringIO
import sys
import time
import traceback
from urllib import unquote
from urlparse import urlparse
from traceback import format_exc
import cogen

from cogen.common import *
#~ from cogen.wsgi \
import async

quoted_slash = re.compile("(?i)%2F")
useless_socket_errors = {}
for _ in ("EPIPE", "ETIMEDOUT", "ECONNREFUSED", "ECONNRESET",
      "EHOSTDOWN", "EHOSTUNREACH",
      "WSAECONNABORTED", "WSAECONNREFUSED", "WSAECONNRESET",
      "WSAENETRESET", "WSAETIMEDOUT"):
  if _ in dir(errno):
    useless_socket_errors[getattr(errno, _)] = None
useless_socket_errors = useless_socket_errors.keys()
comma_separated_headers = ['ACCEPT', 'ACCEPT-CHARSET', 'ACCEPT-ENCODING',
  'ACCEPT-LANGUAGE', 'ACCEPT-RANGES', 'ALLOW', 'CACHE-CONTROL',
  'CONNECTION', 'CONTENT-ENCODING', 'CONTENT-LANGUAGE', 'EXPECT',
  'IF-MATCH', 'IF-NONE-MATCH', 'PRAGMA', 'PROXY-AUTHENTICATE', 'TE',
  'TRAILER', 'TRANSFER-ENCODING', 'UPGRADE', 'VARY', 'VIA', 'WARNING',
  'WWW-AUTHENTICATE']

class WSGIFileWrapper:
  __doc_all__ = ['__init__']
  def __init__(self, filelike, blocksize=8192):
    self.filelike = filelike
    self.blocksize = blocksize
    if hasattr(filelike,'close'):
      self.close = filelike.close
  #TODO: iter method            
class tryclosing(object):
  __doc_all__ = ['__init__', '__enter__', '__exit__']
  """
  This is the exact context manager as contextlib.closing but it 
  doesn't throw a exception if the managed object doesn't have a
  close method.
  """
  def __init__(self, thing):
    self.thing = thing
  def __enter__(self):
    return self.thing
  def __exit__(self, *exc_info):
    if hasattr(self.thing, 'close'):
      self.thing.close()    
  
  
class WSGIPathInfoDispatcher(object):
  __doc_all__ = ['__init__', '__call__']
  """A WSGI dispatcher for dispatch based on the PATH_INFO.
  
  apps: a dict or list of (path_prefix, app) pairs.
  """
  
  def __init__(self, apps):
    try:
      apps = apps.items()
    except AttributeError:
      pass
    
    # Sort the apps by len(path), descending
    apps.sort()
    apps.reverse()
    
    # The path_prefix strings must start, but not end, with a slash.
    # Use "" instead of "/".
    self.apps = [(p.rstrip("/"), a) for p, a in apps]
  
  def __call__(self, environ, start_response):
    path = environ["PATH_INFO"] or "/"
    for p, app in self.apps:
      # The apps list should be sorted by length, descending.
      if path.startswith(p + "/") or path == p:
        environ = environ.copy()
        environ["SCRIPT_NAME"] = environ["SCRIPT_NAME"] + p
        environ["PATH_INFO"] = path[len(p):]
        return app(environ, start_response)
    
    start_response('404 Not Found', [('Content-Type', 'text/plain'),
                     ('Content-Length', '0')])
    return ['']

class WSGIConnection(object):
  __doc_all__ = [
    '__init__', 'start_response', 'render_headers', 
    'simple_response', 'run',
  ]
  connection_environ = {
    "wsgi.version": (1, 0),
    "wsgi.url_scheme": "http",
    "wsgi.multithread": True,
    "wsgi.multiprocess": False,
    "wsgi.run_once": False,
    "wsgi.errors": sys.stderr,
    "wsgi.input": None,
    "wsgi.file_wrapper": WSGIFileWrapper,
  }
  
  def __init__(self, sock, wsgi_app, environ):
    self.conn = sock
    self.wsgi_app = wsgi_app
    self.server_environ = environ
    
  def start_response(self, status, headers, exc_info = None):
    """WSGI callable to begin the HTTP response."""
    if self.started_response:
      if not exc_info:
        raise AssertionError("WSGI start_response called a second "
                   "time with no exc_info.")
      else:
        try:
          raise exc_info[0], exc_info[1], exc_info[2]
        finally:
          exc_info = None
    self.started_response = True
    self.status = status
    self.outheaders.extend(headers)
    
    return self.write_buffer.write
      
  def render_headers(self):
    hkeys = [key.lower() for key, value in self.outheaders]
    status = int(self.status[:3])
    
    if status == 413:
      # Request Entity Too Large. Close conn to avoid garbage.
      self.close_connection = True
    elif "content-length" not in hkeys:
      # "All 1xx (informational), 204 (no content),
      # and 304 (not modified) responses MUST NOT
      # include a message-body." So no point chunking.
      if status < 200 or status in (204, 205, 304):
        pass
      else:
        if self.response_protocol == 'HTTP/1.1':
          # Use the chunked transfer-coding
          self.chunked_write = True
          self.outheaders.append(("Transfer-Encoding", "chunked"))
        else:
          # Closing the conn is the only way to determine len.
          self.close_connection = True
    
    if "connection" not in hkeys:
      if self.response_protocol == 'HTTP/1.1':
        if self.close_connection:
          self.outheaders.append(("Connection", "close"))
      else:
        if not self.close_connection:
          self.outheaders.append(("Connection", "Keep-Alive"))
    
    if "date" not in hkeys:
      self.outheaders.append(("Date", rfc822.formatdate()))
    
    if "server" not in hkeys:
      self.outheaders.append(("Server", self.environ['SERVER_SOFTWARE']))
    
    buf = [self.environ['ACTUAL_SERVER_PROTOCOL'], " ", self.status, "\r\n"]
    try:
      buf += [k + ": " + v + "\r\n" for k, v in self.outheaders]
    except TypeError:
      if not isinstance(k, str):
        raise TypeError("WSGI response header key %r is not a string.")
      if not isinstance(v, str):
        raise TypeError("WSGI response header value %r is not a string.")
      else:
        raise
    buf.append("\r\n")
    return "".join(buf)

  def simple_response(self, status, msg=""):
    """Return a operation for writing simple response back to the client."""
    status = str(status)
    buf = ["%s %s\r\n" % (self.environ['ACTUAL_SERVER_PROTOCOL'], status),
         "Content-Length: %s\r\n" % len(msg),
         "Content-Type: text/plain\r\n"]
    
    if status[:3] == "413" and self.response_protocol == 'HTTP/1.1':
      # Request Entity Too Large
      self.close_connection = True
      buf.append("Connection: close\r\n")
    
    buf.append("\r\n")
    if msg:
      buf.append(msg)
    return sockets.WriteAll(self.conn, "".join(buf))
  def check_start_response(self):
    if self.started_response:
      if not self.sent_headers:
        self.sent_headers = True
        return sockets.WriteAll(self.conn, 
                self.render_headers()+self.write_buffer.getvalue()
              )
  
  @coroutine
  def run(self):
    """A bit bulky atm..."""
    self.close_connection = False
    with closing(self.conn):
      try:
        while True:
          self.started_response = False
          self.status = ""
          self.outheaders = []
          self.sent_headers = False
          self.chunked_write = False
          self.write_buffer = StringIO.StringIO()
          # Copy the class environ into self.
          self.environ = self.connection_environ.copy()
          self.environ.update(self.server_environ)
              
          request_line = yield sockets.ReadLine(self.conn)
          if request_line == "\r\n":
            # RFC 2616 sec 4.1: "... it should ignore the CRLF."
            tolerance = 5
            while tolerance and request_line == "\r\n":
              request_line = yield sockets.ReadLine(self.conn)
              tolerance -= 1
            if not tolerance:
              return
          method, path, req_protocol = request_line.strip().split(" ", 2)
          self.environ["REQUEST_METHOD"] = method
          self.environ["CONTENT_LENGTH"] = ''
          
          scheme, location, path, params, qs, frag = urlparse(path)
          
          if frag:
            yield self.simple_response("400 Bad Request",
                        "Illegal #fragment in Request-URI.")
            return
          
          if scheme:
            self.environ["wsgi.url_scheme"] = scheme
          if params:
            path = path + ";" + params
          
          self.environ["SCRIPT_NAME"] = ""
          
          # Unquote the path+params (e.g. "/this%20path" -> "this path").
          # http://www.w3.org/Protocols/rfc2616/rfc2616-sec5.html#sec5.1.2
          #
          # But note that "...a URI must be separated into its components
          # before the escaped characters within those components can be
          # safely decoded." http://www.ietf.org/rfc/rfc2396.txt, sec 2.4.2
          atoms = [unquote(x) for x in quoted_slash.split(path)]
          path = "%2F".join(atoms)
          self.environ["PATH_INFO"] = path
          
          # Note that, like wsgiref and most other WSGI servers,
          # we unquote the path but not the query string.
          self.environ["QUERY_STRING"] = qs
          
          # Compare request and server HTTP protocol versions, in case our
          # server does not support the requested protocol. Limit our output
          # to min(req, server). We want the following output:
          #     request    server     actual written   supported response
          #     protocol   protocol  response protocol    feature set
          # a     1.0        1.0           1.0                1.0
          # b     1.0        1.1           1.1                1.0
          # c     1.1        1.0           1.0                1.0
          # d     1.1        1.1           1.1                1.1
          # Notice that, in (b), the response will be "HTTP/1.1" even though
          # the client only understands 1.0. RFC 2616 10.5.6 says we should
          # only return 505 if the _major_ version is different.
          rp = int(req_protocol[5]), int(req_protocol[7])
          server_protocol = self.environ["ACTUAL_SERVER_PROTOCOL"]
          sp = int(server_protocol[5]), int(server_protocol[7])
          if sp[0] != rp[0]:
            yield self.simple_response("505 HTTP Version Not Supported")
            return
          # Bah. "SERVER_PROTOCOL" is actually the REQUEST protocol.
          self.environ["SERVER_PROTOCOL"] = req_protocol
          self.response_protocol = "HTTP/%s.%s" % min(rp, sp)
          
          # If the Request-URI was an absoluteURI, use its location atom.
          if location:
            self.environ["SERVER_NAME"] = location
          
          # then all the http headers
          try:
            while True:
              line = yield sockets.ReadLine(self.conn)
              
              if line == '\r\n':
                # Normal end of headers
                break
              
              if line[0] in ' \t':
                # It's a continuation line.
                v = line.strip()
              else:
                k, v = line.split(":", 1)
                k, v = k.strip().upper(), v.strip()
                envname = "HTTP_" + k.replace("-", "_")
              
              if k in comma_separated_headers:
                existing = self.environ.get(envname)
                if existing:
                  v = ", ".join((existing, v))
              self.environ[envname] = v
            
            ct = self.environ.pop("HTTP_CONTENT_TYPE", None)
            if ct:
              self.environ["CONTENT_TYPE"] = ct
            cl = self.environ.pop("HTTP_CONTENT_LENGTH", None)
            if cl:
              self.environ["CONTENT_LENGTH"] = cl
          except ValueError, ex:
            yield self.simple_response("400 Bad Request", repr(ex.args))
            return
          
          creds = self.environ.get("HTTP_AUTHORIZATION", "").split(" ", 1)
          self.environ["AUTH_TYPE"] = creds[0]
          if creds[0].lower() == 'basic':
            user, pw = base64.decodestring(creds[1]).split(":", 1)
            self.environ["REMOTE_USER"] = user
          
          # Persistent connection support
          if self.response_protocol == "HTTP/1.1":
            if self.environ.get("HTTP_CONNECTION", "") == "close":
              self.close_connection = True
          else:
            # HTTP/1.0
            if self.environ.get("HTTP_CONNECTION", "") != "Keep-Alive":
              self.close_connection = True
          
          # Transfer-Encoding support
          te = None
          if self.response_protocol == "HTTP/1.1":
            te = self.environ.get("HTTP_TRANSFER_ENCODING")
            if te:
              te = [x.strip().lower() for x in te.split(",") if x.strip()]
          
          read_chunked = False
          
          if te:
            for enc in te:
              if enc == "chunked":
                read_chunked = True
              else:
                # Note that, even if we see "chunked", we must reject
                # if there is an extension we don't recognize.
                yield self.simple_response("501 Unimplemented")
                self.close_connection = True
                return
          
          self.environ['cogen.wsgi'] = async.COGENProxy(
            read_chunked = read_chunked,
            content_length = int(self.environ.get('CONTENT_LENGTH', None) or 0) or None,
            read_count = 0,
            state = async.Read.NEED_SIZE,
            chunk_remaining = 0,
            operation = None,
            result = None,
            exception = None
          )
          self.environ['cogen.core'] = async.COGENOperationWrapper(
            self.environ['cogen.wsgi'], 
            cogen.core
          )
          self.environ['cogen.input'] = async.COGENOperationWrapper(
            self.environ['cogen.wsgi'], 
            async.COGENProxy( 
              Read = lambda len, **kws: \
                async.Read(self.conn, self.environ['cogen.wsgi'], len, **kws),
              ReadLine = lambda len, **kws: \
                async.ReadLine(self.conn, self.environ['cogen.wsgi'], len, **kws)
            )
          )
          response = self.wsgi_app(self.environ, self.start_response)
          with tryclosing(response):
            csr = self.check_start_response()
            if csr: 
              yield csr
            del csr
            if isinstance(response, WSGIFileWrapper):
              yield sockets.SendFile( response.filelike, self.conn, 
                                      blocksize=response.blocksize )#, timeout=-1)
              #XXX: use content-length 
              #XXX: use chunking
              
            else:
              for chunk in response:
                csr = self.check_start_response()
                if csr: 
                  yield csr
                del csr
                if chunk:
                  if self.chunked_write:
                    buf = [hex(len(chunk))[2:], "\r\n", chunk, "\r\n"]
                    yield sockets.WriteAll(self.conn, "".join(buf))
                  else:
                    yield sockets.WriteAll(self.conn, chunk)
                else:
                  if self.environ['cogen.wsgi'].operation:
                    op = self.environ['cogen.wsgi'].operation
                    self.environ['cogen.wsgi'].operation = None
                    try:
                      #~ print 'WSGI OP:', op
                      self.environ['cogen.wsgi'].result = yield op
                      #~ print 'WSGI OP RESULT:',self.environ['cogen.wsgi'].result
                    except:
                      #~ print 'exception:', sys.exc_info()
                      self.environ['cogen.wsgi'].exception = sys.exc_info()
                      self.environ['cogen.wsgi'].result = \
                        self.environ['cogen.wsgi'].exception[1]
                    del op
          
          if self.chunked_write:
            yield sockets.WriteAll(self.conn, "0\r\n\r\n")
        
          if self.close_connection:
            return
          while (yield async.Read(self.conn, self.environ['cogen.wsgi'])):
            # we need to consume any unread input data to read the next 
            #pipelined request
            pass
      except socket.error, e:
        errno = e.args[0]
        if errno not in useless_socket_errors:
          yield self.simple_response("500 Internal Server Error",
                      format_exc())
        return
      except (events.OperationTimeout, 
          events.ConnectionClosed, 
          events.ConnectionError):
        return
      except (KeyboardInterrupt, SystemExit):
        raise
      except:
        if not self.started_response:
          yield self.simple_response(
            "500 Internal Server Error", 
            format_exc()
          )
        else:
          print "*" * 60
          traceback.print_exc()
          print "*" * 60
      finally:
        self.environ = None
class WSGIServer(object):
  """
  An HTTP server for WSGI.
  || Option || Description ||
  || bind_addr || The interface on which to listen for connections. For TCP sockets, a (host, port) tuple. Host values may be any IPv4 or IPv6 address, or any valid hostname. The string 'localhost' is a synonym for '127.0.0.1' (or '::1', if your hosts file prefers IPv6). The string '0.0.0.0' is a special IPv4 entry meaning "any active interface" (INADDR_ANY), and '::' is the similar IN6ADDR_ANY for IPv6. The empty string or None are not allowed.  For UNIX sockets, supply the filename as a string. ||
  || wsgi_app || the WSGI 'application callable'; multiple WSGI applications may be passed as (path_prefix, app) pairs. ||
  || server_name || the string to set for WSGI's SERVER_NAME environ entry. Defaults to socket.gethostname(). ||
  || request_queue_size || the 'backlog' argument to socket.listen() specifies the maximum number of queued connections (default 5). ||
  || protocol || the version string to write in the Status-Line of all HTTP responses. For example, "HTTP/1.1" (the default). This also limits the supported features used in the response. ||
  """
  
  protocol = "HTTP/1.1"
  _bind_addr = "localhost"
  version = "cogen.web.wsgi/%s Python/%s" % (cogen.__version__, sys.version.split()[0])
  ready = False
  ConnectionClass = WSGIConnection
  environ = {}
  
  def __init__(self, bind_addr, wsgi_app, scheduler, server_name=None, request_queue_size=5):
    self.request_queue_size = int(request_queue_size)
    self.scheduler = scheduler
    if callable(wsgi_app):
      # We've been handed a single wsgi_app, in CP-2.1 style.
      # Assume it's mounted at "".
      self.wsgi_app = wsgi_app
    else:
      # We've been handed a list of (path_prefix, wsgi_app) tuples,
      # so that the server can call different wsgi_apps, and also
      # correctly set SCRIPT_NAME.
      self.wsgi_app = WSGIPathInfoDispatcher(wsgi_app)
    
    self.bind_addr = bind_addr
    if not server_name:
      server_name = socket.gethostname()
    self.server_name = server_name
    
  def __str__(self):
    return "%s.%s(%r)" % (self.__module__, self.__class__.__name__,
                self.bind_addr)
  
  def _get_bind_addr(self):
    return self._bind_addr
  def _set_bind_addr(self, value):
    if isinstance(value, tuple) and value[0] in ('', None):
      # Despite the socket module docs, using '' does not
      # allow AI_PASSIVE to work. Passing None instead
      # returns '0.0.0.0' like we want. In other words:
      #     host    AI_PASSIVE     result
      #      ''         Y         192.168.x.y
      #      ''         N         192.168.x.y
      #     None        Y         0.0.0.0
      #     None        N         127.0.0.1
      # But since you can get the same effect with an explicit
      # '0.0.0.0', we deny both the empty string and None as values.
      raise ValueError("Host values of '' or None are not allowed. "
               "Use '0.0.0.0' (IPv4) or '::' (IPv6) instead "
               "to listen on all active interfaces.")
    self._bind_addr = value
  bind_addr = property(_get_bind_addr, _set_bind_addr,
    doc="""The interface on which to listen for connections.
    
    For TCP sockets, a (host, port) tuple. Host values may be any IPv4
    or IPv6 address, or any valid hostname. The string 'localhost' is a
    synonym for '127.0.0.1' (or '::1', if your hosts file prefers IPv6).
    The string '0.0.0.0' is a special IPv4 entry meaning "any active
    interface" (INADDR_ANY), and '::' is the similar IN6ADDR_ANY for
    IPv6. The empty string or None are not allowed.
    
    For UNIX sockets, supply the filename as a string.""")
    
  @coroutine
  def serve(self):
    """Run the server forever."""
    # We don't have to trap KeyboardInterrupt or SystemExit here,
    
    # Select the appropriate socket
    if isinstance(self.bind_addr, basestring):
      # AF_UNIX socket
      
      # So we can reuse the socket...
      try: os.unlink(self.bind_addr)
      except: pass
      
      # So everyone can access the socket...
      try: os.chmod(self.bind_addr, 0777)
      except: pass
      
      info = [(socket.AF_UNIX, socket.SOCK_STREAM, 0, "", self.bind_addr)]
    else:
      # AF_INET or AF_INET6 socket
      # Get the correct address family for our host (allows IPv6 addresses)
      host, port = self.bind_addr
      try:
        info = socket.getaddrinfo(host, port, socket.AF_UNSPEC,
                      socket.SOCK_STREAM, 0, socket.AI_PASSIVE)
      except socket.gaierror:
        # Probably a DNS issue. Assume IPv4.
        info = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", self.bind_addr)]
    
    self.socket = None
    msg = "No socket could be created"
    for res in info:
      af, socktype, proto, canonname, sa = res
      try:
        self.bind(af, socktype, proto)
      except socket.error, msg:
        if self.socket:
          self.socket.close()
        self.socket = None
        continue
      break
    if not self.socket:
      raise socket.error, msg
    
    self.socket.listen(self.request_queue_size)
    with closing(self.socket):
      while True:
        s, addr = yield sockets.Accept(self.socket, timeout=-1)
         
        environ = self.environ.copy()
        environ["SERVER_SOFTWARE"] = self.version
        # set a non-standard environ entry so the WSGI app can know what
        # the *real* server protocol is (and what features to support).
        # See http://www.faqs.org/rfcs/rfc2145.html.
        environ["ACTUAL_SERVER_PROTOCOL"] = self.protocol
        environ["SERVER_NAME"] = self.server_name
        
        if isinstance(self.bind_addr, basestring):
          # AF_UNIX. This isn't really allowed by WSGI, which doesn't
          # address unix domain sockets. But it's better than nothing.
          environ["SERVER_PORT"] = ""
        else:
          environ["SERVER_PORT"] = str(self.bind_addr[1])
          # optional values
          # Until we do DNS lookups, omit REMOTE_HOST
          environ["REMOTE_ADDR"] = addr[0]
          environ["REMOTE_PORT"] = str(addr[1])
        
        conn = self.ConnectionClass(s, self.wsgi_app, environ)
        yield events.AddCoro(conn.run, prio=priority.CORO)
        #TODO: how scheduling ?

   
  def bind(self, family, type, proto=0):
    """Create (or recreate) the actual socket object."""
    self.socket = sockets.Socket(family, type, proto)
    self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    self.socket.setblocking(0)
    #~ self.socket.setsockopt(socket.SOL_SOCKET, socket.TCP_NODELAY, 1)
    self.socket.bind(self.bind_addr)
  
def server_factory(global_conf, host, port, **options):
  port = int(port)
  def serve(app):
    sched = Scheduler(
      poller = getattr(
        cogen.core.pollers, 
        options.get('scheduler.poller', 'DefaultPoller')
      ), 
      default_priority = 
        int(options.get('scheduler.default_priority', priority.FIRST)), 
      default_timeout = 
        int(options.get('scheduler.default_timeout', 15))
    )
    server = WSGIServer( 
      (host, port), 
      app, 
      sched, 
      server_name=host, 
      **dict(
        [(k.split('.',1)[1],v) for k,v in options.items() if k.startswith('wsgi_server.')]
      )
    )
    sched.add(server.serve)
    sched.run()
  return serve
  
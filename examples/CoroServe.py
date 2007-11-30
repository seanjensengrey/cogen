import sys, os
sys.path.append(os.path.split(os.getcwd())[0])

try:
    import psyco
    psyco.full()
except ImportError:
    pass

from cogen.web.httpserver import *
from cogen.common import *
import sys
import string

def test(ServerClass = HTTPServer, protocol="HTTP/1.1"):
    class HandlerClass(BaseHTTPRequestHandler):
        def version_string(t):
            return "CoroServe/%s-%s %s" % (
                    filter(lambda x:x.isupper(),t.server.m.__class__.__name__), 
                    filter(lambda x:x.isupper(),t.server.m.poll.__class__.__name__), 
                    t.sys_version
                )
        @coroutine
        def do_GET(t):
            yield events.Call(t.send_response,args=(200,))
            yield events.Call(t.send_header,args=("Content-type", "text/html"))
            yield events.Call(t.end_headers)
            yield sockets.Write(sock=t.request, buff="""
                Lorem ipsum dolor sit amet, consectetuer adipiscing elit. Donec commodo tincidunt urna. Phasellus commodo metus sit amet dolor. Fusce vitae sapien. Donec consectetuer nonummy ipsum. Donec enim enim, placerat nec, faucibus eget, commodo a, risus. Cras tincidunt. Lorem ipsum dolor sit amet, consectetuer adipiscing elit. Donec eros leo, adipiscing a, ornare at, imperdiet vel, arcu. Cum sociis natoque penatibus et magnis dis parturient montes, nascetur ridiculus mus. In feugiat lacus eu leo. Etiam bibendum urna nec metus. Fusce egestas accumsan nulla. Nulla molestie auctor arcu. Curabitur mi urna, imperdiet sed, consectetuer fermentum, iaculis vitae, velit.

                Nam in nisi. Cras orci. Praesent ac eros ut diam consectetuer sagittis. Etiam augue ligula, egestas elementum, porta vitae, gravida nec, nisl. Ut lacinia. Etiam bibendum lacus et est. Aenean interdum. Nulla nec libero. Fusce viverra lectus in orci. Nam et erat. Praesent nulla mauris, molestie ac, fermentum sed, luctus vel, leo. Pellentesque habitant morbi tristique senectus et netus et malesuada fames ac turpis egestas. Etiam pharetra mauris non justo.

                Fusce tellus nisl, sodales at, semper id, adipiscing eu, purus. Aliquam sed urna. Aliquam dapibus augue et elit. Donec volutpat risus in sapien. In hac habitasse platea dictumst. Nunc pharetra. Sed vulputate vulputate justo. Vestibulum ante ipsum primis in faucibus orci luctus et ultrices posuere cubilia Curae; Nunc nisi. Maecenas purus.

                Nullam massa purus, placerat ac, aliquam ut, dapibus id, neque. Nullam hendrerit eros id urna. In hac habitasse platea dictumst. In suscipit. In nonummy. Etiam ultrices arcu vel nunc. Donec feugiat massa at dolor. Mauris facilisis lectus vel libero. Vestibulum viverra mattis nisi. Sed dapibus. Nunc ligula turpis, ultrices nec, laoreet vel, fermentum eu, justo. Fusce volutpat, lorem vel fermentum sodales, sapien augue auctor risus, sed consectetuer metus urna in nisl. Nulla nec lorem vitae nunc placerat tempus. Pellentesque non pede. Ut mollis velit sit amet risus vestibulum eleifend. Suspendisse vel sem. Duis accumsan varius erat. Donec sodales. Proin justo.

                Nulla quis felis. Mauris ultrices, arcu ut blandit eleifend, arcu odio porta libero, a posuere mi nisi nec elit. Phasellus in neque. In diam eros, venenatis quis, consectetuer et, ultrices non, arcu. Ut pretium, turpis a imperdiet suscipit, sapien tellus venenatis urna, feugiat egestas ante neque a orci. Cras orci sapien, porta et, pharetra sit amet, dignissim nonummy, lacus. Suspendisse facilisis turpis quis ante venenatis egestas. Sed et magna nec eros suscipit varius. Aliquam eu pede ut lectus rutrum bibendum. Ut ac tellus. Fusce porta dictum augue. Nunc vel nibh nec nulla pulvinar tempus. Mauris in tortor. Proin vehicula. Pellentesque consequat. Sed metus augue, condimentum eget, iaculis a, aliquam in, tellus.
            """)
            #~ print '---- DONE ----'
    if sys.argv[1:]:
        port = int(sys.argv[1])
    else:
        port = 5000
    server_address = ('', port)

    HandlerClass.protocol_version = protocol
    httpd = ServerClass(server_address, HandlerClass)
    httpd.allow_reuse_address = True

    sa = httpd.socket.getsockname()
    print "Serving HTTP on", sa[0], "port", sa[1], "..."
    httpd.quickstart(default_priority=3)


if __name__ == '__main__':
    import cProfile
    cProfile.run("test()", "cprofile.log")
    import pstats
    for i in ['calls','cumulative','file','module','pcalls','line','name','nfl','stdname','time']:
        stats = pstats.Stats("cprofile.log",stream = file('cprofile.%s.txt' %i,'w'))
        stats.sort_stats(i)
        stats.print_stats()
    #~ test()
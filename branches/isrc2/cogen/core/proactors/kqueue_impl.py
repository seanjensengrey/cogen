from __future__ import division
import kqueue, time, sys

from base import ProactorBase, perform_recv, perform_accept, perform_send, \
                                perform_sendall, perform_sendfile, \
                                perform_connect
from cogen.core import sockets
from cogen.core.util import priority
from cogen.core import events

class KQueueProactor(ProactorBase):
    def __init__(self, scheduler, res, default_size=1024, **options):
        super(self.__class__, self).__init__(scheduler, res, **options)
        self.kq = kqueue.kqueue()
    
    def unregister_fd(self, act):
        try:
            flag =  kqueue.EVFILT_READ if performer == perform_recv \
                    or performer == perform_accept else kqueue.EVFILT_WRITE 
            ev = kqueue.EV_SET(fileno, flag, kqueue.EV_DELETE)
            self.kq.kevent(ev)
        except OSError, e:
            import warnings
            warnings.warn("fd remove error: %r" % e)

    def register_fd(self, act, performer):
        fileno = act.sock.fileno()
        flag =  kqueue.EVFILT_READ if performer == perform_recv \
                or performer == perform_accept else kqueue.EVFILT_WRITE 
        ev = kqueue.EV_SET(
            fileno, flag, 
            kqueue.EV_ADD | kqueue.EV_ENABLE | kqueue.EV_ONESHOT
        )
        ev.udata = act
        self.kq.kevent(ev)

    def run(self, timeout = 0):
        """ 
        Run a proactor loop and return new socket events. Timeout is a timedelta 
        object, 0 if active coros or None. 
        
        kqueue timeout param is a integer number of nanoseconds (seconds/10**9).
        """
        ptimeout = int(
            timeout.days*86400000000000 + 
            timeout.microseconds*1000 + 
            timeout.seconds*1000000000 
            if timeout else (self.n_resolution if timeout is None else 0)
        )
        if ptimeout>sys.maxint:
            ptimeout = sys.maxint
        if self.tokens:
            events = self.kq.kevent(None, self.default_size, ptimeout)
            # should check here if timeout isn't negative or larger than maxint
            len_events = len(events)-1
            for nr, ev in enumerate(events):
                fd = ev.ident
                act = ev.udata
                
                if ev.flags & kqueue.EV_ERROR:
                    self.handle_error_event(act, 'System error %s.'%ev.data)
                else:
                    if nr == len_events:
                        return self.yield_event(act)
                    else:
                        if not self.handle_event(act):
                            ev.flags = kqueue.EV_ADD | kqueue.EV_ENABLE | kqueue.EV_ONESHOT
                            self.kq.kevent(ev)
        
            
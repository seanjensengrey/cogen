"""
Scheduling framework.
"""

import collections
import datetime
import heapq
import weakref
import sys                

from cogen.core.pollers import DefaultPoller
from cogen.core import events
from cogen.core import sockets
from cogen.core.util import debug, TimeoutDesc, priority

class DebugginWrapper:
    def __init__(self, obj):
        self.obj = obj
    
    def __getattr__(self, name):
        if 'append' in name:
            return debug(0)(getattr(self.obj, name))
        else:
            return getattr(self.obj, name)
class Timeout(object):
    __slots__ = [
        'coro', 'op', 'timeout', 'weak_timeout', 
        'delta', 'last_checkpoint'
    ]
    def __init__(self, op, coro, weak_timeout=False):
        assert isinstance(op.timeout, datetime.datetime)
        self.timeout = op.timeout
        self.last_checkpoint = datetime.datetime.now()
        self.delta = self.timeout - self.last_checkpoint
        self.coro = weakref.ref(coro)
        self.op = weakref.ref(op)
        self.weak_timeout = weak_timeout
        
    def __cmp__(self, other):
        return cmp(self.timeout, other.timeout)    
    def __repr__(self):
        return "<%s@%s timeout:%s, coro:%s, op:%s, weak:%s, lastcheck:%s, delta:%s>" % (
            self.__class__.__name__, 
            id(self), 
            self.timeout, 
            self.coro(), 
            self.op(), 
            self.weak_timeout, 
            self.last_checkpoint, 
            self.delta
        )

class Scheduler(object):
    def __init__(self, poller=DefaultPoller, default_priority=priority.LAST, default_timeout=None):
        self.timeouts = []
        self.active = collections.deque()
        self.sigwait = collections.defaultdict(collections.deque)
        self.signals = collections.defaultdict(collections.deque)
        self.timewait = [] # heapq
        self.poll = poller(self)
        self.default_priority = default_priority
        self.default_timeout = default_timeout
        self.running = False
    def __repr__(self):
        return "<%s@0x%X active:%s sigwait:%s timewait:%s poller:%s default_priority:%s default_timeout:%s>" % (
            self.__class__.__name__, 
            id(self), 
            len(self.active), 
            len(self.sigwait), 
            len(self.timewait), 
            self.poll, 
            self.default_priority, 
            self.default_timeout
        )
    def add(self, coro, args=(), kwargs={}, first=True):
        assert callable(coro), "Coroutine not a callable object"
        coro = coro(*args, **kwargs)
        if first:
            self.active.append( (None, coro) )
        else:
            self.active.appendleft( (None, coro) )    
        return coro
       
    def run_timer(self):
        if self.timewait:
            now = datetime.datetime.now() 
            while self.timewait and self.timewait[0].wake_time <= now:
                op = heapq.heappop(self.timewait)
                self.active.appendleft((op, op.coro))
    
    def next_timer_delta(self): 
        if self.timewait and not self.active:
            return (datetime.datetime.now() - self.timewait[0].wake_time)
        else:
            if self.active:
                return 0
            else:
                return None
    def run_poller(self):
        if self.poll:
            self.poll.run(timeout = self.next_timer_delta())

    def add_timeout(self, op, coro, weak_timeout):
        heapq.heappush(self.timeouts, Timeout(op, coro, weak_timeout))
    def handle_timeouts(self):
        if self.timeouts:
            now = datetime.datetime.now()
            #~ print '>to:', self.timeouts, self.timeouts and self.timeouts[0].timeout <= now
            while self.timeouts and self.timeouts[0].timeout <= now:
                timo = heapq.heappop(self.timeouts)
                op, coro = timo.op(), timo.coro()
                if op:
                    #~ print timo
                    if timo.weak_timeout and hasattr(op, 'last_update'):
                        if op.last_update > timo.last_checkpoint:
                            timo.last_checkpoint = op.last_update
                            timo.timeout = timo.last_checkpoint + timo.delta
                            heapq.heappush(self.timeouts, timo)
                            continue
                    
                    if isinstance(op, sockets.SocketOperation):
                        self.poll.remove(op, coro)
                    elif coro and isinstance(op, events.Join):
                        op.coro.remove_waiter(coro)
                    elif isinstance(op, events.WaitForSignal):
                        try:
                            self.sigwait[op.name].remove((op, coro))
                        except ValueError:
                            pass
                    if not op.finalized and coro and coro.running:
                        self.active.append((
                            events.CoroutineException((
                                events.OperationTimeout, 
                                events.OperationTimeout(op)
                            )), 
                            coro
                        ))
    #~ @debug(0)        
    def process_op(self, op, coro):
        if op is None:
            if self.active:
                self.active.append((op, coro))
            else:
                return op, coro 
        else:
            try:
                result = op.process(self, coro) or (None, None)
            except:
                result = events.CoroutineException(sys.exc_info()), coro
            return result
        return None, None
        
    def run(self):
        self.running = True
        while self.running and (self.active or self.poll or self.timewait):
            if self.active:
                #~ print 'ACTIVE:', self.active
                op, coro = self.active.popleft()
                while True:
                    #~ print coro, op
                    op, coro = self.process_op(coro.run_op(op), coro)
                    if not op and not coro:
                        break  
                    
            self.run_poller()
            self.run_timer()
            self.handle_timeouts()
            #~ print 'active:  ',len(self.active)
            #~ print 'poll:    ',len(self.poll)
            #~ print 'timeouts:',len(self.poll._timeouts)
    def stop(self):
        self.running = False
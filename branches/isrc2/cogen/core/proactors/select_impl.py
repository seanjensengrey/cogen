from __future__ import division
import select, time, itertools

from base import ProactorBase
from cogen.core import sockets
from cogen.core.util import priority

class SelectProactor(ProactorBase):
    def run(self, timeout = 0):
        """ 
        Run a proactor loop and return new socket events. Timeout is a timedelta 
        object, 0 if active coros or None. 
        
        select timeout param is a float number of seconds.
        """
        ptimeout = timeout.days*86400 + timeout.microseconds/1000000 + timeout.seconds \
                if timeout else (self.resolution if timeout is None else 0)
        if self.tokens:
            ready_to_read, ready_to_write, in_error = select.select(
                [act for act in self.tokens 
                    if self.tokens[act] == self.perform_recv
                    or self.tokens[act] == self.perform_accept], 
                [act for act in self.tokens 
                    if self.tokens[act] == self.perform_send 
                    or self.tokens[act] == self.perform_sendall
                    or self.tokens[act] == self.perform_connect], 
                [act for act in self.tokens], 
                ptimeout
            )
            for act in in_error:
                self.handle_error_event(act, 'Unknown error.')
            last_act = None
            for act in itertools.chain(ready_to_read, ready_to_write):
                if last_act:
                    if self.handle_event(last_act):
                        del self.tokens[last_act]
                last_act = act
            return self.yield_event(last_act)
        else:
            time.sleep(self.resolution)
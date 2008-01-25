import unittest
import sys
import exceptions
import datetime

from cStringIO import StringIO

from cogen.common import *
from cogen.test.base import PrioMixIn, NoPrioMixIn

class SchedulerTest_MixIn:
    def setUp(self):
        self.m = self.scheduler(default_priority=self.prio)
        self.msgs = []
        
    def tearDown(self):
        pass
    def test_signal(self):
        class X:
            pass
        x = X()
        @coroutine
        def signalee():
            self.msgs.append(1)
            yield events.WaitForSignal("test_sig")
            self.msgs.append(3)
            yield events.WaitForSignal(x)
            self.msgs.append(5)
        @coroutine
        def signaler():
            self.msgs.append(2)
            yield events.Signal("test_sig")
            self.msgs.append(4)
            yield events.Signal(x, recipients=1)
            self.msgs.append(6)
            
        self.m.add(signalee)
        self.m.add(signaler)
        self.m.run()
        if self.prio:
            self.assertEqual(self.msgs, [1,2,4,3,6,5])
        else:
            self.assertEqual(self.msgs, [1,2,3,4,5,6])
    def test_add_coro(self):
        @coroutine
        def added(x):
            self.msgs.append(x)
        @coroutine
        def adder(c):
            self.msgs.append(1)
            yield events.AddCoro(c, args=(self.prio and 3 or 2,))
            self.msgs.append(self.prio and 2 or 3)
        self.m.add(adder, args=(added,))
        self.m.run()
        self.assertEqual(self.msgs, [1,2,3])
    def test_call(self):
        @coroutine
        def caller():
            self.msgs.append(1)
            ret = yield events.Call(callee_1)
            self.msgs.append(ret)
            ret = yield events.Call(callee_2)
            self.msgs.append(ret is None and 3 or -1)
            try:
                ret = yield events.Call(callee_3)
            except Exception, e:
                self.msgs.append(e.message=='some_message' and 4 or -1)
             
            ret = yield events.Call(callee_4)
            self.msgs.append(ret)
            try:
                ret = yield events.Call(callee_5)
            except:
                import traceback
                s = traceback.format_exc()
                self.exc = s

            ret = yield events.Call(callee_6, args=(6,))
            self.msgs.append(ret)
            
        @coroutine
        def callee_1():
            raise StopIteration(2)
        @coroutine
        def callee_2():
            pass
        @coroutine
        def callee_3():
            yield
            raise Exception("some_message")
            yield
            
        @coroutine
        def callee_4():
            raise StopIteration((yield events.Call(callee_4_1)))
        @coroutine
        def callee_4_1():
            raise StopIteration((yield events.Call(callee_4_2)))
        @coroutine
        def callee_4_2():
            raise StopIteration(5)
        
        @coroutine
        def callee_5():
            raise StopIteration((yield events.Call(callee_5_1)))
        @coroutine
        def callee_5_1():
            raise StopIteration((yield events.Call(callee_5_2)))
        @coroutine
        def callee_5_2():
            raise Exception("long_one")
        
        @coroutine
        def callee_6(x):
            raise StopIteration(x)
            
        
        self.m.add(caller)
        self.m.run()
        self.assertEqual(self.msgs, [1,2,3,4,5,6])
        self.assert_('raise StopIteration((yield events.Call(callee_5_1)))' in self.exc)
        self.assert_('raise StopIteration((yield events.Call(callee_5_2)))' in self.exc)
        self.assert_('raise Exception("long_one")' in self.exc)
    def test_join(self):
        @coroutine
        def caller():
            self.msgs.append(1)
            ret = yield events.Join(self.m.add(callee_1))
            self.msgs.append(ret)
            ret = yield events.Join(self.m.add(callee_2))
            self.msgs.append(3 if ret is None else -1)
            #~ try:
            self.c = self.m.add(callee_3)
            sys.stderr = StringIO()
            #~ self.c.handle_error=lambda*a:None
            ret = yield events.Join(self.c)
            sys.stderr = sys.__stderr__
            self.msgs.append(
                4 
                if ret is None and self.c.exception[1].message=='some_message' 
                else -1
            )
            
            
        @coroutine
        def callee_1():
            raise StopIteration(2)
        @coroutine
        def callee_2():
            pass
        @coroutine
        def callee_3():
            yield
            raise Exception("some_message")
            yield
        self.m.add(caller)
        self.m.run()
        self.assertEqual(self.msgs, [1,2,3,4])

class SchedulerTest_Prio(SchedulerTest_MixIn, PrioMixIn, unittest.TestCase):
    scheduler = Scheduler
class SchedulerTest_NoPrio(SchedulerTest_MixIn, NoPrioMixIn, unittest.TestCase):
    scheduler = Scheduler
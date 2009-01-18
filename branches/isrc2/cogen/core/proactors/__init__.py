"""
Network code.
"""

def has_select():
    try:
        import select
        import select_impl
        return select_impl.SelectProactor
    except ImportError:
        pass
    

def has_poll():
    try:
        import select
        if select and hasattr(select, 'poll'):
            import poll_impl
            return poll_impl.PollProactor
    except ImportError:
        pass
    

def has_epoll():
    try:
        import epoll
        import epoll_impl
        return epoll_impl.EpollProactor
    except ImportError:
        pass

def has_kqueue():
    try:
        import kqueue
        if kqueue.PYKQ_VERSION.split('.')[0] != '2':
            raise ImportError("%s too old."%kqueue.PYKQ_VERSION)
        import kqueue_impl
        return kqueue_impl.KQueueProactor
    except ImportError:
        pass

def has_iocp():
    try:
        import win32file
        import win32event
        import win32api
        import pywintypes
        import socket
        import ctypes
        import struct       
        import iocp_impl
        return iocp_impl.IOCPProactor
    except ImportError:
        pass
        
def get_first(*imps):
    "Returns the first result that evaluates to true from a list of callables."
    for imp in imps:
        proactor = imp()
        if proactor:
            return proactor

def has_any():
    "Returns the best available proactor implementation for the current platform."
    return get_first(has_iocp, has_kqueue, has_epoll, has_poll, has_select)
         #, has_iocp, has_kqueue, has_epoll, has_poll, has_select)

DefaultProactor = has_any()
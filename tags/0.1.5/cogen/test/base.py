from cogen.common import *

class PrioMixIn:
    prio = priority.FIRST
class NoPrioMixIn:
    prio = priority.LAST
    
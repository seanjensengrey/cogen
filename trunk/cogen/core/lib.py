
class SimpleAttrib:
    def __init__(t, **kws):
        t.__dict__.update(kws)
class SimpleArgs:
    def __init__(t, *args, **kws):
        t.args = args
        t.kws = kws


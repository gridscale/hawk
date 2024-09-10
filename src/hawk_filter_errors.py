from gshawk.vars import filters

def error(msg):
    raise ValueError(msg)

filters['hawk.utils.error'] = error

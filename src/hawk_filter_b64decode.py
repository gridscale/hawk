import base64
from gshawk.vars import filters

def decode(s):
    return str(base64.b64decode(s).decode('utf-8'))
def encode(s):
    return str(base64.b64encode(s.encode('utf-8')).decode('utf-8'))

filters['hawk.utils.b64decode'] = decode
filters['hawk.utils.b64encode'] = encode

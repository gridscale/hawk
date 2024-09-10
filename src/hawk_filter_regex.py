import re
from gshawk.vars import filters

def regex_replace(s, find, replace):
    return re.sub(find, replace, s)
 
filters['hawk.utils.regex_replace'] = regex_replace

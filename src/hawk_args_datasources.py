from gshawk.vars import parser
import os
import sys

def get_default_cache_dir():
    """Return default cache directory based on OS and user privileges."""
    if os.getuid() == 0:
        if sys.platform == 'darwin':
            return '/Library/Caches/gs-hawk'
        else:
            return '/var/cache/gs-hawk'
    else:
        return os.path.expanduser('~/.cache/gs-hawk')

parser.add_argument('--config', metavar='<path>', type=str, action='store',
                    help='path to datasource configuration JSON', default="/etc/gs-hawk.json", required=False)
parser.add_argument('--cache-dir', metavar='<path>', type=str, action='store',
                    help='directory for datasource cache', default=get_default_cache_dir(), required=False)

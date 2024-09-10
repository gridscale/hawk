from gshawk.vars import parser

parser.add_argument('--source', metavar='<path>', type=str, action='store',
                    help='root dir of Hawk feathers', default="/usr/share/gs-hawk", required=False)
parser.add_argument('--dry-run', nargs='?', const=True, type=bool, action='store',
                    help='Do not write output files', default=False, required=False)
parser.add_argument('--no-diff', nargs='?', const=True, type=bool, action='store',
                    help='Do not show diff', default=False, required=False)
parser.add_argument('--target', metavar='<path>', type=str, action='store',
                    help='root dir of Hawk output', default="/", required=False)
parser.add_argument('--skip-systemd', nargs='?', const=True, type=bool, action='store',
                    help='skip systemd executions and hooks', default=False, required=False)
parser.add_argument('--log', metavar='<path>', type=str, action='store',
                    help='log template usage as json', default="/dev/null", required=False)
parser.add_argument('--expression', type=str, action='store', help='if specified this template string is evaluated. Just this output will be emitted', default='', required=False)
parser.add_argument('--metrics-dir', metavar='<path>', type=str, action='store',
                    help='directory for prometheus metrics export', default="/var/lib/prometheus/node-exporter", required=False)
parser.add_argument('--show-datasources', nargs='?', const=True, type=bool, action='store',
                    help='output merged datasource JSON and exit', default=False, required=False)
parser.add_argument('--show-log-context', nargs='?', const=True, type=bool, action='store',
                    help='output log context JSON and exit', default=False, required=False)

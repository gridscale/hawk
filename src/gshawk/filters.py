from inspect import isclass, isfunction
from pkgutil import iter_modules, walk_packages
from pathlib import Path
from importlib import import_module
from jinja2.filters import FILTERS
from gshawk.vars import filters
from os import getenv
import sys

discovered_plugins = {
    name: import_module(name)
    for finder, name, ispkg
    in iter_modules()
    if name.startswith('hawk_filter_')
}

if getenv("LIST_PLUGINS"):
    print("found filter plugins:", file=sys.stderr)
for name in discovered_plugins:
    if getenv("LIST_PLUGINS"):
        print(discovered_plugins[name], file=sys.stderr)
    __import__(name, locals(), globals())

def register_filters():
    for name, filter in filters.items():
        FILTERS[name] = filter


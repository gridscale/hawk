from inspect import isclass, isfunction
from pkgutil import iter_modules, walk_packages
from pathlib import Path
from importlib import import_module
from os import getenv
import sys

discovered_plugins = {
    name: import_module(name)
    for finder, name, ispkg
    in iter_modules()
    if name.startswith('hawk_helper_')
}

if getenv("LIST_PLUGINS"):
    print("found helper plugins:", file=sys.stderr)
for name in discovered_plugins:
    if getenv("LIST_PLUGINS"):
        print(discovered_plugins[name], file=sys.stderr)
    __import__(name, locals(), globals())



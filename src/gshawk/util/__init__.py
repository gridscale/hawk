import os
import pathlib
import jinja2
import sys
import re
import time
import yaml
import time
import datetime
import platform
from difflib import unified_diff
from gshawk.vars import global_args
from prometheus_client import CollectorRegistry, Gauge, write_to_textfile, Counter, Histogram
registry = CollectorRegistry()
files = Counter('hawk_files_updated_total', 'Files updated', ['state'], registry=registry)
execution_time = Histogram('hawk_execution_time_seconds', 'Hawk execution time', ['code_area'], registry=registry)
units_updated = Counter('hawk_units_updated_total', 'Units updated', ['state'], registry=registry)
magic_units = Counter('hawk_magic_units_total', 'Magic units', ['state'], registry=registry)


import gshawk.db
gshawk.db.DB(os.getenv("STATE_DIR", "/var/lib/gs-hawk/"))

class FeatherConfig:
    name = ""
    path = ""
    db_id = 0
    exists = False
    once_per_boot = False
    write_once = False
    def __init__(self, name, path):
        self.name = str(name)
        self.path = str(path)
        self.db_id = gshawk.db.DB.feather_id(self)
        self.exists = os.path.isdir(self.path)
        yml_path = os.path.join(path, "feather.yml")
        if not os.path.isfile(yml_path):
            return
        cfg = {}
        with open(yml_path, 'r') as stream:
            cfg = yaml.safe_load(stream)
        if cfg.get('once_per_boot', False):
            self.once_per_boot = True
        if cfg.get('write_once', False):
            self.write_once = True
    
    def allow_write(self, absolute_target_file):
        t = gshawk.db.DB.get_modify_time(absolute_target_file, self)
        if self.once_per_boot:
            if platform.system() == 'Linux':
                cutoff = datetime.datetime.now().timestamp() - time.clock_gettime(time.CLOCK_BOOTTIME)
            else:
                cutoff = datetime.datetime.now().timestamp() - time.clock_gettime(time.CLOCK_MONOTONIC)
            if t > cutoff:
                print("skipped: %s, skipping because file was already handled by hawk after boot" % (absolute_target_file), file=sys.stderr)
                return False # File modified after boot
        if self.write_once:
            if t != 0: # File found in DB == handled
                print("skipped: %s, skipping because file was already created by hawk" % (absolute_target_file), file=sys.stderr)
                return False
        
        return True

def dumpMetrics():
    prometheus_dir = pathlib.Path(gshawk.vars.global_args.get('metrics_dir', '/var/lib/prometheus/node-exporter'))
    if not prometheus_dir.exists():
        return
    
    prometheus_file = prometheus_dir / 'hawk.prom'
    g = Gauge('hawk_last_updated', 'UNIX timestamp of last execution', registry=registry)
    g.set(int(time.time()))
    write_to_textfile(prometheus_file, registry)

@execution_time.labels('write_file').time()
def write_file(relative_source_file, target_file, new_content, binary, feather, show_diff):
    if target_file == "/dev/null":
        return False
    new_file = True
    old_content = bytes()
    dirname = os.path.dirname(target_file)
    pathlib.Path(dirname).mkdir(parents=True, exist_ok=True)
    if binary:
        if not global_args['dry_run']:
            with open(target_file, 'wb') as new_file:
                new_file.write(new_content)
                new_file.close
                gshawk.db.DB.record_file(relative_source_file, target_file, "binary", feather)
        return True

    if os.path.isfile(target_file):
        new_file = False
        with open(target_file, 'rb') as check:
            old_content = check.read()

    changed = False
    if new_content.decode('utf-8') != old_content.decode('utf-8'):
        changed = True
        if not global_args['dry_run']:
            with open(target_file, 'wb') as new_file:
                new_file.write(new_content)
                new_file.close

    diff = unified_diff(old_content.decode('utf-8').splitlines(True), new_content.decode('utf-8').splitlines(True), fromfile='before', tofile=target_file)
    if not global_args['no_diff'] and show_diff:
        sys.stdout.writelines(diff)

    if new_file:
        gshawk.db.DB.record_file(relative_source_file, target_file, "created", feather)
    elif changed:
        
        gshawk.db.DB.record_file(relative_source_file, target_file, "changed", feather)
    else:
        gshawk.db.DB.record_file(relative_source_file, target_file, "up-to-date", feather)
    
    return new_file or changed

def jinja2_template_error_lineno(filename):
    type, value, tb = sys.exc_info()
    if not issubclass(type, jinja2.exceptions.TemplateError):
        return None
    if hasattr(value, 'lineno'):
        return value.lineno
    while tb:
        if tb.tb_frame.f_code.co_filename == filename:
            return tb.tb_lineno
        tb = tb.tb_next

def error_from_exception(e, filename):
    line = jinja2_template_error_lineno(filename)
    if line == None:
        line = 0
    error = {
        'exception': ".".join([e.__class__.__module__, e.__class__.__name__]),
        'line': line,
        'message': str(e),
    }
    return error

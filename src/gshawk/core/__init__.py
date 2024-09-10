import pathlib
import re
import os
import stat
import json
import traceback
import sys
import ast
from jinja2 import Environment, StrictUndefined
from gshawk.vars import global_args, template_vars, log_context
from gshawk.systemd import systemd_unit_dir, container_unit_dir, parseSystemdUnit, processConditions, handleHawkHook
from gshawk.util import FeatherConfig, write_file, error_from_exception, files, execution_time
import gshawk.db

reg = re.compile(r"^%s/(?P<unit>.*\.(service|timer|path|target|hook))($|\.d/)" % systemd_unit_dir)
container_reg = re.compile(r"^%s/(?P<unit>.*)\.container$" % container_unit_dir)
is_sysctl = re.compile(r"/etc/sysctl.d/.*")

JINJA2_OVERRIDE = '#jinja2:'

@execution_time.labels('render_template').time()
def render_template(data, mock_source=None):
    env = Environment(undefined=StrictUndefined)
    if data.startswith(JINJA2_OVERRIDE):
        eol = data.find('\n')
        line = data[len(JINJA2_OVERRIDE):eol]
        data = data[eol + 1:]
        for pair in line.split(','):
            pair = pair.strip()
            if ':' not in pair:
                raise ValueError(f"failed to parse jinja2 override {pair!r}.")
            (key, val) = pair.split(':', 1)
            key = key.strip()
            if hasattr(env, key):
                setattr(env, key, ast.literal_eval(val.strip()))
            else:
                raise ValueError(f"invalid jinja2 override {pair!r}.")
    tmpl = env.from_string(data)
    if mock_source != None:
        tmpl.filename = mock_source
    return tmpl.render(template_vars)

def handle_file(relative_source_file, absolute_target_dir, absolute_source_file, absolute_target_file, feather):
    with open(absolute_source_file, 'rb') as f:
        new_content = data = f.read()
        executable = data.startswith(b'#!')
        try:
          string_data = data.decode('utf-8')
        except UnicodeDecodeError:
          binary = True
        else:
          binary = False
        if absolute_source_file.endswith('.jinja2'):
            if binary:
                raise ValueError('A .jinja2 template must be valid utf-8')
            new_content = render_template(string_data, relative_source_file).encode('utf-8')
            absolute_target_file = absolute_target_file.replace('.jinja2', '')
        unit_name = ''
        container_unit = False
        if is_sysctl.search(absolute_target_file) != None:
            global_args['reload_sysctl'] = True
        matches = reg.search(absolute_target_file)
        if matches != None:
            unit_name = matches.group('unit')

        matches = container_reg.search(absolute_target_file)
        if matches != None:
            unit_name = matches.group('unit') + ".service"
            container_unit = True

        if not global_args['dry_run']:
            pathlib.Path(absolute_target_dir).mkdir(parents=True, exist_ok=True)

        if not feather.allow_write(absolute_target_file):
                print("skipped: %s, dropping because of unmet conditions" % (absolute_source_file), file=sys.stderr)
                files.labels('skipped').inc()
                gshawk.db.DB.record_file(relative_source_file, absolute_target_file, "skipped", feather)
                return None

        if unit_name != '':
            config = parseSystemdUnit(new_content.decode('utf-8'), absolute_target_file)
            conditions = processConditions(config)
            if not conditions:
                print("unit: %s, dropping because of unmet conditions" % (unit_name), file=sys.stderr)
                print("Source:", absolute_source_file, "Target:", '/dev/null', "systemd unit:", 'dropped', "Binary:", binary, "feather:", feather.name)
                files.labels('dropped').inc()
                return None

            print("Source:", absolute_source_file, "Target:", absolute_target_file, "systemd unit:", unit_name, "Binary:", binary, "feather:", feather.name)
            changed = write_file(relative_source_file, absolute_target_file, new_content,  binary, feather,True)
            handleHawkHook(relative_source_file, unit_name, container_unit, changed, config, feather)
        else:
            print("Source:", absolute_source_file, "Target:", absolute_target_file, "systemd unit:", False, "Binary:", binary, "feather:", feather.name)
            changed = write_file(relative_source_file, absolute_target_file, new_content, binary, feather,True)

        if executable and not global_args['dry_run']:
            st = os.stat(absolute_target_file)
            os.chmod(absolute_target_file, 0o755)
        if changed:
            files.labels('changed').inc()
        else:
            files.labels('up-to-date').inc()
        return absolute_target_file

def rollout():
    cases = []
    failures = 0

    source_dir = pathlib.Path(global_args['source']).absolute()
    target_dir = pathlib.Path(global_args['target']).absolute()

    feathers = [f for f in os.listdir(source_dir) if os.path.isdir(os.path.join(source_dir, f))]

    if not feathers:
        print("Warning: No feathers found in '%s'" % source_dir, file=sys.stderr)
        return True

    for feather in feathers:
        feather_dir = pathlib.Path(os.path.join(source_dir, feather)).absolute()
        fth = FeatherConfig(feather, feather_dir)

        print("Processing feather '%s' from '%s'..." % (feather, feather_dir),file=sys.stderr)
        for _file in sorted(feather_dir.glob('**/*')):
            absolute_source_file = str(_file.absolute())
            relative_source_file = str(pathlib.Path(absolute_source_file).relative_to(feather_dir))
            if relative_source_file == "feather.yml":
                continue
            if os.path.isdir(absolute_source_file):
                continue
            error = {}

            template_source_name = str(pathlib.Path('/' + relative_source_file).absolute()) # This will reduce any duplicate slashes.
            absolute_target_file = os.path.join(str(target_dir), str(pathlib.Path(absolute_source_file).relative_to(feather_dir)))
            absolute_target_dir = os.path.dirname(absolute_target_file)
            try:
                written_file = handle_file(relative_source_file = relative_source_file, absolute_target_dir = absolute_target_dir, absolute_source_file = absolute_source_file, absolute_target_file = absolute_target_file, feather = fth)
                if written_file == None:
                    continue
                cases.append({'target': absolute_target_file, 'source': absolute_source_file, 'success': True, 'log': "", 'error': error}|log_context)
            except Exception as e:
                error = error_from_exception(e, absolute_source_file)
                cases.append({'target': absolute_target_file, 'source': absolute_source_file, 'success': False, 'log': "".join(traceback.format_exception(e)), 'error': error}|log_context)
                print(traceback.format_exception(e), file=sys.stderr)
                files.labels('failed').inc()
                failures += 1

    write_file('/dev/null', global_args['log'], json.dumps(cases).encode('utf-8'), False,feather =feather, show_diff=False)
    return failures == 0

import configparser
import os
import pathlib
import subprocess
import json
import sys
import platform
from jinja2 import Environment, StrictUndefined
from gshawk.vars import global_args
from gshawk.util import write_file, execution_time, units_updated, magic_units

magic_files = []
units = {}
config = configparser.ConfigParser()
systemd_unit_dir = str(os.path.join(global_args['target'],"etc/systemd/system"))
container_unit_dir = str(os.path.join(global_args['target'],"etc/containers/systemd"))

class Secrets:
    __credentials = {}
    __initialized = False
    def __listCreds(self, system):
        if platform.system() != 'Linux':
            return
        data = ""
        if system:
            data = subprocess.run(["/usr/bin/systemd-creds", "list", "--system", "--json=short"], capture_output=True, text=True).stdout
        else:
            data = subprocess.run(["/usr/bin/systemd-creds", "list", "--json=short"], capture_output=True, text=True).stdout
        if data == "":
            return
        credentials = json.loads(data)
        for cred in credentials:
            self.__credentials[cred['name']] = {
                'encrypted': cred['secure'] == "encrypted",
                'path': cred['path'] if cred['secure'] == "encrypted" else None,
                'system': system
            }
    def __readCredPlain(self, system, name):
        credential = ""
        if system:
            credential = subprocess.run(["/usr/bin/systemd-creds", "cat", "--system", name], capture_output=True, text=True, check=True).stdout
        else:
            credential = subprocess.run(["/usr/bin/systemd-creds", "cat", name], capture_output=True, text=True, check=True).stdout
        return credential

    def __readCredEncrypted(self, path, name):
        return subprocess.run(["/usr/bin/systemd-creds", "decrypt", "--name", name, path], capture_output=True, text=True, check=True).stdout

    def __listCredsAll(self):
        if platform.system() != 'Linux':
            return
        if self.__initialized:
            return

        self.__listCreds(True)
        self.__listCreds(False)
        self.__initialized = True

    def __init__(self):
        if os.getenv("LIST_CREDS"):
            self.__listCredsAll()
            print("Found credentials:", file=sys.stderr)
            print(self.__credentials, file=sys.stderr)

    def readFirst(self, names, check=True):
        self.__listCredsAll()
        for name in names:
            if name in self.__credentials:
                return self.read(name, check)
        return self.read(','.join(names), check) # Cheaply re-use error handling from there

    def read(self, name, check=True):
        self.__listCredsAll()
        if name not in self.__credentials:
            if check:
                raise Exception("Credential '%s' not found. Available are: %s" % (name, ', '.join(self.__credentials.keys())))
            return None
        try:
            cred = self.__credentials[name]
            if cred['encrypted']:
                return self.__readCredEncrypted(cred['path'], name)
            else:
                return self.__readCredPlain(cred['system'], name)
        except:
            print("Warn: Failed to read systemd credential '%s'" % name, file=sys.stderr)
            if check:
                raise Exception("Credential '%s' could not be %s" % (name, ('decrypted' if cred['encrypted'] else 'read')))
            return False

secrets = Secrets()

# Template helpers
class M(dict):
    def __setitem__(self, key, value):
        if not key in self:
            if isinstance(value, str):
                value = [value]
            super(M, self).__setitem__(key, value)

        else:
            items = super(M,self).__getitem__(key)
            if not isinstance(items, list):
                items = [items]
            if isinstance(value, str):
                value = [value]
            #print( key, value)
            value[0] = value[0].split('\n',1)[0]
            new = value[0]
            if new not in items and len(new) > 0:
                items.append(new)

def collect_magic_units():
    if platform.system() != 'Linux':
        return
    for _file in pathlib.Path(systemd_unit_dir).glob('hawk-magic-*'):
        if os.path.isfile(_file):
            file = str(_file.absolute())
            magic_files.append(file)
            magic_units.labels('collected').inc()

def reap_magic_units():
    if platform.system() != 'Linux':
        return
    for _file in sorted(magic_files):
        if os.path.isfile(_file):
            os.remove(_file)
            magic_units.labels('removed').inc()

def parseSystemdUnit(content, filename):
    config = configparser.ConfigParser(
        delimiters=['='],
        allow_no_value=True,
        interpolation=None,
        strict=False,
        dict_type=M,
        empty_lines_in_values=False
    )
    config.optionxform = str
    config.read_dict({
        "DEFAULT": {
            # X-Hawk section
            "RestartOnConfigChange": "yes", # Place magic path unit to restart on changes to ConfigFile
            "ReloadOnConfigChange": "no", # Place magic path unit to reload on changes to ConfigFile
            "RestartOnUnitChange": "yes", # Restart if Hawk updated the unit
            "EnableUnit": "yes", # Enable unit. Disables if `no`
            "StopOnUnitDisable": "no", # If EnableUnit is no, and this is yes, the unit will also be stopped.
            "StartUnit": "yes", # Start if not started. Will NOT stop running units.
            "ExecOnUnitChange": "", # Run these commands when the unit changed. Can be listed multiple times.
            "ExecOnApply": "", # Always ran, always first. Can be listed multiple times.
            "ConfigFile": "", # Files listed here will be respected for `Re(start|load)OnConfigChange`. Can be listed multiple times.
           
            # Install section:
            "WantedBy": "multi-user.target",
        }
    })
    config.add_section("X-Hawk")
    config.add_section("Install")
    config.add_section("Unit")
    config.read_string(content, filename)
    return config

def dummyUnit(changed, conditions_met):
    return {
        "enable": False,
        "disable": False,
        "start": False,
        "stop": False,
        "restart": False,
        "dummy": True,
        "changed": changed,
        "exec": [],
    }

def processConditions(config):
    if platform.system() != 'Linux':
        return False # TODO: Maybe raise Error?
    conditions = {key:value for key, value in config.items("X-Hawk", raw=True) if key.startswith("Condition") or key.startswith("Assert")}

    template_conditions = config.get("X-Hawk", "TemplateCondition", fallback=["yes"])
    for condition in template_conditions:
        if condition == "no" or condition == "False":
            return False
        if condition != "yes" and condition != "True":
            raise TypeError("Invalid condition %s. Expected 'yes','no','True','False'" % condition)
    if not conditions:
        return True
    cond_str = []
    for keyword in conditions:
        for instance in conditions[keyword]:
            cond_str.append("%s=%s" % (keyword, instance))
    args = ["/usr/bin/systemd-analyze", "condition"] + cond_str
    code = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    return code == 0

def execHooks(unit_name, changed, config):
    on_change = [val for val in config.get("X-Hawk", "ExecOnUnitChange") if val]
    always = [val for val in config.get("X-Hawk", "ExecOnApply") if val]

    units[unit_name]["exec"] = always
    if changed:
        units[unit_name]["exec"] += on_change

def handleHawkHook(relative_source_file, unit, container, changed, config, feather):
    if platform.system() != 'Linux':
        return # TODO: Maybe raise Error?
    if unit.endswith('.service') or unit.endswith('.target') or unit.endswith('.timer'):
        unit_name = unit.split('.', 1)[0]
        config_files = [val for val in config.get("X-Hawk", "ConfigFile") if val]
        magic_restart = config.get("X-Hawk", "RestartOnConfigChange")[-1] == "yes"
        magic_reload = config.get("X-Hawk", "ReloadOnConfigChange")[-1] == "yes"
        if config_files and (magic_restart or magic_reload):
            tpl = """# Magically created with gridscale Hawk
[Unit]
Description=hawk magic path unit '{{unit}}'
[Path]
{%- for path in paths %}
PathChanged={{path}}
{%- endfor %}
Unit=hawk-unit-{{method}}er@{{unit}}.service
[Install]
WantedBy={{target}}
"""
            restart_method = "restart"
            if magic_reload:
                restart_method = "reload"
            content = Environment(undefined=StrictUndefined).from_string(tpl).render(paths = config_files, unit=unit, method=restart_method, target=config.get("Install", "WantedBy")[-1])
            magic_unit_name = "hawk-magic-%s.path" % (unit_name)
            target_file = "%s/%s"% (systemd_unit_dir, magic_unit_name)
            magic_changed = write_file(relative_source_file, target_file, content.encode('utf-8'), False, feather, False)
            units[magic_unit_name] = {
                "enable": True,
                "disable": False,
                "start": False,
                "stop": False,
                "restart": True,
                "exec": [],
                "changed": magic_changed,
            }
            if magic_changed:
                magic_units.labels('changed').inc()
            else:
                magic_units.labels('created').inc()
            while target_file in magic_files:
                magic_files.remove(target_file)

    if '@' in unit or unit.endswith('.hook'):
        units[unit] = dummyUnit(changed, processConditions(config))
    else:
        should_restart = config.get("X-Hawk", "RestartOnUnitChange")[-1] == "yes"

        should_enable = config.get("X-Hawk", "EnableUnit")[-1] == "yes"
        stop_on_disable = config.get("X-Hawk", "StopOnUnitDisable")[-1] == "yes"
        should_start = config.get("X-Hawk", "StartUnit")[-1] == "yes"

        disable_unit = not should_enable
        stop_unit = not should_start and (disable_unit and stop_on_disable)
        start_unit = should_start and not should_restart
        enable_unit = should_enable
        restart_unit = should_restart and not (stop_unit or disable_unit)

        units[unit] = {
            "enable": enable_unit,
            "disable": disable_unit,
            "start": start_unit,
            "stop": stop_unit,
            "restart": restart_unit,
            "changed": changed,
            "exec": [],
            "dummy": False,
        }
    execHooks(unit, changed, config)

def _run_systemctl_with_fallback(cmd):
    """
    Run systemctl command synchronously with 30s timeout.
    On timeout, warn and re-run with --no-block flag.
    """
    no_block_cmd = cmd.copy()
    no_block_cmd.insert(2, "--no-block")
    
    try:
        result = subprocess.run(cmd, timeout=30)
        return result.returncode
    except subprocess.TimeoutExpired:
        print("Warn: Command '%s' timed out after 30 seconds, falling back to async" % (" ".join(cmd)), file=sys.stderr)
        subprocess.run(no_block_cmd)
        return -1

def _run_with_timeout(cmd, timeout):
    """
    Run command synchronously with timeout.
    On timeout, warn and return -1 without retry.
    """
    try:
        result = subprocess.run(cmd, timeout=timeout)
        return result.returncode
    except subprocess.TimeoutExpired:
        print("Warn: Command '%s' timed out after %d seconds" % (" ".join(cmd), timeout), file=sys.stderr)
        return -1

@execution_time.labels('run_unit_tasks').time()
def run_unit_tasks():
    if platform.system() != 'Linux':
        return # TODO: Maybe raise Error?
    systemd_offline = global_args['skip_systemd']

    if not systemd_offline:
        _run_systemctl_with_fallback(["/usr/bin/systemctl", "daemon-reload"])

        if global_args['reload_sysctl']:
            _run_with_timeout(["/usr/sbin/sysctl", "--system"], 300)

    for unit in sorted(units):
        actions = units[unit];
        filtered = [key for key, val in actions.items() if val and key not in["changed", "exec", "dummy"]]
        if not actions["changed"]:
            print("unit: %s, actions: none (up-to-date)" %(unit), file=sys.stderr)
            units_updated.labels('up-to-date').inc()
            continue
    
        print("unit: %s\n\tactions: %s" %(unit, ", ".join(filtered)), file=sys.stderr)
    
        if systemd_offline or len(filtered) < 1:
            continue
    
        _run_systemctl_with_fallback(["/usr/bin/systemctl", "reset-failed", unit])
    
        if actions["enable"]:
            _run_systemctl_with_fallback(["/usr/bin/systemctl", "reenable", unit])
        if actions["disable"]:
            _run_systemctl_with_fallback(["/usr/bin/systemctl", "disable", unit])
        if not systemd_offline:
            if actions["restart"]:
                subprocess.run(["/usr/bin/systemctl", "--no-block", "restart", unit])
            if actions["start"]:
                subprocess.run(["/usr/bin/systemctl", "--no-block", "start", unit])
            if actions["stop"]:
                subprocess.run(["/usr/bin/systemctl", "--no-block", "stop", unit])
    
    for unit in sorted(units):
        units_updated.labels('changed').inc()
        actions = units[unit];
        if not actions["exec"]:
            continue
        
        print("unit: %s, executing hooks..." %(unit), file=sys.stderr)
        for exec in actions["exec"]:
            if not systemd_offline:
                _run_with_timeout(["/bin/bash", "-c", exec], 300)

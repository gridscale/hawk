import subprocess
from gshawk.vars import filters, template_vars

class HawkAssertionError(Exception):
    def __init__(self, wanted):
        super().__init__(f'Assertion {wanted} is NOT satisfied')

def check_condition(value):
    args = ["/usr/bin/systemd-analyze", "condition", f"Condition{value}"]
    code = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    return code == 0

def assert_condition(value):
    args = ["/usr/bin/systemd-analyze", "condition", f"Assert{value}"]
    code = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    if code != 0:
        raise HawkAssertionError(value)

filters['hawk.condition'] = check_condition
template_vars['hawk']['condition'] = check_condition

filters['hawk.assert'] = assert_condition
template_vars['hawk']['assert'] = assert_condition

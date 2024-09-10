from pyroute2 import NDB
import gshawk.vars
from jinja2.tests import TESTS
import re

# Template helpers
class hawk_helper:
    @classmethod
    def interfaces(this):
        ifaces = []
        with NDB() as ndb:
            for iface in ndb.interfaces:
                ifaces.append(iface.ifname)
        return ifaces
    @classmethod
    def match(this, var, expression):
        try:
            return re.search(expression, str(var)) != None
        except:
            return False

gshawk.vars.template_vars['hawk']['interfaces'] = hawk_helper.interfaces
gshawk.vars.template_vars['hawk']['match'] = hawk_helper.match

TESTS['match'] = hawk_helper.match

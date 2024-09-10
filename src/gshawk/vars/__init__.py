import argparse
import os
import sys
template_vars = {
    "hawk": dict()
}
filters = {}
log_context  = {}
global_args = {
    "reload_sysctl": False
}

parser = argparse.ArgumentParser(prog=os.getenv('SCIE', sys.argv[0]),description='gridscale Hawk')

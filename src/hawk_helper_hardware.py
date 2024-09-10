import gshawk.vars
import subprocess
import copy
import os
import platform

class ReservationError(Exception):
    def __init__(self, wanted, have, typ):
        super().__init__(f'Unable to allocate {wanted} {typ}. Allocated {have}')

class IllegalDesiredReservation(Exception):
    def __init__(self):
        super().__init__('Reservation must be != 0')
class CpuSetEmpty(Exception):
    def __init__(self):
        super().__init__('cpu set must not be empty')
class LinuxOnly(Exception):
    def __init__(self):
        super().__init__('currently only supported in Linux')

arch = []

smt_cores = []
dedicated_cores = []
numa_nodes = 1
sockets = 1
big_little = False

if platform.system() == 'Linux':
    cpus = subprocess.run(["lscpu", "--parse=CORE,CPU,SOCKET,NODE,CACHE"], capture_output=True, check=True)
    lines = cpus.stdout.splitlines()
else:
    lines = []
if os.getenv('HAWK_MOCK_CPU') is not None:
    lines = [b"# Core,CPU,Socket,Node,L1d:L1i:L2:L3",
             b"0,0,0,0,0:0:0:0",
             b"0,1,0,0,0:0:0:0",
             b"1,2,0,0,4:4:1:0",
             b"1,3,0,0,4:4:1:0",
             b"2,4,0,0,8:8:2:0",
             b"2,5,0,0,8:8:2:0",
             b"3,6,0,0,12:12:3:0",
             b"3,7,0,0,12:12:3:0",
             b"4,8,0,0,16:16:4:0",
             b"5,9,0,0,17:17:4:0",
             b"6,10,0,0,18:18:4:0",
             b"7,11,0,0,19:19:4:0",
             b"8,12,0,0,20:20:5:0",
             b"9,13,0,0,21:21:5:0",
             b"10,14,0,0,22:22:5:0",
             b"11,15,0,0,23:23:5:0",
             ]
cache_names = []
for line in lines:
    line = line.rstrip().decode(encoding='UTF-8',errors='strict')
    params = line.split(',', 4)
    if params[0].lower() == '# core': # This is the header line
        params[0] = 'Core'
        cache_names = params[4].split(':')
        continue
    if params[0].startswith('#'): # Ignore other header lines
        continue
    caches = params[4].split(':')
    named_caches = {}
    for idx, cache in enumerate(caches):
        named_caches[cache_names[idx]] = int(cache)
    arch.append({"thread": int(params[1]), "core": int(params[0]), "socket": int(params[2]), "node": int(params[3]), "caches": named_caches})
    numa_nodes = max(numa_nodes,(int(params[3]) + 1))
    sockets = max(sockets,(int(params[2]) + 1))

for idx, cpu in enumerate(arch):
    cpu["reserved"] = False
    cpu["idx"] = idx # To ensure we can access any core by index on the `arch` array
    for other_idx, other_cpu in enumerate(arch):
        if other_idx == idx:
            continue # Ignore the same core
        cpu["smt"] = cpu.get('smt', False) or cpu["core"] == other_cpu["core"]

for idx, cpu in enumerate(arch):
    if cpu["smt"]:
        smt_cores.append(cpu)
        if len(dedicated_cores) > 0:
            big_little = True
    else:
        dedicated_cores.append(cpu)
        if len(smt_cores) > 0:
            big_little = True

# Template helpers
class hawk_hardware:
    @classmethod
    def _return_reserved_cores(this, current):
        return [x for x in current if x["reserved"]]

    @classmethod
    def _return_unreserved_cores(this, current):
        return [x for x in current if not x["reserved"]]

    @classmethod
    def _return_threads_on_socket(this, socket, current):
        return [x for x in current if x["socket"] == socket]

    @classmethod
    def _return_threads_on_node(this, node, current):
        return [x for x in current if x["node"] == node]

    @classmethod
    def _return_threads_on_core(this, core, current):
        return [x for x in current if x["core"] == core]

    @classmethod
    def _display_threads(this, current):
        return [f'Core {x["core"]}, Thread: {x["thread"]}, NUMA: {x["node"]}, SMT: {x["smt"]}' for x in current if x is not None]

    @classmethod
    def _format_cpuset(this, cset):
        if len(cset) == 0:
            raise CpuSetEmpty()
        if len(cset) == 1:
            return str(cset[0]["thread"])
        string = []
        ranges = []
        cset = sorted(cset, key=lambda x: x["thread"])
        cur_min = cset[0]["thread"]
        cur_max = cset[0]["thread"]
        prev = cset[0]
        for idx, core in enumerate(cset):
            if core["thread"] > prev["thread"] + 1:
                # we skipped.
                ranges.append((cur_min, cur_max))
                cur_min = core["thread"]
                cur_max = core["thread"]
            cur_min = min(cur_min,core["thread"])
            cur_max = max(cur_max,core["thread"])
            prev = core
        ranges.append((cur_min, cur_max))

        for r in ranges:
            if r[0] != r[1]:
                string.append("%s-%s" % (r[0], r[1]))
            else:
                string.append(str(r[0]))
        return(','.join(string))

    @classmethod
    def _try_allocate_dedicated(this, desired, current):
        allocated = 0
        for i in range(min(len(dedicated_cores),desired)):
                current[dedicated_cores[i]["idx"]]["reserved"] = True
                allocated += 1
        return allocated

    @classmethod
    def _try_allocate_unreserved(this, desired, current):
        allocated = 0
        per_numa = desired / numa_nodes
        for numa_node in range(numa_nodes):
            current_numa_cores = 0
            in_node = this._return_threads_on_node(numa_node, current)
            available = this._return_unreserved_cores(in_node)
            for cpu in available:
                if current_numa_cores == per_numa:
                    break
                if cpu["reserved"]:
                    continue;
                siblings = this._return_threads_on_core(cpu["core"], available)
                for sibling in siblings:
                    current[sibling["idx"]]["reserved"] = True
                allocated += 1
                current_numa_cores += 1
        return allocated
    
    @classmethod
    def memory_total_bytes(this):
        this._ensure_linux()
        if os.getenv('HAWK_MOCK_MEMORY') is not None:
            return 274877906944
        return os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')

    @classmethod
    def memory_total_mib(this):
        return this.memory_total_bytes() / 1024.**2

    @classmethod
    def memory_total_gib(this):
        return this.memory_total_bytes() / 1024.**3

    @classmethod
    def memory_reserve_bytes(this, reserve):
        this._ensure_linux()
        if this.memory_total_bytes() < abs(reserve):
            raise ReservationError(abs(reserve), this.memory_total_bytes(), ' bytes of memory')
            raise("Could not reserve memory")
        
        if reserve < 0:
            return this.memory_total_bytes() - abs(reserve)
        else:
            return reserve

    @classmethod
    def memory_reserve_mib(this, reserve):
        return this.memory_reserve_bytes(reserve * 1024**2)/(1024.**2)
    @classmethod
    def memory_reserve_gib(this, reserve):
        return this.memory_reserve_bytes(reserve * 1024**3)/(1024.**3)

    @classmethod
    def cpuset_reserve_physical_cores(this, desired, prefer_E_cores=True):
        this._ensure_linux()
        flip = False
        current = copy.deepcopy(arch)
        allocated = 0

        if desired < 0:
            flip = True
            desired *= -1
        if desired == 0:
            raise IllegalDesiredReservation()

        if big_little and prefer_E_cores:
            # We are running on an architecture with P and E cores.
            allocated += this._try_allocate_dedicated(desired, current)

        remaining = desired - allocated
        allocated += this._try_allocate_unreserved(remaining, current)

        candidates = []
        if flip:
            candidates = this._return_unreserved_cores(current)
        else:
            candidates = this._return_reserved_cores(current)

        if allocated != desired:
            raise ReservationError(desired, allocated, 'physical CPUs')

        return this._format_cpuset(candidates)

    @classmethod
    def _ensure_linux(this):
        if platform.system() != 'Linux':
            raise LinuxOnly()

gshawk.vars.template_vars['hawk']['hardware'] = hawk_hardware


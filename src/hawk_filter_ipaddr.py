import netaddr
from gshawk.vars import filters

def ipaddr(value, *args):
    return _ipaddr(value, 0, args)
def ipv6(value, *args):
    return _ipaddr(value, 6, args)
def ipv4(value, *args):
    return _ipaddr(value, 4, args)
def _ipaddr(value, version, args):
    result = value
    if isinstance(value, list):
        result = []
        for item in value:
            res = _ipaddr(item, version, args)
            if res:
                result.append(res)
        return result

    query = "valid"
    if len(args) > 0:
        query = args[0]
    if query == "valid":
        valid = False
        if version == 4 or version == 0:
            try:
                valid = valid or netaddr.valid_ipv4(value, flags=netaddr.INET_PTON | netaddr.ZEROFILL)
            except:
                pass
        if version == 6 or version == 0:
            try:
                valid = valid or netaddr.valid_ipv6(value)
            except:
                pass
        if valid:
            return value
    try:
        net = netaddr.IPNetwork(value)
        if version != 0:
             # We want a specific IP version
            if net.version != version:
                return None
    except:
        return None

    match query:
        case "valid":
            # If a network is provided, we fall through to here. Just return the value, as it could be parsed
            return value
        case "netmask":
            return net.netmask
        case "wrapped_address" if net.version == 6:
            return f"[{net.ip}]"
        case "address" | "wrapped_address":
            return net.ip
        case "wrapped_network" if net.version == 6:
            return f"[{net.network}]"
        case "network" | "wrapped_network":
            return net.network
        case "wrapped_cidr" if net.version == 6:
            return f"[{net.network}]/{net.prefixlen}"
        case "cidr" | "wrapped_cidr":
            return net.cidr
        case "wrapped_interface" if net.version == 6:
            return f"[{net.ip}]/{net.prefixlen}"
        case "interface" | "wrapped_interface":
            return f"{net.ip}/{net.prefixlen}"
        case "prefix":
            return net.prefixlen
        case _:
            raise BaseException("unknown query '%s'" % (query))

filters['hawk.utils.ipaddr'] = ipaddr
filters['hawk.utils.ipv4'] = ipv4
filters['hawk.utils.ipv6'] = ipv6


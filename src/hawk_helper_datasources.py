import gshawk.vars
import re
import time
import hashlib
import os
import ssl
import sys
import urllib.request
import urllib.error
from pathlib import Path
import yaml

ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE
_request_cache = {}


def get_default_cache_dir():
    if os.getuid() == 0:
        return '/Library/Caches/gs-hawk' if sys.platform == 'darwin' else '/var/cache/gs-hawk'
    return os.path.expanduser('~/.cache/gs-hawk')


def substitute_variables(value, variables):
    if not isinstance(value, str):
        return value
    return re.sub(r'\$\{([^}]+)\}', lambda m: str(variables.get(m.group(1), m.group(0))), value)


def get_nested_value(data, key_path):
    if key_path in ('.', ''):
        return data
    parts, current, i = [], '', 0
    while i < len(key_path):
        if key_path[i] == '.':
            if current: parts.append(current); current = ''
        elif key_path[i] == '[':
            if current: parts.append(current); current = ''
            bracket, i = '', i + 1
            while i < len(key_path) and key_path[i] != ']': bracket += key_path[i]; i += 1
            parts.append(('idx', bracket))
        else:
            current += key_path[i]
        i += 1
    if current: parts.append(current)
    
    result = data
    for part in parts:
        if isinstance(part, tuple):
            idx = part[1]
            if isinstance(result, list):
                try: result = result[int(idx)]
                except (ValueError, IndexError): raise KeyError(f"Invalid array index: {idx}")
            elif isinstance(result, dict) and idx in result:
                result = result[idx]
            else: raise KeyError(f"Key not found: {idx}")
        elif isinstance(result, dict) and part in result:
            result = result[part]
        else: raise KeyError(f"Key not found: {part}")
    return result


def deep_merge(base, override):
    result = base.copy()
    for key, value in override.items():
        if key in result:
            if isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = deep_merge(result[key], value)
            elif isinstance(result[key], list) and isinstance(value, list):
                result[key] = result[key] + value
            else:
                result[key] = value
        else:
            result[key] = value
    return result


def filter_keys(data, include_keys, exclude_keys):
    if include_keys and exclude_keys:
        raise ValueError("Cannot specify both include_keys and exclude_keys")
    if not include_keys and not exclude_keys:
        return data
    if not isinstance(data, dict):
        return data
    if include_keys:
        return {k: v for k, v in data.items() if k in include_keys}
    return {k: v for k, v in data.items() if k not in exclude_keys}


def get_cache_key(source_url):
    return hashlib.sha256(source_url.encode()).hexdigest()


def load_from_cache(cache_dir, cache_key):
    cache_file = Path(cache_dir) / f"{cache_key}.yml"
    if cache_file.exists():
        with open(cache_file, 'r') as f:
            return yaml.safe_load(f)
    return None


def get_cache_mtime(cache_dir, cache_key):
    cache_file = Path(cache_dir) / f"{cache_key}.yml"
    if cache_file.exists():
        try:
            with open(cache_file, 'r') as f:
                data = yaml.safe_load(f)
                return data.get('updated_at', cache_file.stat().st_mtime)
        except (yaml.YAMLError, KeyError):
            return cache_file.stat().st_mtime
    return None


def purge_stale_cache(cache_dir, active_keys):
    cache_path = Path(cache_dir)
    if cache_path.exists():
        for cache_file in cache_path.glob('*.yml'):
            if cache_file.stem not in active_keys:
                cache_file.unlink()


def export_cache_metrics(cache_dir, active_keys):
    prometheus_dir = Path(gshawk.vars.global_args.get('metrics_dir', '/var/lib/prometheus/node-exporter'))
    if not prometheus_dir.exists():
        return
    metrics = []
    for cache_key in active_keys:
        mtime = get_cache_mtime(cache_dir, cache_key)
        if mtime is not None:
            metrics.append(f'hawk_cache_timestamp{{cache_key="{cache_key}"}} {mtime}')
    if metrics:
        prometheus_dir.mkdir(parents=True, exist_ok=True)
        with open(prometheus_dir / 'hawk-cache.prom', 'w') as f:
            f.write('\n'.join(metrics) + '\n')


def save_to_cache(cache_dir, cache_key, content, source_url=None):
    cache_file = Path(cache_dir) / f"{cache_key}.yml"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_file, 'w') as f:
        yaml.dump({'content': content, 'source': source_url, 'updated_at': time.time()}, f)


def load_source(source_config, variables):
    source = substitute_variables(source_config['source'], variables)
    cache_dir = gshawk.vars.global_args.get('cache_dir', get_default_cache_dir())
    cache_key = get_cache_key(source)
    
    if source.startswith('file://'):
        with open(source[7:], 'r') as f:
            return f.read()
    elif source.startswith(('http://', 'https://')):
        if cache_key in _request_cache:
            return _request_cache[cache_key]
        max_retries, retry_delay, last_error = 3, 1, None
        for attempt in range(max_retries):
            try:
                with urllib.request.urlopen(source, timeout=30, context=ssl_context) as response:
                    content = response.read().decode('utf-8')
                save_to_cache(cache_dir, cache_key, content, source)
                _request_cache[cache_key] = content
                return content
            except urllib.error.URLError as e:
                last_error = e
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (attempt + 1))
        cached = load_from_cache(cache_dir, cache_key)
        if cached:
            _request_cache[cache_key] = cached['content']
            return cached['content']
        raise RuntimeError(f"Failed to fetch {source} after {max_retries} attempts: {last_error}")
    elif source.startswith('inline:'):
        return source[7:]
    raise ValueError(f"Unsupported source protocol: {source}")


def process_source(source_config, variables, target_key=None):
    content = load_source(source_config, variables)
    source_path = source_config.get('source', 'unknown')
    as_json = source_config.get('json', True)
    
    if source_config.get('as_variable'):
        as_var = source_config['as_variable']
        if as_json:
            sub_key = source_config.get('sub_key')
            if sub_key:
                sub_key = substitute_variables(sub_key, variables)
                try:
                    data = yaml.safe_load(content)
                except yaml.YAMLError as e:
                    raise RuntimeError(f"Failed to parse YAML from {source_path}: {e}")
                try:
                    variables[as_var] = get_nested_value(data, sub_key)
                except KeyError as e:
                    raise KeyError(f"Failed to extract sub_key '{sub_key}' from {source_path}: {e}")
            else:
                try:
                    variables[as_var] = yaml.safe_load(content)
                except yaml.YAMLError as e:
                    raise RuntimeError(f"Failed to parse YAML from {source_path}: {e}")
        else:
            variables[as_var] = content.strip()
        return None, None
    
    if as_json:
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as e:
            raise RuntimeError(f"Failed to parse YAML from {source_path}: {e}")
        
        sub_key = source_config.get('sub_key', '.')
        if sub_key != '.':
            sub_key = substitute_variables(sub_key, variables)
            try:
                data = get_nested_value(data, sub_key)
            except KeyError as e:
                raise KeyError(f"Failed to extract sub_key '{sub_key}' from {source_path}: {e}")
        
        data = filter_keys(data, source_config.get('include_keys', []), source_config.get('exclude_keys', []))
    else:
        data = content.strip()
    
    return (target_key if target_key else source_config.get('as', '.')), data


def load_datasources():
    import warnings
    config_path = gshawk.vars.global_args.get('config', '/etc/gs-hawk.json')
    if not config_path:
        raise RuntimeError("Config path not specified")
    if not Path(config_path).exists():
        warnings.warn(f"Datasource config not found: {config_path}")
        return
    
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise RuntimeError(f"Invalid YAML in config {config_path}: {e}")
    
    cache_dir = gshawk.vars.global_args.get('cache_dir', get_default_cache_dir())
    active_cache_keys = set()
    variables, template_vars_to_set, log_context_to_set = {}, {}, {}
    
    # Phase 1: as_variable sources
    for src in config:
        if 'as_variable' in src:
            source = substitute_variables(src['source'], variables)
            if source.startswith(('http://', 'https://')):
                active_cache_keys.add(get_cache_key(source))
            process_source(src, variables)
    
    # Phase 2: normal sources (as)
    for src in config:
        if 'as_variable' not in src and 'as_log_context' not in src:
            source = substitute_variables(src['source'], variables)
            if source.startswith(('http://', 'https://')):
                active_cache_keys.add(get_cache_key(source))
            as_key, data = process_source(src, variables)
            if as_key == '.':
                if isinstance(data, dict):
                    for k, v in data.items():
                        template_vars_to_set[k] = deep_merge(template_vars_to_set.get(k, {}), v) if k in template_vars_to_set else v
                else:
                    template_vars_to_set.setdefault('hawk', {})['data'] = data
            else:
                existing = template_vars_to_set.get(as_key)
                if isinstance(existing, dict) and isinstance(data, dict):
                    template_vars_to_set[as_key] = deep_merge(existing, data)
                else:
                    template_vars_to_set[as_key] = data
    
    # Phase 3: as_log_context sources
    for src in config:
        if 'as_log_context' in src:
            source = substitute_variables(src['source'], variables)
            if source.startswith(('http://', 'https://')):
                active_cache_keys.add(get_cache_key(source))
            as_lc, data = process_source(src, variables, src['as_log_context'])
            if as_lc == '.' and isinstance(data, dict):
                log_context_to_set.update(data)
            else:
                log_context_to_set[as_lc] = data
    
    gshawk.vars.template_vars.update(variables)
    gshawk.vars.global_args.update(variables)
    gshawk.vars.template_vars.update(template_vars_to_set)
    gshawk.vars.log_context.update(log_context_to_set)
    
    purge_stale_cache(cache_dir, active_cache_keys)
    export_cache_metrics(cache_dir, active_cache_keys)

load_datasources()

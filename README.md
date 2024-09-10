# gridscale Hawk

Hawk is a small layer to deterministically apply configuration. It consists of a small templating layer.

## Datasource Configuration

Hawk supports loading JSON data from files and HTTP(S) sources into the template context via a configuration file. This allows dynamic data injection without custom helper plugins.

### Configuration File

The datasource configuration file defaults to `/etc/gs-hawk.json` and can be overridden with `--config <path>`.

The configuration is a JSON array of source objects:

```json
[
  {
    "source": "file:///run/node-id",
    "as_variable": "node_id"
  },
  {
    "source": "file:///run/gridscale/hc.json",
    "as": "hybridcore",
    "sub_key": "hybridcore"
  },
  {
    "source": "file:///run/gridscale/hc.json",
    "as": "node",
    "sub_key": "hybridcore.nodes[${node_id}]"
  },
  {
    "source": "https://api.example.com/v1/data",
    "as": "api_data",
    "cache_key": "api_cache",
    "sub_key": "cluster"
  }
]
```

### Source Object Properties

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `source` | string | - | **Required.** URL of the data source. Supports `file://`, `http://`, `https://`, `inline:`. Variable substitution (`${var}`) is supported. Content must be valid YAML (JSON-compatible). Files may use `.json` extension. |
| `as_variable` | string | - | If present, the raw content (or sub_key value) is stored as a simple variable, not merged into template context. |
| `as_log_context` | string | - | If present, the value is added to `gshawk.vars.log_context` for logging. Processed after all variables are defined. |
| `as` | string | `.` | Target key in template context. Defaults to root merge. |
| `sub_key` | string | `.` | Extract a nested value using dot notation and array indexing (e.g., `hybridcore.nodes[${node_id}]`). Ignored when `json: false`. |
| `json` | boolean | `true` | If `true` (default), parse content as YAML (JSON-compatible). If `false`, treat content as raw string (sub_key has no effect). |
| `include_keys` | array | - | Only include these top-level keys in the merged data. |
| `exclude_keys` | array | - | Exclude these top-level keys from the merged data. |


### Processing Order

1. All sources with `as_variable` are processed first, making variables available for substitution in subsequent sources.
2. Variables are added to template context and global args.
3. Remaining sources (without `as_variable` or `as_log_context`) are processed and deep-merged into the template context.
4. Sources with `as_log_context` are processed and added to the log context.

### Caching

HTTP(S) sources are cached locally. The cache directory is determined as follows:

| User | macOS | Linux |
|------|-------|-------|
| root (uid=0) | `/Library/Caches/gs-hawk` | `/var/cache/gs-hawk` |
| regular user | `~/.cache/gs-hawk` | `~/.cache/gs-hawk` |

The default can be overridden with `--cache-dir <path>`.

- On successful HTTP fetch, the response is cached with a timestamp.
- If HTTP fetch fails after 3 retry attempts, the cached value is used as fallback.
- If no cache is available, Hawk fails to start.
- Cache files not referenced by the current configuration are purged on each run.
- Cache timestamps are exported as Prometheus metrics to `/var/lib/prometheus/node-exporter/hawk-cache.prom` (if the directory exists).
  - Metric format: `hawk_cache_timestamp{cache_key="<key>"} <unix_timestamp>`

### Example: Compute Node Configuration

```json
[
  {
    "source": "file:///run/node-id",
    "as_variable": "node_id"
  },
  {
    "source": "file:///run/gridscale/hc.json",
    "as": "hybridcore",
    "sub_key": "hybridcore"
  },
  {
    "source": "file:///run/gridscale/hc.json",
    "as": "node",
    "sub_key": "hybridcore.nodes[${node_id}]"
  },
  {
    "source": "file:///run/gridscale/boot.json",
    "as": "boot"
  }
]
```

This configuration makes the following available in templates:
- `node_id` - The node ID string
- `hybridcore` - The full hybridcore object
- `node` - The node-specific object from `hybridcore.nodes[<node_id>]`
- `boot` - The boot.json contents

### Example: Using Inline Values and Log Context

```json
[
  {
    "source": "file:///run/node-id",
    "as_variable": "node_id"
  },
  {
    "source": "inline:\"noble-generic\"",
    "as": "default_variant"
  },
  {
    "source": "file:///run/node-id",
    "as_log_context": "node"
  },
  {
    "source": "inline:{\"env\": \"production\", \"region\": \"eu-central\"}",
    "as_log_context": "."
  }
]
```

This configuration:
- Sets `default_variant` to a static string value via `inline:`
- Adds `node` to the log context with the node ID value
- Merges `env` and `region` into the log context from inline JSON

### Example: Reading Raw String Files

```json
[
  {
    "source": "file:///etc/hostname",
    "as_variable": "hostname",
    "json": false
  },
  {
    "source": "file:///run/secrets/api_key",
    "as": "api_key",
    "json": false
  }
]
```

When `json: false`, the file content is treated as a raw string:
- Content is stripped of leading/trailing whitespace
- `sub_key`, `include_keys`, and `exclude_keys` have no effect
- Useful for reading plain text files like hostnames, tokens, or keys

### Commandline Options

| Option | Default | Description |
|--------|---------|-------------|
| `--config` | `/etc/gs-hawk.json` | Path to datasource configuration JSON |
| `--cache-dir` | See below | Directory for HTTP source cache |
| `--metrics-dir` | `/var/lib/prometheus/node-exporter` | Directory for Prometheus metrics export |
| `--show-datasources` | `false` | Output merged datasource JSON and exit |
| `--show-log-context` | `false` | Output log context JSON and exit |
| `--source` | `/usr/share/gs-hawk` | Root dir of Hawk feathers |
| `--target` | `/` | Root dir of Hawk output |
| `--dry-run` | `false` | Do not write output files |
| `--no-diff` | `false` | Do not show diff |
| `--skip-systemd` | `false` | Skip systemd executions and hooks |
| `--log` | `/dev/null` | Log template usage as JSON |
| `--expression` | `''` | Evaluate template string and output result |
| `--target` | `/` | Root dir of Hawk output |
| `--dry-run` | `false` | Do not write output files |
| `--no-diff` | `false` | Do not show diff |
| `--skip-systemd` | `false` | Skip systemd executions and hooks |
| `--log` | `/dev/null` | Log template usage as JSON |
| `--expression` | `''` | Evaluate template string and output result |

Files placed in the `/usr/share/gs-hawk/<feather>` directory will be applied to the target root.

Hawk will smartly detect any systemd unit which was altered during the application of the changes.

Any unit (`.service`, `.path`, `.timer`, `target`, `hook`) will be handled as configured in the `X-Hawk` section of the unit. See below for details.

Files in the Hawk folder will be copied over as-is, with 2 exceptions:

 1. The file in question has a `#!` - the file will be marked executable in the target system
 2. The filename ends with `.jinja2` - The file will get templated and the suffix removed.

### Templating

Hawk enables Jinja2 based templating. Inserted into the template is nothing by default. This requires a plugin.

Included filters:
- `hawk.utils.{ipaddr,ipv4,ipv6}`
- `hawk.utils.regex_replace`
- `hawk.utils.b64{de,en}code`

Helper Methods:
- `hawk.interfaces()` - returns an array of network interfaces
- `hawk.hardware.cpuset_reserve_physical_cores(desired, prefer_E_cores=True)`
  - If desired is positive, the selected cores are returned as a cpu set string (e.g. `0-1,8-9` ,`0,4`, etc.) - (reserve X cores)
  - If desired is negative, the inverse of the selected cores are returned as a cpu set string - (reserve all except X cores)
  - if `prefer_E_cores` is `true` and the CPU is on bigLITTLE topology, E-cores are preferredly allocated. Otherwise cores are allocated by id
  - NOTE: This is not an actual allocation! This is a tool to be able to divide a system up into 2 partitions, by calling this helper in 2 places - once, with a positive count, once with a negative count. Subsequent calls will return the same cpu sets!
- `hawk.hardware.memory_reserve_{bytes,mib,gib}(amount)`
  - Returns a float. Cast as needed.
  - If `amount` is positive, it is checked that the amount is available on the system and then returned
  - If `amount` is negative, the total system memory reduced by the amount specified is returned
  - NOTE: This is not an actual allocation! This is a tool to be able to divide a system up into 2 partitions, by calling this helper in 2 places - once, with a positive count, once with a negative count. Subsequent calls will return the same amount of memory!
- `hawk.hardware.memory_total_{bytes,mib,gib}()`
  - Returns a float. Cast as needed.

### Systemd Units and X-Hawk

Because we need some finesse in dealing with some services, we have a section in Systemd units that is ignored by systemd itself, and just parsed in Hawk.

The `[X-Hawk]` section can be used in any systemd unit deployed via Hawk. For any option not specified the following defaults will be used:

```
[X-Hawk]
RestartOnConfigChange=yes
RestartOnUnitChange=yes
ReloadOnConfigChange=no
EnableUnit=yes
StopOnUnitDisable=no
StartUnit=yes
ExecOnUnitChange=""
ExecOnApply=""
ConfigFile=""
# systemd-analyze condition queries
Condidion...=Query

# If EnableUnit==yes:
[Install]
WantedBy=multi-user.target
```

| Option | Default | Description |
| ----- | -----| --------- |
| `RestartOnConfigChange` | `yes` | If `yes` the affetcted service will restart if one of the paths specified via `ConfigFile` have changed |
| `RestartOnUnitChange` | `yes` | If `yes` Hawk will restart the affected unit if the file content changed. Otherwise it is not restarted |
| `ReloadOnConfigChange` | `no` | If `yes` the affetcted service will reload if one of the paths specified via `ConfigFile` have changed |
| `EnableUnit` | `yes` |  If `yes`, `systemctl enable` is called on the unit. It is *only* started, if `StartUnit` is also `yes`, otherwise it is enabled, but not started. If `no`, disables the Unit. The unit will not be stopped unless `StopUnitOnDisable` is `yes`. |
| `StopUnitOnDisable` | `no` | If `yes` a unit with `EnableUnit=no` will be stopped when deployed. Otherwise the state does not change |
| `StartUnit` | `yes` | If `yes` the unit will get started upon rollout |
| `ExecOnUnitChange` | - | This command will be executed on the node if the file content changed and the deployment conditions were met. Can be specified multiple times. Executed via `bash -c` |
| `ExecOnApply` | - | This command will be executed on the node after Hawk ran and the deployment conditions were met. Can be specified multiple times. Executed via `bash -c` |
| `ConfigFile` | - | Path to a file. When specified, and `Re(start\|load)OnConfigChange=yes` a change in the file will trigger a restart/reload of the unit. Can be specified multiple times. |
| `Condition...`\|`Assert...` | - | If specified in `X-Hawk` section, will be checked at apply time. Units with non-matching conditions will not get copied or handled. If specified in `Unit` section, the units will get copied and hooks get executed. Actual evaluation of the rules happens in systemd at runtime. See [systemd documentation](https://www.freedesktop.org/software/systemd/man/latest/systemd.unit.html#Conditions%20and%20Asserts) for all possible options. |
| `TemplateCondition` | `yes` | If `no` or `False` the unit template will be dropped. Use this for deciding whether to copy a unit over from a templated value. Can be specified multiple times. |

## hawk-crypt

`hawk-crypt` is a commandline tool to encrypt secrets for use in Hawk.

By default `hawk-crypt` will make use of the 1Password CLI to retrieve the encryption keys. This can be disabled by passing `--one-password=false`

The tool requires `CAP_IPC_LOCK` or root permissions on Linux. Alternatively run with `INSECURE_NO_LOCK` defined

## Building and Installation

For gridscale internal use, the packaging is handled automatically via the CI/CD pipeline defined in `.gitlab-ci.yml`. External parties who wish to build and run Hawk independently can use one of the following methods:

### Option 1: Debian Package (Recommended)

Build a Debian package using the provided Makefile:

```bash
# Requires: Ruby fpm gem (gem install fpm)
make deb
```

This creates a `.deb` package in the `dist/` directory with all dependencies configured. Install with:

```bash
sudo dpkg -i dist/gs-hawk_*.deb
```

The package includes:
- Python source files in `/usr/lib/gs-hawk/`
- Binaries (`hawk`, `hawk-crypt`) in `/usr/bin/`
- Systemd helper units in `/usr/lib/systemd/system/`
- Directory for feathers in `/usr/share/gs-hawk/`

### Option 2: Virtual Environment

Run Hawk directly from a Python virtual environment:

```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run Hawk
./bin/hawk --help
./bin/hawk-crypt --help
```

### Option 3: PEX (Portable Python Executable)

Package Hawk as a self-contained PEX binary:

```bash
# Install pex
pip install pex

# Build PEX binary
pex -r requirements.txt -c hawk -o hawk.pex bin/hawk

# Run
./hawk.pex --help
```

PEX bundles all dependencies into a single executable file, making it easy to distribute and run on different systems.


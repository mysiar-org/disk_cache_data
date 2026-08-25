# disk_cache_data decorator

Fully configurable decorator with functionnality as Streamlit st.cache_data with local disk storage as backend.

## Installation

```bash
pip install mysiar-disk-cache-data
```

## Usage 

### disk_cache_data

`ttl` accepts:
- `None` or no `ttl` at all - the entry never expires, same as `st.cache_data`
- a number of seconds, e.g. `30`
- a string, e.g. `"30s"`, `"5m"`, `"2h"`, `"1d"`, `"1d 4h"`
- `0` - caching is bypassed and the function runs every call

Configuration by environment variables:
- DISK_CACHE_DISABLED=1 - disable caching
- DISK_CACHE_DEBUG=1 - enable debug logging (currently by print statements)
- DISK_CACHE_DIR=/path/to/cache/dir - set custom cache directory (default is `/tmp/disk_cache`)
- DISK_CACHE_NAMESPACE - subfolder for namespacing (default is `disk_cache`)

```python
from mysiar.disk_cache_data import disk_cache_data

@disk_cache_data(ttl="30s")
def load_data(a, b):
    return a + b

# first call is whole function process
result = load_data(1, 2)
# each next call within ttl is cached
result = load_data(1, 2)
```

### Excluding an argument from the cache key

An argument whose parameter name starts with an underscore is left out of the cache
key, as in `st.cache_data`. It still reaches the function on every computed call, but
changing it does not create a new entry. Use it for values the result does not depend
on, or for values that are expensive or impossible to hash.

The name is resolved from the function signature, so the rule applies whether the
caller passes the argument positionally or by keyword.

```python
@disk_cache_data(ttl="30s")
def load_data(a, _conn):
    return _conn.query(a)

load_data(1, conn_one)   # computed and cached
load_data(1, conn_two)   # hits the entry above; conn_two is not part of the key
```

Values absorbed by a `*args` parameter have no name to test, so they are always
hashed.

Arguments are keyed by name rather than by position, so a positional call and a
keyword call with the same values reach the same entry, and keyword order never
matters:

```python
load_data(1, conn)             # computed and cached
load_data(a=1, _conn=conn)     # hits the same entry
```

### Clearing the cache of one function

Each decorated function stores its entries in its own subdirectory of the namespace,
so a function can drop its own cache without touching any other function.

```python
# drop every cached entry of load_data in the current namespace
load_data.clear()

# drop only the entry cached for these arguments
load_data.clear(1, 2)

# drop the entries of load_data in every namespace under DISK_CACHE_DIR
load_data.clear_all_namespaces()

# path of the directory holding the entries of load_data
path = load_data.cache_dir()
```

### disk_cache_cleanup

Function to cleanup expired or corrupt cache files across all namespaces. It also removes
emptied function directories and leftovers of interrupted `clear()` calls. Entries cached
with `ttl=None` never expire, so cleanup leaves them alone - drop them with `clear()`.

An entry is a pair of files, `<key>.pkl` and `<key>.meta`. A crash between the two writes
leaves one file without its pair, and cleanup removes it once it is older than 60 seconds.
The grace period keeps a writer that is still creating the pair from losing it, and it can
be changed per call.

```python
from mysiar.disk_cache_data import disk_cache_cleanup

disk_cache_cleanup()

# drop half written entries immediately instead of after 60 seconds
disk_cache_cleanup(orphan_grace_seconds=0)
```

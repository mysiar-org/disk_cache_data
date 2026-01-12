# disk_cache_data decorator

Fully configurable decorator with functionnality as Streamlit st.cache_data with local disk storage as backend.

## Installation

```bash
pip install mysiar-disk-cache-data
```

## Usage 

### disk_cache_data

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

### disk_cache_cleanup

Function to cleanup expired cache files.

```python
from mysiar.disk_cache_data import disk_cache_cleanup
disk_cache_cleanup()
```

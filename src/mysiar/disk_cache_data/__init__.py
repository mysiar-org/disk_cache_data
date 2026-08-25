import functools
import hashlib
import inspect
import os
import pickle
import re
import shutil
import threading
import time

DISK_CACHE_DEFAULT_NAMESPACE = "disk_cache"
DISK_CACHE_DIR = os.getenv("DISK_CACHE_DIR", "/tmp/disk_cache")
# How long a file without its pair is left alone before cleanup removes it.
DISK_CACHE_ORPHAN_GRACE_SECONDS = 60

_TRASH_SUFFIX = ".trash-"
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")

_disk_lock = threading.Lock()


def _debug_enabled() -> bool:
    return os.getenv("DISK_CACHE_DEBUG", "").lower() in ("1", "true", "yes", "on")


def _cache_disabled() -> bool:
    return os.getenv("DISK_CACHE_DISABLED", "").lower() in ("1", "true", "yes", "on")


def _namespace_dir(namespace=None) -> str:
    if namespace is None:
        namespace = os.getenv("DISK_CACHE_NAMESPACE", DISK_CACHE_DEFAULT_NAMESPACE)
    return os.path.join(DISK_CACHE_DIR, namespace)


def function_dir_name(func) -> str:
    """
    Directory name holding every cached entry of a single function.

    Readable label plus a short digest of module and qualified name, so two
    same-named functions from different modules never share a directory.
    """
    digest = hashlib.sha256(f"{func.__module__}.{func.__qualname__}".encode()).hexdigest()[:8]
    label = _UNSAFE_NAME_CHARS.sub("_", func.__qualname__)[:64].strip("_") or "func"
    return f"{label}-{digest}"


def function_dir(func, namespace=None) -> str:
    """Full path of the cache directory of a single function."""
    return os.path.join(_namespace_dir(namespace), function_dir_name(func))


def _positional_arg_names(func) -> tuple:
    """Parameter name of each positional slot, None where that slot has no name.

    Slots filled by *args, and keyword-only parameters, have no positional name.
    Values landing there are hashed as-is, since there is no name to test for the
    underscore prefix. Same rule as st.cache_data.
    """
    try:
        params = inspect.signature(func).parameters.values()
    except (TypeError, ValueError):
        # Builtins and some C callables expose no signature. Without names, every
        # positional argument is hashed, which is the safe direction.
        return ()

    nameable = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    return tuple(p.name if p.kind in nameable else None for p in params)


def _entry_key(positional_names, args, kwargs) -> str:
    """Hash of the call arguments.

    An argument whose parameter name starts with an underscore is left out of the
    key, as in st.cache_data: it still reaches the function, but changing it does
    not mint a new entry. Positional arguments are resolved to their parameter
    name first, so the exclusion holds whether the caller passes them
    positionally or by keyword, and both call styles reach the same entry.

    Named arguments are sorted by name, so keyword order cannot change the key.
    Values with no name keep their positional order, which is all that identifies
    them.
    """
    named = {}
    unnamed = []

    for index, value in enumerate(args):
        name = positional_names[index] if index < len(positional_names) else None
        if name is None:
            unnamed.append(value)
        elif not name.startswith("_"):
            named[name] = value

    for name, value in kwargs.items():
        if not name.startswith("_"):
            named[name] = value

    payload = ([(name, named[name]) for name in sorted(named)], unnamed)
    return hashlib.sha256(pickle.dumps(payload)).hexdigest()


def _mtime(path) -> float:
    """Modification time, or 0.0 when the file is already gone."""
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _purge_dir(path) -> int:
    """
    Remove a directory and return the number of files removed.

    Renamed out of the way first, so a concurrent writer cannot land a file
    between listing and deleting and survive the purge.
    """
    if not os.path.isdir(path):
        return 0

    trash_path = f"{path}{_TRASH_SUFFIX}{os.getpid()}-{time.time_ns()}"
    try:
        os.rename(path, trash_path)
    except OSError:
        # Another process already renamed or removed it.
        return 0

    removed = sum(len(files) for _root, _dirs, files in os.walk(trash_path))
    shutil.rmtree(trash_path, ignore_errors=True)
    return removed


def disk_cache_data(ttl=None):
    """
    Disk-backed equivalent of st.cache_data with:
    - Streamlit-style TTL strings ('5m', '2h', '1d 4h')
    - ttl = 0 disables caching
    - ttl = None never expires, as in st.cache_data
    - DISK_CACHE_DISABLED=1 bypasses caching globally
    - DISK_CACHE_DEBUG=1 prints cache events
    - DISK_CACHE_NAMESPACE subfolder for namespacing
    - DISK_CACHE_DIR sets the directory for cached files
    - decorated_function.clear() drops the cached entries of that function
    """

    def decorator(func):
        # Resolved once here rather than per call: inspect.signature is not cheap
        # enough for a hot path, and the signature cannot change afterwards.
        positional_names = _positional_arg_names(func)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # ---- 0. Global disable ----
            debug_flag = _debug_enabled()

            if _cache_disabled():
                if debug_flag:
                    print(f"[disk-cache-data] DISABLED → running {func.__name__}() without caching")
                return func(*args, **kwargs)

            # ---- 1. TTL handling ----
            if ttl == 0:
                if debug_flag:
                    print(f"[disk-cache-data] TTL=0 → bypassing cache for {func.__name__}()")
                return func(*args, **kwargs)

            # None means the entry never expires.
            effective_ttl = parse_ttl(ttl)

            # ---- 2. Locate entry inside the directory of this function ----
            key_hash = _entry_key(positional_names, args, kwargs)
            fn_dir = function_dir(func)
            os.makedirs(fn_dir, exist_ok=True)

            data_path = os.path.join(fn_dir, f"{key_hash}.pkl")
            meta_path = os.path.join(fn_dir, f"{key_hash}.meta")

            # ---- 3. Try load from disk ----
            if os.path.exists(data_path) and os.path.exists(meta_path):
                try:
                    with open(meta_path, "rb") as f:
                        expires_at = pickle.load(f)

                    if expires_at is None or time.time() < expires_at:
                        with open(data_path, "rb") as f:
                            value = pickle.load(f)
                        if debug_flag:
                            print(f"[disk-cache-data] HIT → {func.__name__}() key={key_hash}")
                        return value
                    else:
                        if debug_flag:
                            print(f"[disk-cache-data] EXPIRED → deleting key={key_hash}")
                        safe_delete(data_path)
                        safe_delete(meta_path)
                except Exception:
                    if debug_flag:
                        print(f"[disk-cache-data] CORRUPT → deleting key={key_hash}")
                    safe_delete(data_path)
                    safe_delete(meta_path)

            # ---- 4. Compute value ----
            if debug_flag:
                print(f"[disk-cache-data] MISS → computing {func.__name__}()")

            result = func(*args, **kwargs)

            # ---- 5. Store to disk ----
            expires_at = None if effective_ttl is None else time.time() + effective_ttl
            payload = pickle.dumps(result)

            with _disk_lock:
                try:
                    os.makedirs(fn_dir, exist_ok=True)
                    with open(data_path, "wb") as f:
                        f.write(payload)
                    with open(meta_path, "wb") as f:
                        pickle.dump(expires_at, f)

                    if debug_flag:
                        ttl_text = "never" if effective_ttl is None else f"{effective_ttl}s"
                        print(f"[disk-cache-data] STORE → key={key_hash}, ttl={ttl_text}")
                except Exception as e:
                    if debug_flag:
                        print(f"[disk-cache-data] ERROR storing key={key_hash}: {e}")

            return result

        def clear(*args, **kwargs):
            """
            Drop cached entries of this function in the current namespace.

            Called without arguments it drops every entry. Called with the same
            arguments used at call time it drops only that single entry.
            """
            debug_flag = _debug_enabled()
            fn_dir = function_dir(func)

            if args or kwargs:
                key_hash = _entry_key(positional_names, args, kwargs)
                with _disk_lock:
                    safe_delete(os.path.join(fn_dir, f"{key_hash}.pkl"))
                    safe_delete(os.path.join(fn_dir, f"{key_hash}.meta"))
                if debug_flag:
                    print(f"[disk-cache-data] CLEAR → {func.__name__}() key={key_hash}")
                return

            removed = _purge_dir(fn_dir)
            if debug_flag:
                print(f"[disk-cache-data] CLEAR → {func.__name__}() removed {removed:,} files")

        def clear_all_namespaces():
            """Drop cached entries of this function in every namespace under DISK_CACHE_DIR."""
            debug_flag = _debug_enabled()
            dir_name = function_dir_name(func)
            removed = 0

            if os.path.isdir(DISK_CACHE_DIR):
                for namespace in os.listdir(DISK_CACHE_DIR):
                    removed += _purge_dir(os.path.join(DISK_CACHE_DIR, namespace, dir_name))

            if debug_flag:
                print(f"[disk-cache-data] CLEAR ALL → {func.__name__}() removed {removed:,} files")

        wrapper.clear = clear
        wrapper.clear_all_namespaces = clear_all_namespaces
        wrapper.cache_dir = lambda namespace=None: function_dir(func, namespace)

        return wrapper

    return decorator


def parse_ttl(ttl) -> int | None:
    """
    Convert TTL into seconds.
    Accepts:
      - None (entry never expires, as in st.cache_data)
      - int or float (seconds)
      - strings like '5m', '2h', '1d 4h'
      - bare numeric strings like '15' (seconds)
    Raises:
      - ValueError for invalid TTL formats
    """
    # None → never expires
    if ttl is None:
        return None

    # Numeric → seconds
    if isinstance(ttl, (int, float)):
        return int(ttl)

    ttl_str = str(ttl).strip().lower()

    # Bare number string → seconds
    if ttl_str.isdigit():
        return int(ttl_str)

    total = 0
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}

    matches = re.findall(r"(\d+)\s*([smhd])", ttl_str)

    # If regex found nothing → invalid TTL
    if not matches:
        raise ValueError(f"Invalid TTL format: {ttl}")

    for amount, unit in matches:
        total += int(amount) * units[unit]

    return total


def safe_delete(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except Exception:
        pass


def disk_cache_cleanup(orphan_grace_seconds=DISK_CACHE_ORPHAN_GRACE_SECONDS):
    """
    Scans all namespaces inside DISK_CACHE_DIR and removes expired or corrupt entries.
    Half written entries, leftover directories of interrupted clear() calls and
    emptied function directories are removed too.

    An entry is a pair of files, <key>.pkl and <key>.meta. A file without its
    pair is removed once it is older than orphan_grace_seconds, so a writer that
    is still creating the pair does not lose it.

    Safe, atomic, and silent unless DISK_CACHE_DEBUG=1.
    """
    base_root = DISK_CACHE_DIR
    debug_flag = _debug_enabled()

    if not os.path.exists(base_root):
        return

    now = time.time()

    for namespace in os.listdir(base_root):
        ns_path = os.path.join(base_root, namespace)
        if not os.path.isdir(ns_path):
            continue

        for dirpath, dirnames, filenames in os.walk(ns_path, topdown=True):
            # Interrupted clear() left these behind.
            for dirname in [d for d in dirnames if _TRASH_SUFFIX in d]:
                dirnames.remove(dirname)
                shutil.rmtree(os.path.join(dirpath, dirname), ignore_errors=True)
                if debug_flag:
                    print(f"[disk-cache-data] CLEANUP: LEFTOVER → {dirname}")

            meta_keys = {f[:-5] for f in filenames if f.endswith(".meta")}
            data_keys = {f[:-4] for f in filenames if f.endswith(".pkl")}

            # Half written entry → drop the file that has no pair.
            orphans = set()
            for key in sorted(meta_keys ^ data_keys):
                suffix = "pkl" if key in data_keys else "meta"
                orphan_path = os.path.join(dirpath, f"{key}.{suffix}")

                if now - _mtime(orphan_path) < orphan_grace_seconds:
                    continue

                safe_delete(orphan_path)
                orphans.add(key)
                if debug_flag:
                    print(f"[disk-cache-data] CLEANUP: ORPHAN {suffix} → {key}")

            for key in sorted(meta_keys - orphans):
                meta_path = os.path.join(dirpath, f"{key}.meta")
                data_path = os.path.join(dirpath, f"{key}.pkl")

                try:
                    with open(meta_path, "rb") as f:
                        expires_at = pickle.load(f)
                except Exception:
                    # Corrupt metadata → delete both
                    safe_delete(meta_path)
                    safe_delete(data_path)
                    if debug_flag:
                        print(f"[disk-cache-data] CLEANUP: CORRUPT meta → {key}")
                    continue

                # None means the entry never expires.
                if expires_at is None:
                    continue

                if now >= expires_at:
                    safe_delete(meta_path)
                    safe_delete(data_path)
                    if debug_flag:
                        print(f"[disk-cache-data] CLEANUP: EXPIRED → {key}")

        _prune_empty_dirs(ns_path, debug_flag)


def _prune_empty_dirs(ns_path, debug_flag=False):
    """Remove function directories left empty inside a namespace."""
    for entry in os.listdir(ns_path):
        dir_path = os.path.join(ns_path, entry)
        if not os.path.isdir(dir_path):
            continue
        try:
            os.rmdir(dir_path)
            if debug_flag:
                print(f"[disk-cache-data] CLEANUP: EMPTY DIR → {entry}")
        except OSError:
            # Not empty, or removed concurrently.
            pass

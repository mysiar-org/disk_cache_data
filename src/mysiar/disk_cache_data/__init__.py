import functools
import hashlib
import os
import pickle
import re
import threading
import time

DISK_CACHE_DEFAULT_TTL = "5m"
DISK_CACHE_DEFAULT_NAMESPACE = "disk_cache"
DISK_CACHE_DIR = os.getenv("DISK_CACHE_DIR", "/tmp/disk_cache")

_disk_lock = threading.Lock()


def disk_cache_data(ttl=None):
    """
    Disk-backed equivalent of st.cache_data with:
    - Streamlit-style TTL strings ('5m', '2h', '1d 4h')
    - ttl = 0 disables caching
    - ttl = None uses DEFAULT_TTL
    - DISK_CACHE_DISABLED=1 bypasses caching globally
    - DISK_CACHE_DEBUG=1 prints cache events
    - DISK_CACHE_NAMESPACE subfolder for namespacing
    - DISK_CACHE_DIR sets the directory for cached files
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # ---- 0. Global disable ----
            disabled_flag = os.getenv("DISK_CACHE_DISABLED", "").lower()
            debug_flag = os.getenv("DISK_CACHE_DEBUG", "").lower() in (
                "1",
                "true",
                "yes",
                "on",
            )

            if disabled_flag in ("1", "true", "yes", "on"):
                if debug_flag:
                    print(f"[disk-cache-data] DISABLED → running {func.__name__}() without caching")
                return func(*args, **kwargs)

            # ---- 1. Filter out private kwargs ----
            filtered_kwargs = {k: v for k, v in kwargs.items() if not k.startswith("_")}

            # ---- 2. Build deterministic key ----
            key_raw = (func.__module__, func.__name__, args, filtered_kwargs)
            key_bytes = pickle.dumps(key_raw)
            key_hash = hashlib.sha256(key_bytes).hexdigest()

            # ---- 3. Namespace ----
            namespace = os.getenv("DISK_CACHE_NAMESPACE", DISK_CACHE_DEFAULT_NAMESPACE)
            base_dir = os.path.join(DISK_CACHE_DIR, namespace)
            os.makedirs(base_dir, exist_ok=True)

            data_path = os.path.join(base_dir, f"{key_hash}.pkl")
            meta_path = os.path.join(base_dir, f"{key_hash}.meta")

            # ---- 4. TTL handling ----
            if ttl == 0:
                if debug_flag:
                    print(f"[disk-cache-data] TTL=0 → bypassing cache for {func.__name__}()")
                return func(*args, **kwargs)

            effective_ttl = parse_ttl(DISK_CACHE_DEFAULT_TTL if ttl is None else ttl)

            # ---- 5. Try load from disk ----
            if os.path.exists(data_path) and os.path.exists(meta_path):
                try:
                    with open(meta_path, "rb") as f:
                        expires_at = pickle.load(f)

                    if time.time() < expires_at:
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

            # ---- 6. Compute value ----
            if debug_flag:
                print(f"[disk-cache-data] MISS → computing {func.__name__}()")

            result = func(*args, **kwargs)

            # ---- 7. Store to disk ----
            expires_at = time.time() + effective_ttl
            payload = pickle.dumps(result)

            with _disk_lock:
                try:
                    with open(data_path, "wb") as f:
                        f.write(payload)
                    with open(meta_path, "wb") as f:
                        pickle.dump(expires_at, f)

                    if debug_flag:
                        print(f"[disk-cache-data] STORE → key={key_hash}, ttl={effective_ttl}s")
                except Exception as e:
                    if debug_flag:
                        print(f"[disk-cache-data] ERROR storing key={key_hash}: {e}")

            return result

        return wrapper

    return decorator


def parse_ttl(ttl) -> int:
    """
    Convert TTL into seconds.
    Accepts:
      - int or float (seconds)
      - strings like '5m', '2h', '1d 4h'
      - bare numeric strings like '15' (seconds)
    Raises:
      - ValueError for invalid TTL formats
    """
    if ttl is None:
        ttl = DISK_CACHE_DEFAULT_TTL

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


def disk_cache_cleanup():
    """
    Scans all namespaces inside DISK_CACHE_DIR and removes expired or corrupt entries.
    Safe, atomic, and silent unless DISK_CACHE_DEBUG=1.
    """
    base_root = DISK_CACHE_DIR
    debug_flag = os.getenv("DISK_CACHE_DEBUG", "").lower() in ("1", "true", "yes", "on")

    if not os.path.exists(base_root):
        return

    now = time.time()

    for namespace in os.listdir(base_root):
        ns_path = os.path.join(base_root, namespace)
        if not os.path.isdir(ns_path):
            continue

        for fname in os.listdir(ns_path):
            if not fname.endswith(".meta"):
                continue

            key = fname[:-5]  # strip .meta
            meta_path = os.path.join(ns_path, fname)
            data_path = os.path.join(ns_path, f"{key}.pkl")

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

            if now >= expires_at:
                safe_delete(meta_path)
                safe_delete(data_path)
                if debug_flag:
                    print(f"[disk-cache-data] CLEANUP: EXPIRED → {key}")

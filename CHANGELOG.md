# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](http://keepachangelog.com/en/1.0.0/) and this project adheres
to [Semantic Versioning](http://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-08-25

### Added

- Support for Python 3.10 and 3.11. `requires-python` drops from `>=3.12` to `>=3.10`, and the test
  workflow runs the suite on 3.10, 3.11, 3.12 and 3.13.

## [0.2.0] - 2026-08-25

### Added

- An argument whose parameter name starts with an underscore is left out of the cache key, as in
  `st.cache_data`. It still reaches the function on every computed call, but changing it does not
  create a new entry. Use it for connections, clients and other values the result does not depend on,
  or that cannot be pickled.
- GitHub Actions workflow running ruff and the test suite on Python 3.12 and 3.13.

### Changed

- Arguments are keyed by parameter name instead of by position, so a positional call and a keyword
  call with the same values reach the same entry. Values absorbed by a `*args` parameter have no name
  and keep being keyed by position.
- The function signature is inspected once at decoration time rather than on every call.

## [0.1.0] - 2026-08-19

### Added

- `clear()` on a decorated function drops its cached entries in the current namespace. Called with
  the arguments of a single call it drops only that entry.
- `clear_all_namespaces()` drops the entries of a function in every namespace under `DISK_CACHE_DIR`.
- `cache_dir()` returns the directory holding the entries of a function.
- Each function now caches into its own subdirectory of the namespace, named after the function plus a
  digest of its module and qualified name, so same-named functions from different modules never share
  a directory.
- `disk_cache_cleanup()` takes an `orphan_grace_seconds` argument and removes half written entries, the
  leftover directories of an interrupted `clear()`, and function directories left empty.

### Changed

- `ttl=None` now means the entry never expires, matching `st.cache_data`. It previously fell back to a
  five minute default, and the `DISK_CACHE_DEFAULT_TTL` constant is gone.
- `clear()` removes a directory by renaming it out of the way first, so an entry written concurrently
  cannot survive the purge.
- Test suite reports coverage.

## [0.0.1] - 2026-01-12

First release.

### Added

- `disk_cache_data` decorator, a disk-backed equivalent of `st.cache_data`, storing pickled results
  under `DISK_CACHE_DIR`.
- TTL as seconds or as a Streamlit-style string (`"30s"`, `"5m"`, `"2h"`, `"1d 4h"`); `ttl=0` bypasses
  the cache.
- Environment variables `DISK_CACHE_DIR`, `DISK_CACHE_NAMESPACE`, `DISK_CACHE_DISABLED` and
  `DISK_CACHE_DEBUG`.
- Keyword arguments prefixed with an underscore excluded from the cache key.
- Expired and corrupt entries dropped on read.
- `disk_cache_cleanup()` sweeps expired and corrupt entries across all namespaces.

import io
import os
import pickle
import shutil
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from pandas.testing import assert_frame_equal

from src.mysiar.disk_cache_data import (
    DISK_CACHE_DEFAULT_NAMESPACE,
    disk_cache_cleanup,
    disk_cache_data,
    function_dir_name,
    safe_delete,
)


@disk_cache_data(ttl="10s")
def load_data(a, b):
    """Add two numbers."""
    print("function")
    return a + b


@disk_cache_data(ttl="12h")
def load_data_pd(cache="long-live"):
    print("function-pd")
    return pd.DataFrame({"data": [1, 2, 3]})


@disk_cache_data(ttl=0)
def load_data_no_cache(a):
    print("function-no-cache")
    return a + 1


@disk_cache_data(ttl="10s")
def load_data_private_kwargs(a, _token=None):
    print("function-private")
    return (a, _token)


@disk_cache_data(ttl="10s")
def load_data_private_positional(a, _token):
    print("function-private-positional")
    return (a, _token)


@disk_cache_data(ttl="10s")
def load_data_private_star_args(a, *rest):
    print("function-private-star")
    return (a, rest)


def _decorated_from_module(module_name):
    """Build a decorated function whose qualified name is shared across modules."""

    def sample(a):
        return a

    sample.__module__ = module_name
    return disk_cache_data(ttl="10s")(sample)


class DiskCacheDataTestCase(unittest.TestCase):
    """Disk cache data decorator tests."""

    def setUp(self) -> None:
        path = os.getenv("DISK_CACHE_DIR", None)
        if path is None:
            raise RuntimeError("DISK_CACHE_DIR environment variable is not set")

        p = Path(path)
        # ignore_errors keeps the wipe safe on network filesystems, where a
        # lingering handle leaves an undeletable .nfsXXXX entry behind.
        shutil.rmtree(p, ignore_errors=True)

        p.mkdir(parents=True, exist_ok=True)
        disk_cache_cleanup()

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_cache(self, mock_stdout) -> None:
        start = time.time()

        load_data(1, 2)
        load_data(1, 2)
        load_data(1, 2)

        output = mock_stdout.getvalue().strip().splitlines()
        self.assertEqual(len(output), 1)

        for line in output:
            self.assertIn("function", line)

        meta_file = self.__find_pkl_file()
        value = self.__load_pkl_value(meta_file)

        self.assertEqual(value, 3)

        meta_file = self.__find_pkl_file(ext="meta")
        meta_value = self.__load_pkl_value(meta_file)

        ttl = 10
        lower = start + ttl - 1
        upper = start + ttl + 1
        self.assertTrue(lower <= meta_value <= upper)

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_cache_pd(self, mock_stdout) -> None:
        start = time.time()

        df1 = load_data_pd()
        self.assertIsInstance(df1, pd.DataFrame)
        df2 = load_data_pd()
        self.assertIsInstance(df2, pd.DataFrame)
        df3 = load_data_pd()
        self.assertIsInstance(df3, pd.DataFrame)

        output = mock_stdout.getvalue().strip().splitlines()
        print(output)
        self.assertEqual(len(output), 1)

        for line in output:
            self.assertIn("function-pd", line)

        value_file = self.__find_pkl_file()
        value = self.__load_pkl_value(value_file)

        self.assertIsInstance(value, pd.DataFrame)
        assert_frame_equal(value, pd.DataFrame({"data": [1, 2, 3]}))

        meta_file = self.__find_pkl_file(ext="meta")
        meta_value = self.__load_pkl_value(meta_file)

        ttl = 12 * 3600
        lower = start + ttl - 1
        upper = start + ttl + 1
        self.assertTrue(lower <= meta_value <= upper)

    @staticmethod
    def __find_pkl_file(ext: str = "pkl") -> Path:
        # Read env vars
        cache_dir = os.environ.get("DISK_CACHE_DIR")
        namespace = os.environ.get("DISK_CACHE_NAMESPACE")

        if not cache_dir:
            raise RuntimeError("DISK_CACHE_DIR is not set")

        if not namespace:
            raise RuntimeError("DISK_CACHE_NAMESPACE is not set")

        # Build full namespace path
        ns_path = Path(cache_dir) / namespace

        if not ns_path.is_dir():
            raise RuntimeError(f"Namespace directory does not exist: {ns_path}")

        # Find first .pkl file, entries live in a subdirectory per function
        for file in ns_path.rglob(f"*.{ext}"):
            return file

        raise FileNotFoundError(f"No .{ext} files found in namespace: {ns_path}")

    @staticmethod
    def __load_pkl_value(pkl_path: Path):
        if not pkl_path.is_file():
            raise FileNotFoundError(f"Pickle file does not exist: {pkl_path}")

        with pkl_path.open("rb") as f:
            data = pickle.load(f)

        return data


class DiskCacheDataBehaviourTestCase(unittest.TestCase):
    """Cache bypassing, key building, expiry and corruption handling."""

    def setUp(self) -> None:
        path = os.getenv("DISK_CACHE_DIR", None)
        if path is None:
            raise RuntimeError("DISK_CACHE_DIR environment variable is not set")

        self.cache_dir = Path(path)
        shutil.rmtree(self.cache_dir, ignore_errors=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        namespace = os.getenv("DISK_CACHE_NAMESPACE", None)
        if namespace is None:
            raise RuntimeError("DISK_CACHE_NAMESPACE environment variable is not set")
        self.ns_dir = self.cache_dir / namespace

    def __fn_dir(self, func) -> Path:
        return self.ns_dir / function_dir_name(func)

    def __entries(self, func) -> int:
        fn_dir = self.__fn_dir(func)
        if not fn_dir.is_dir():
            return 0
        return len(list(fn_dir.glob("*.pkl")))

    @staticmethod
    def __calls(mock_stdout, marker: str) -> int:
        return len([line for line in mock_stdout.getvalue().splitlines() if line == marker])

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_ttl_zero_bypasses_cache(self, mock_stdout) -> None:
        self.assertEqual(load_data_no_cache(1), 2)
        self.assertEqual(load_data_no_cache(1), 2)

        self.assertEqual(self.__calls(mock_stdout, "function-no-cache"), 2)
        self.assertFalse(self.__fn_dir(load_data_no_cache).exists())

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_disk_cache_disabled_bypasses_cache(self, mock_stdout) -> None:
        for flag in ("1", "true", "yes", "on"):
            with self.subTest(flag=flag), patch.dict(os.environ, {"DISK_CACHE_DISABLED": flag}):
                self.assertEqual(load_data(1, 2), 3)

        self.assertEqual(self.__calls(mock_stdout, "function"), 4)
        self.assertEqual(self.__entries(load_data), 0)

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_caching_resumes_when_disabled_flag_is_off(self, mock_stdout) -> None:
        with patch.dict(os.environ, {"DISK_CACHE_DISABLED": "0"}):
            load_data(1, 2)
            load_data(1, 2)

        self.assertEqual(self.__calls(mock_stdout, "function"), 1)
        self.assertEqual(self.__entries(load_data), 1)

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_different_arguments_are_cached_separately(self, mock_stdout) -> None:
        self.assertEqual(load_data(1, 2), 3)
        self.assertEqual(load_data(2, 3), 5)
        self.assertEqual(load_data(1, 2), 3)
        self.assertEqual(load_data(2, 3), 5)

        self.assertEqual(self.__calls(mock_stdout, "function"), 2)
        self.assertEqual(self.__entries(load_data), 2)

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_keyword_order_hits_the_same_entry(self, mock_stdout) -> None:
        self.assertEqual(load_data(a=1, b=2), 3)
        self.assertEqual(load_data(b=2, a=1), 3)

        self.assertEqual(self.__calls(mock_stdout, "function"), 1)
        self.assertEqual(self.__entries(load_data), 1)

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_private_kwargs_are_passed_to_function_but_kept_out_of_the_key(self, mock_stdout) -> None:
        first = load_data_private_kwargs(1, _token="first")
        second = load_data_private_kwargs(1, _token="second")

        # The private kwarg reaches the function on the computed call.
        self.assertEqual(first, (1, "first"))
        # A different private kwarg still hits the entry cached above.
        self.assertEqual(second, (1, "first"))
        self.assertEqual(self.__calls(mock_stdout, "function-private"), 1)
        self.assertEqual(self.__entries(load_data_private_kwargs), 1)

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_private_args_are_kept_out_of_the_key_when_passed_positionally(self, mock_stdout) -> None:
        first = load_data_private_positional(1, "first")
        second = load_data_private_positional(1, "second")

        # The name is resolved from the signature, so the underscore rule applies
        # to a positional argument exactly as it does to a keyword one.
        self.assertEqual(first, (1, "first"))
        self.assertEqual(second, (1, "first"))
        self.assertEqual(self.__calls(mock_stdout, "function-private-positional"), 1)
        self.assertEqual(self.__entries(load_data_private_positional), 1)

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_positional_and_keyword_calls_share_one_entry(self, mock_stdout) -> None:
        self.assertEqual(load_data(1, 2), 3)
        self.assertEqual(load_data(a=1, b=2), 3)
        self.assertEqual(load_data(1, b=2), 3)

        self.assertEqual(self.__calls(mock_stdout, "function"), 1)
        self.assertEqual(self.__entries(load_data), 1)

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_values_absorbed_by_star_args_stay_in_the_key(self, mock_stdout) -> None:
        # A *args slot has no parameter name to test, so its values are hashed.
        self.assertEqual(load_data_private_star_args(1, "x"), (1, ("x",)))
        self.assertEqual(load_data_private_star_args(1, "y"), (1, ("y",)))

        self.assertEqual(self.__calls(mock_stdout, "function-private-star"), 2)
        self.assertEqual(self.__entries(load_data_private_star_args), 2)

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_expired_entry_is_recomputed_and_replaced(self, mock_stdout) -> None:
        load_data(1, 2)

        meta_path = next(self.__fn_dir(load_data).glob("*.meta"))
        with meta_path.open("wb") as f:
            pickle.dump(time.time() - 1, f)

        self.assertEqual(load_data(1, 2), 3)

        self.assertEqual(self.__calls(mock_stdout, "function"), 2)
        self.assertEqual(self.__entries(load_data), 1)
        with meta_path.open("rb") as f:
            self.assertGreater(pickle.load(f), time.time())

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_corrupt_meta_is_recomputed(self, mock_stdout) -> None:
        load_data(1, 2)
        next(self.__fn_dir(load_data).glob("*.meta")).write_bytes(b"not-a-pickle")

        self.assertEqual(load_data(1, 2), 3)

        self.assertEqual(self.__calls(mock_stdout, "function"), 2)
        self.assertEqual(self.__entries(load_data), 1)

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_corrupt_data_is_recomputed(self, mock_stdout) -> None:
        load_data(1, 2)
        next(self.__fn_dir(load_data).glob("*.pkl")).write_bytes(b"not-a-pickle")

        self.assertEqual(load_data(1, 2), 3)

        self.assertEqual(self.__calls(mock_stdout, "function"), 2)

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_missing_meta_is_recomputed(self, mock_stdout) -> None:
        load_data(1, 2)
        next(self.__fn_dir(load_data).glob("*.meta")).unlink()

        self.assertEqual(load_data(1, 2), 3)

        self.assertEqual(self.__calls(mock_stdout, "function"), 2)
        self.assertEqual(self.__entries(load_data), 1)

    def test_default_namespace_is_used_when_env_var_is_absent(self) -> None:
        with patch.dict(os.environ):
            del os.environ["DISK_CACHE_NAMESPACE"]
            load_data(1, 2)

        default_dir = self.cache_dir / DISK_CACHE_DEFAULT_NAMESPACE / function_dir_name(load_data)
        self.assertTrue(default_dir.is_dir())
        self.assertEqual(len(list(default_dir.glob("*.pkl"))), 1)

    def test_same_qualified_name_in_two_modules_does_not_collide(self) -> None:
        first = _decorated_from_module("pkg_one.mod")
        second = _decorated_from_module("pkg_two.mod")

        self.assertNotEqual(function_dir_name(first), function_dir_name(second))

        first(1)
        second(1)

        self.assertEqual(self.__entries(first), 1)
        self.assertEqual(self.__entries(second), 1)

    def test_directory_name_is_filesystem_safe(self) -> None:
        name = function_dir_name(_decorated_from_module("pkg_one.mod"))

        self.assertNotIn("<", name)
        self.assertNotIn(">", name)
        self.assertLessEqual(len(name), 73)  # 64 char label, dash, 8 char digest

    def test_decorator_preserves_function_metadata(self) -> None:
        self.assertEqual(load_data.__name__, "load_data")
        self.assertEqual(load_data.__doc__, "Add two numbers.")

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_debug_flag_reports_miss_store_and_hit(self, mock_stdout) -> None:
        with patch.dict(os.environ, {"DISK_CACHE_DEBUG": "1"}):
            load_data(1, 2)
            load_data(1, 2)

        output = mock_stdout.getvalue()
        self.assertIn("MISS → computing load_data()", output)
        self.assertIn("ttl=10s", output)
        self.assertIn("HIT → load_data()", output)

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_debug_flag_reports_bypasses(self, mock_stdout) -> None:
        with patch.dict(os.environ, {"DISK_CACHE_DEBUG": "1", "DISK_CACHE_DISABLED": "1"}):
            load_data(1, 2)

        with patch.dict(os.environ, {"DISK_CACHE_DEBUG": "1"}):
            load_data_no_cache(1)

        output = mock_stdout.getvalue()
        self.assertIn("DISABLED → running load_data() without caching", output)
        self.assertIn("TTL=0 → bypassing cache for load_data_no_cache()", output)

    def test_cleanup_is_silent_when_cache_dir_is_missing(self) -> None:
        shutil.rmtree(self.cache_dir)

        disk_cache_cleanup()

        self.assertFalse(self.cache_dir.exists())

    def test_cleanup_ignores_files_next_to_namespaces(self) -> None:
        stray = self.cache_dir / "stray.txt"
        stray.write_text("keep me")

        disk_cache_cleanup()

        self.assertTrue(stray.is_file())

    def test_cleanup_removes_data_file_without_meta(self) -> None:
        load_data(1, 2)
        meta_path = next(self.__fn_dir(load_data).glob("*.meta"))
        meta_path.unlink()

        disk_cache_cleanup(orphan_grace_seconds=0)

        self.assertEqual(self.__entries(load_data), 0)

    def test_cleanup_removes_meta_file_without_data(self) -> None:
        load_data(1, 2)
        fn_dir = self.__fn_dir(load_data)
        next(fn_dir.glob("*.pkl")).unlink()

        disk_cache_cleanup(orphan_grace_seconds=0)

        self.assertEqual(len(list(fn_dir.glob("*.meta"))), 0)

    def test_cleanup_keeps_a_fresh_orphan_within_the_grace_period(self) -> None:
        load_data(1, 2)
        fn_dir = self.__fn_dir(load_data)
        next(fn_dir.glob("*.meta")).unlink()

        # A writer that just created the data file has not written meta yet.
        disk_cache_cleanup()

        self.assertEqual(self.__entries(load_data), 1)

    def test_cleanup_removes_an_orphan_older_than_the_grace_period(self) -> None:
        load_data(1, 2)
        fn_dir = self.__fn_dir(load_data)
        next(fn_dir.glob("*.meta")).unlink()

        data_path = next(fn_dir.glob("*.pkl"))
        stale = time.time() - 120
        os.utime(data_path, (stale, stale))

        disk_cache_cleanup()

        self.assertEqual(self.__entries(load_data), 0)

    def test_cleanup_keeps_complete_entries_while_removing_orphans(self) -> None:
        load_data(1, 2)
        load_data(2, 3)

        fn_dir = self.__fn_dir(load_data)
        surviving_key = sorted(p.stem for p in fn_dir.glob("*.pkl"))[0]
        (fn_dir / f"{surviving_key}.pkl").with_name("orphan.pkl").write_bytes(b"x")

        disk_cache_cleanup(orphan_grace_seconds=0)

        self.assertEqual(self.__entries(load_data), 2)
        self.assertFalse((fn_dir / "orphan.pkl").exists())

    def test_cleanup_treats_an_unreadable_timestamp_as_stale(self) -> None:
        load_data(1, 2)
        next(self.__fn_dir(load_data).glob("*.meta")).unlink()

        with patch("src.mysiar.disk_cache_data.os.path.getmtime", side_effect=OSError("gone")):
            disk_cache_cleanup()

        self.assertEqual(self.__entries(load_data), 0)

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_debug_flag_reports_orphans(self, mock_stdout) -> None:
        load_data(1, 2)
        fn_dir = self.__fn_dir(load_data)
        next(fn_dir.glob("*.meta")).unlink()
        (fn_dir / "lonely.meta").write_bytes(pickle.dumps(time.time() + 3600))

        with patch.dict(os.environ, {"DISK_CACHE_DEBUG": "1"}):
            disk_cache_cleanup(orphan_grace_seconds=0)

        output = mock_stdout.getvalue()
        self.assertIn("CLEANUP: ORPHAN pkl →", output)
        self.assertIn("CLEANUP: ORPHAN meta → lonely", output)

    def test_cleanup_ignores_files_inside_a_namespace(self) -> None:
        self.ns_dir.mkdir(parents=True, exist_ok=True)
        stray = self.ns_dir / "notes.txt"
        stray.write_text("keep me")

        disk_cache_cleanup()

        self.assertTrue(stray.is_file())

    def test_invalid_ttl_raises_on_first_call(self) -> None:
        @disk_cache_data(ttl="not-a-ttl")
        def broken():
            return 1

        with self.assertRaises(ValueError):
            broken()

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_failing_write_does_not_break_the_call(self, mock_stdout) -> None:
        real_open = open

        def failing_open(path, mode="r", *args, **kwargs):
            if "w" in mode:
                raise OSError("no space left on device")
            return real_open(path, mode, *args, **kwargs)

        with patch.dict(os.environ, {"DISK_CACHE_DEBUG": "1"}), patch("builtins.open", failing_open):
            self.assertEqual(load_data(1, 2), 3)

        self.assertEqual(self.__entries(load_data), 0)
        self.assertIn("ERROR storing key=", mock_stdout.getvalue())

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_clear_survives_a_failing_rename(self, mock_stdout) -> None:
        load_data(1, 2)

        with patch("src.mysiar.disk_cache_data.os.rename", side_effect=OSError("busy")):
            load_data.clear()

        # Nothing removed, but no exception reached the caller.
        self.assertEqual(self.__entries(load_data), 1)

    def test_safe_delete_swallows_permission_errors(self) -> None:
        target = self.cache_dir / "locked.pkl"
        target.write_bytes(b"x")

        with patch("src.mysiar.disk_cache_data.os.remove", side_effect=PermissionError("denied")):
            safe_delete(str(target))

        self.assertTrue(target.is_file())

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_debug_flag_reports_expiry_on_read(self, mock_stdout) -> None:
        load_data(1, 2)
        meta_path = next(self.__fn_dir(load_data).glob("*.meta"))
        with meta_path.open("wb") as f:
            pickle.dump(time.time() - 1, f)

        with patch.dict(os.environ, {"DISK_CACHE_DEBUG": "1"}):
            load_data(1, 2)

        self.assertIn("EXPIRED → deleting key=", mock_stdout.getvalue())

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_debug_flag_reports_corruption_on_read(self, mock_stdout) -> None:
        load_data(1, 2)
        next(self.__fn_dir(load_data).glob("*.meta")).write_bytes(b"not-a-pickle")

        with patch.dict(os.environ, {"DISK_CACHE_DEBUG": "1"}):
            load_data(1, 2)

        self.assertIn("CORRUPT → deleting key=", mock_stdout.getvalue())

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_debug_flag_reports_clearing(self, mock_stdout) -> None:
        load_data(1, 2)
        load_data(2, 3)

        with patch.dict(os.environ, {"DISK_CACHE_DEBUG": "1"}):
            load_data.clear(1, 2)
            load_data.clear()
            load_data.clear_all_namespaces()

        output = mock_stdout.getvalue()
        self.assertIn("CLEAR → load_data() key=", output)
        self.assertIn("CLEAR → load_data() removed 2 files", output)
        self.assertIn("CLEAR ALL → load_data() removed 0 files", output)

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_debug_flag_reports_cleanup_actions(self, mock_stdout) -> None:
        # Expired entry of a real function.
        load_data(1, 2)
        meta_path = next(self.__fn_dir(load_data).glob("*.meta"))
        with meta_path.open("wb") as f:
            pickle.dump(time.time() - 1, f)

        # Corrupt entry.
        corrupt_dir = self.ns_dir / "corrupt-00000000"
        corrupt_dir.mkdir(parents=True)
        (corrupt_dir / "key.meta").write_bytes(b"not-a-pickle")
        (corrupt_dir / "key.pkl").write_bytes(b"x")

        # Leftover of an interrupted clear().
        leftover = self.ns_dir / "leftover-00000000.trash-1-2"
        leftover.mkdir(parents=True)

        with patch.dict(os.environ, {"DISK_CACHE_DEBUG": "1"}):
            disk_cache_cleanup()

        output = mock_stdout.getvalue()
        self.assertIn("CLEANUP: EXPIRED →", output)
        self.assertIn("CLEANUP: CORRUPT meta →", output)
        self.assertIn("CLEANUP: LEFTOVER →", output)
        self.assertIn("CLEANUP: EMPTY DIR →", output)

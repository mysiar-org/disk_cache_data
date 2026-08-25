import io
import os
import pickle
import shutil
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.mysiar.disk_cache_data import disk_cache_cleanup, disk_cache_data, function_dir_name


@disk_cache_data(ttl="10s")
def add(a, b):
    print("add-called")
    return a + b


@disk_cache_data(ttl="10s")
def multiply(a, b):
    print("multiply-called")
    return a * b


@disk_cache_data(ttl="10s")
def subtract(a, _token):
    print("subtract-called")
    return a - 1


class ClearTestCase(unittest.TestCase):
    """Per function cache clearing tests."""

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

    def __entries(self, func) -> int:
        fn_dir = self.ns_dir / function_dir_name(func)
        if not fn_dir.is_dir():
            return 0
        return len(list(fn_dir.glob("*.pkl")))

    def test_each_function_gets_own_directory(self) -> None:
        add(1, 2)
        multiply(1, 2)

        self.assertEqual(self.__entries(add), 1)
        self.assertEqual(self.__entries(multiply), 1)
        self.assertNotEqual(function_dir_name(add), function_dir_name(multiply))
        self.assertIn("add-", function_dir_name(add))

    def test_clear_removes_every_entry_of_that_function_only(self) -> None:
        add(1, 2)
        add(3, 4)
        add(5, 6)
        multiply(1, 2)

        self.assertEqual(self.__entries(add), 3)

        add.clear()

        self.assertEqual(self.__entries(add), 0)
        self.assertEqual(self.__entries(multiply), 1)

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_function_recomputes_after_clear(self, mock_stdout) -> None:
        add(1, 2)
        add(1, 2)
        add.clear()
        add(1, 2)
        add(1, 2)

        calls = [line for line in mock_stdout.getvalue().splitlines() if line == "add-called"]
        self.assertEqual(len(calls), 2)

    def test_clear_with_arguments_removes_single_entry(self) -> None:
        add(1, 2)
        add(3, 4)

        add.clear(1, 2)

        self.assertEqual(self.__entries(add), 1)

        # The remaining entry is the one that was not cleared.
        self.assertEqual(add(3, 4), 7)
        self.assertEqual(self.__entries(add), 1)

    def test_clear_ignores_private_args_whatever_the_call_style(self) -> None:
        subtract(5, "token-used-at-call-time")
        self.assertEqual(self.__entries(subtract), 1)

        # A private argument is not part of the key, so clear() finds the entry
        # with any value for it, positionally or by keyword.
        subtract.clear(5, "a-completely-different-token")

        self.assertEqual(self.__entries(subtract), 0)

    def test_clear_matches_an_entry_cached_with_the_other_call_style(self) -> None:
        add(a=1, b=2)
        self.assertEqual(self.__entries(add), 1)

        add.clear(1, 2)

        self.assertEqual(self.__entries(add), 0)

    def test_clear_with_kwargs_ignores_keyword_order(self) -> None:
        add(a=1, b=2)
        self.assertEqual(self.__entries(add), 1)

        add.clear(b=2, a=1)

        self.assertEqual(self.__entries(add), 0)

    def test_clear_is_noop_when_nothing_cached(self) -> None:
        add.clear()
        add.clear(1, 2)

        self.assertEqual(self.__entries(add), 0)

    def test_clear_leaves_no_leftover_directory(self) -> None:
        add(1, 2)
        add.clear()

        leftovers = [p.name for p in self.ns_dir.iterdir() if ".trash-" in p.name]
        self.assertEqual(leftovers, [])

    def test_clear_only_touches_current_namespace(self) -> None:
        add(1, 2)

        with patch.dict(os.environ, {"DISK_CACHE_NAMESPACE": "other"}):
            add(1, 2)
            add.clear()
            self.assertFalse((self.cache_dir / "other" / function_dir_name(add)).exists())

        self.assertEqual(self.__entries(add), 1)

    def test_clear_all_namespaces(self) -> None:
        add(1, 2)

        with patch.dict(os.environ, {"DISK_CACHE_NAMESPACE": "other"}):
            add(1, 2)

        self.assertTrue((self.cache_dir / "other" / function_dir_name(add)).exists())

        add.clear_all_namespaces()

        self.assertEqual(self.__entries(add), 0)
        self.assertFalse((self.cache_dir / "other" / function_dir_name(add)).exists())

    def test_cache_dir_helper_points_at_function_directory(self) -> None:
        add(1, 2)

        self.assertEqual(Path(add.cache_dir()), self.ns_dir / function_dir_name(add))
        self.assertEqual(
            Path(add.cache_dir("other")),
            self.cache_dir / "other" / function_dir_name(add),
        )

    def test_cleanup_removes_expired_entries_in_function_directories(self) -> None:
        add(1, 2)
        fn_dir = self.ns_dir / function_dir_name(add)

        meta_path = next(fn_dir.glob("*.meta"))
        with meta_path.open("wb") as f:
            pickle.dump(time.time() - 1, f)

        disk_cache_cleanup()

        self.assertEqual(self.__entries(add), 0)
        # Emptied function directory is pruned.
        self.assertFalse(fn_dir.exists())

    def test_cleanup_removes_corrupt_entries_in_function_directories(self) -> None:
        add(1, 2)
        fn_dir = self.ns_dir / function_dir_name(add)

        meta_path = next(fn_dir.glob("*.meta"))
        meta_path.write_bytes(b"not-a-pickle")

        disk_cache_cleanup()

        self.assertEqual(self.__entries(add), 0)

    def test_cleanup_keeps_live_entries(self) -> None:
        add(1, 2)

        disk_cache_cleanup()

        self.assertEqual(self.__entries(add), 1)

    def test_cleanup_removes_leftover_trash_directory(self) -> None:
        leftover = self.ns_dir / f"{function_dir_name(add)}.trash-123-456"
        leftover.mkdir(parents=True)
        (leftover / "stale.pkl").write_bytes(b"x")

        disk_cache_cleanup()

        self.assertFalse(leftover.exists())


if __name__ == "__main__":
    unittest.main()

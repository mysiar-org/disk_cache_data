import io
import os
import pickle
import shutil
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.mysiar.disk_cache_data import disk_cache_cleanup, disk_cache_data, function_dir_name


@disk_cache_data(ttl=None)
def load_forever(a):
    print("forever-called")
    return a * 10


@disk_cache_data()
def load_default(a):
    print("default-called")
    return a * 100


@disk_cache_data(ttl="10s")
def load_expiring(a):
    print("expiring-called")
    return a * 1000


class TtlNeverTestCase(unittest.TestCase):
    """ttl=None keeps entries for ever, as st.cache_data does."""

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

    def __meta_value(self, func):
        meta_path = next((self.ns_dir / function_dir_name(func)).glob("*.meta"))
        with meta_path.open("rb") as f:
            return pickle.load(f)

    def __entries(self, func) -> int:
        fn_dir = self.ns_dir / function_dir_name(func)
        if not fn_dir.is_dir():
            return 0
        return len(list(fn_dir.glob("*.pkl")))

    def test_ttl_none_stores_no_expiry(self) -> None:
        load_forever(2)

        self.assertIsNone(self.__meta_value(load_forever))

    def test_ttl_omitted_stores_no_expiry(self) -> None:
        load_default(2)

        self.assertIsNone(self.__meta_value(load_default))

    def test_ttl_number_still_stores_expiry(self) -> None:
        start = time.time()
        load_expiring(2)

        expires_at = self.__meta_value(load_expiring)

        self.assertIsNotNone(expires_at)
        self.assertTrue(start + 9 <= expires_at <= start + 11)

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_ttl_none_entry_is_reused(self, mock_stdout) -> None:
        self.assertEqual(load_forever(2), 20)
        self.assertEqual(load_forever(2), 20)
        self.assertEqual(load_forever(2), 20)

        calls = [line for line in mock_stdout.getvalue().splitlines() if line == "forever-called"]
        self.assertEqual(len(calls), 1)

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_ttl_none_entry_survives_a_far_future_clock(self, mock_stdout) -> None:
        load_forever(2)

        ten_years = time.time() + 10 * 365 * 86400
        with patch("src.mysiar.disk_cache_data.time.time", return_value=ten_years):
            self.assertEqual(load_forever(2), 20)

        calls = [line for line in mock_stdout.getvalue().splitlines() if line == "forever-called"]
        self.assertEqual(len(calls), 1)

    def test_cleanup_keeps_ttl_none_entries(self) -> None:
        load_forever(2)
        load_expiring(2)

        # Force the expiring entry past its deadline.
        meta_path = next((self.ns_dir / function_dir_name(load_expiring)).glob("*.meta"))
        with meta_path.open("wb") as f:
            pickle.dump(time.time() - 1, f)

        disk_cache_cleanup()

        self.assertEqual(self.__entries(load_forever), 1)
        self.assertEqual(self.__entries(load_expiring), 0)

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_debug_flag_reports_ttl_never(self, mock_stdout) -> None:
        with patch.dict(os.environ, {"DISK_CACHE_DEBUG": "1"}):
            load_forever(2)

        self.assertIn("ttl=never", mock_stdout.getvalue())

    def test_ttl_none_entry_can_still_be_cleared(self) -> None:
        load_forever(2)
        self.assertEqual(self.__entries(load_forever), 1)

        load_forever.clear()

        self.assertEqual(self.__entries(load_forever), 0)


if __name__ == "__main__":
    unittest.main()

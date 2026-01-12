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

from src.mysiar.disk_cache_data import disk_cache_cleanup, disk_cache_data


@disk_cache_data(ttl="10s")
def load_data(a, b):
    print("function")
    return a + b


@disk_cache_data(ttl="12h")
def load_data_pd(cache="long-live"):
    print("function-pd")
    return pd.DataFrame({"data": [1, 2, 3]})


class DiskCacheDataTestCase(unittest.TestCase):
    """Disk cache data decorator tests."""

    def setUp(self) -> None:
        path = os.getenv("DISK_CACHE_DIR", None)
        if path is None:
            raise RuntimeError("DISK_CACHE_DIR environment variable is not set")

        p = Path(path)
        if p.exists():
            shutil.rmtree(p)

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

        # Find first .pkl file
        for file in ns_path.glob(f"*.{ext}"):
            return file

        raise FileNotFoundError(f"No .{ext} files found in namespace: {ns_path}")

    @staticmethod
    def __load_pkl_value(pkl_path: Path):
        if not pkl_path.is_file():
            raise FileNotFoundError(f"Pickle file does not exist: {pkl_path}")

        with pkl_path.open("rb") as f:
            data = pickle.load(f)

        return data

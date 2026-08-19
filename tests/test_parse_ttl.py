import unittest

from mysiar.disk_cache_data import parse_ttl


class ParseTtlTestCase(unittest.TestCase):
    """Parse ttl tests."""

    def test(self) -> None:

        self.assertEqual(parse_ttl(10), 10)
        # None means never expires, as in st.cache_data
        self.assertIsNone(parse_ttl(None))
        self.assertEqual(parse_ttl("15"), 15)
        self.assertEqual(parse_ttl("5m"), 300)
        self.assertEqual(parse_ttl("2h"), 7200)
        self.assertEqual(parse_ttl("1d"), 86400)
        self.assertEqual(parse_ttl("1d 4h"), 100800)

        self.assertEqual(parse_ttl(0), 0)
        self.assertEqual(parse_ttl(1.9), 1)
        self.assertEqual(parse_ttl("  2H  "), 7200)
        self.assertEqual(parse_ttl("30S"), 30)
        self.assertEqual(parse_ttl("1d4h"), 100800)

        with self.assertRaises(ValueError):
            parse_ttl("invalid")

        with self.assertRaises(ValueError):
            parse_ttl("")

import unittest
import weakref
from collections import OrderedDict

from scripts.x431_re.gag_lib import GgpLanguage


class LanguageFixture(GgpLanguage):
    def __init__(self):
        self._lookup_cache = OrderedDict()
        self.lookups = 0

    def _lookup_id_uncached(self, gag_name, id_u32):
        self.lookups += 1
        return None if id_u32 == 0 else {'id': id_u32}


class LanguageCacheTests(unittest.TestCase):
    def test_reader_and_cached_values_are_released_without_global_retention(self):
        first = LanguageFixture()
        second = LanguageFixture()
        self.assertEqual(first.lookup_id('EXPRESS.GAG', 1), {'id': 1})
        self.assertEqual(first.lookup_id('EXPRESS.GAG', 1), {'id': 1})
        self.assertEqual(first.lookups, 1)
        second.lookup_id('EXPRESS.GAG', 1)
        self.assertEqual(second.lookups, 1)
        observed = weakref.ref(first)
        del first
        self.assertIsNone(observed())
        self.assertEqual(second.lookup_id('EXPRESS.GAG', 1), {'id': 1})

    def test_missing_results_are_cached_and_capacity_evicts_least_recent_lookup(self):
        reader = LanguageFixture()
        reader.lookup_id('EXPRESS.GAG', 0)
        reader.lookup_id('EXPRESS.GAG', 0)
        self.assertEqual(reader.lookups, 1)
        for key in range(1, 200001):
            reader.lookup_id('EXPRESS.GAG', key)
        self.assertEqual(len(reader._lookup_cache), 200000)
        reader.lookup_id('EXPRESS.GAG', 1)
        reader.lookup_id('EXPRESS.GAG', 200001)
        self.assertIn(('EXPRESS.GAG', 1), reader._lookup_cache)
        self.assertNotIn(('EXPRESS.GAG', 2), reader._lookup_cache)
        reader.lookup_id('EXPRESS.GAG', 0)
        self.assertIsNone(reader._lookup_cache[('EXPRESS.GAG', 0)])


if __name__ == '__main__':
    unittest.main()

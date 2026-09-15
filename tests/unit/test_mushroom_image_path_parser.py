
import unittest
from datetime import datetime
from src.vision.mushroom_image_processor import MushroomImagePathParser

class TestDateNormalization(unittest.TestCase):
    def setUp(self):
        self.parser = MushroomImagePathParser()

    def test_standard_8digit(self):
        # 20251218 -> 20251218
        # Collection time: Dec 24, 2025. Diff: 6 days. OK.
        col_date = "20251218"
        det_time = "20251224160000"
        normalized = self.parser._normalize_collection_date(col_date, det_time)
        self.assertEqual(normalized, "20251218")

    def test_7digit_resolution_dec4(self):
        # 2025124 -> 20251204 (Dec 4) if ref is Dec 24
        col_date = "2025124"
        det_time = "20251224160000" # Dec 24
        # Jan 24 (20250124) is too far in past.
        # Dec 4 (20251204) is 20 days ago. OK.
        normalized = self.parser._normalize_collection_date(col_date, det_time)
        self.assertEqual(normalized, "20251204")

    def test_7digit_resolution_jan24(self):
        # 2025124 -> 20250124 (Jan 24) if ref is Feb 10
        col_date = "2025124"
        det_time = "20250210160000" # Feb 10
        # Jan 24 (20250124) is 17 days ago. OK.
        # Dec 4 (20251204) is in future. FAIL.
        normalized = self.parser._normalize_collection_date(col_date, det_time)
        self.assertEqual(normalized, "20250124")

    def test_out_of_range_error(self):
        # Date is too old
        col_date = "20250101"
        det_time = "20251224160000" # Dec 24.
        # Jan 1 is way more than 30 days.
        with self.assertRaises(ValueError):
           self.parser._normalize_collection_date(col_date, det_time)

    def test_future_date_error(self):
        # Date is in future
        col_date = "20251230"
        det_time = "20251224160000" # Dec 24
        with self.assertRaises(ValueError):
           self.parser._normalize_collection_date(col_date, det_time)

if __name__ == '__main__':
    unittest.main()

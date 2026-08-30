import unittest
from datetime import date, timedelta

from keywords import count_keywords, iter_daily_keywords


def _identity(entry) -> tuple[str, str, str]:
    return entry.category_large, entry.category_small, entry.keyword


class ElevenKeywordRotationTests(unittest.TestCase):
    def test_next_day_moves_by_expected_daily_keyword_count(self):
        first_day = list(
            iter_daily_keywords(
                date(2026, 1, 1),
                expected_keywords_per_day=20,
            )
        )
        next_day = list(
            iter_daily_keywords(
                date(2026, 1, 2),
                expected_keywords_per_day=20,
            )
        )

        self.assertEqual(len(first_day), count_keywords())
        self.assertEqual(next_day, first_day[20:] + first_day[:20])

    def test_first_round_interleaves_all_large_categories(self):
        keywords = list(
            iter_daily_keywords(
                date(2026, 1, 1),
                expected_keywords_per_day=20,
            )
        )

        first_round_categories = {
            entry.category_large for entry in keywords[:8]
        }
        self.assertEqual(len(first_round_categories), 8)

    def test_ten_daily_windows_cover_every_keyword(self):
        start = date(2026, 1, 1)
        covered = {
            _identity(entry)
            for day in range(10)
            for entry in list(
                iter_daily_keywords(
                    start + timedelta(days=day),
                    expected_keywords_per_day=20,
                )
            )[:20]
        }
        all_keywords = {
            _identity(entry)
            for entry in iter_daily_keywords(
                start,
                expected_keywords_per_day=20,
            )
        }

        self.assertEqual(covered, all_keywords)


if __name__ == "__main__":
    unittest.main()

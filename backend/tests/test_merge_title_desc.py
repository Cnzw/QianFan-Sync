import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from impl._utils import merge_title_into_desc


class MergeTitleIntoDescTests(unittest.TestCase):
    def test_both_empty(self):
        self.assertEqual(merge_title_into_desc("", ""), "")

    def test_title_only(self):
        self.assertEqual(merge_title_into_desc("展位4046", ""), "展位4046")

    def test_desc_only(self):
        self.assertEqual(merge_title_into_desc("", "现场实拍"), "现场实拍")

    def test_title_and_desc_joined_by_single_newline(self):
        self.assertEqual(
            merge_title_into_desc("展位4046", "现场实拍"), "展位4046\n现场实拍"
        )

    def test_surrounding_whitespace_is_stripped(self):
        self.assertEqual(
            merge_title_into_desc("  展位4046 ", " 现场实拍\n"), "展位4046\n现场实拍"
        )

    def test_none_values(self):
        self.assertEqual(merge_title_into_desc(None, None), "")

    def test_limit_truncates(self):
        self.assertEqual(merge_title_into_desc("标题", "描述", limit=5), "标题\n描述"[:5])

    def test_limit_not_reached(self):
        self.assertEqual(merge_title_into_desc("标题", "描述", limit=100), "标题\n描述")


if __name__ == "__main__":
    unittest.main()

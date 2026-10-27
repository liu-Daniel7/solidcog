import unittest

from app.services.translation_guard import protect, protected_tokens, restore, validate


class TranslationGuardTests(unittest.TestCase):
    def test_engineering_tokens_round_trip(self):
        source = "内角R1.0，尺寸15.4mm，材料06Cr19Ni10，按GB/T 1804-m，⌀25 ±0.02 mm。"
        masked, mapping = protect(source)
        self.assertNotIn("R1.0", masked)
        self.assertNotIn("GB/T 1804-m", masked)
        self.assertNotIn("06Cr19Ni10", masked)
        restored, missing = restore(masked, mapping)
        self.assertEqual(restored, source)
        self.assertEqual(missing, [])
        self.assertEqual(validate(source, restored), [])

    def test_missing_placeholder_is_reported(self):
        _, mapping = protect("尺寸15.4mm")
        restored, missing = restore("Size SCGPH9999", mapping)
        self.assertEqual(restored, "Size SCGPH9999")
        self.assertEqual(missing, ["15.4mm"])

    def test_unit_and_standard_substitution_are_flagged(self):
        source = "未注公差按 GB/T 1804-m，长度 25 mm。"
        issues = validate(source, "General tolerance per ASTM A36, length 1 in.")
        self.assertTrue(any("工程数据" in issue for issue in issues))
        self.assertTrue(any("毫米" in issue for issue in issues))
        self.assertTrue(any("标准" in issue for issue in issues))

    def test_tokens_adjacent_to_chinese_are_found(self):
        tokens = protected_tokens("材料06Cr19Ni10，比例1:5，图幅A3，内角R1.0")
        self.assertIn("06Cr19Ni10", tokens)
        self.assertIn("1:5", tokens)
        self.assertIn("A3", tokens)
        self.assertIn("R1.0", tokens)
        self.assertIn("241～286HBW", protected_tokens("硬度241～286HBW"))
        self.assertIn("GB/T 3480.5—2008", protected_tokens("按GB/T 3480.5—2008执行"))


if __name__ == "__main__":
    unittest.main()

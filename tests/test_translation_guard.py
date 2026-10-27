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

    def test_extended_material_hardness_mass_and_tolerance_tokens(self):
        cases = {
            "硬度250HBW": "250HBW",
            "材料35CrMo": "35CrMo",
            "材料2A12": "2A12",
            "材料H62": "H62",
            "质量0.18kg": "0.18kg",
            "角度25°": "25°",
            "浓度5%": "5%",
            "表面0.8μm": "0.8μm",
            "孔径φ25 +0.021": "φ25 +0.021",
            "尺寸25 ±0.02 mm": "25 ±0.02 mm",
        }
        for source, token in cases.items():
            with self.subTest(source=source):
                self.assertIn(token, protected_tokens(source))

    def test_hardness_and_mass_substitution_are_flagged(self):
        self.assertTrue(any("工程数据" in issue for issue in validate("硬度250HBW", "Hardness 300 HBW")))
        self.assertTrue(any("工程数据" in issue for issue in validate("质量0.18kg", "Mass 0.4 lb")))
        self.assertTrue(any("工程数据" in issue for issue in validate("尺寸25 ±0.02 mm", "Dimension 35 ±0.02 mm")))
        self.assertTrue(any("千克" in issue for issue in validate("质量0.18kg", "Mass 0.18 kg to 0.4 lb")))

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

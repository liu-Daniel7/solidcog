import unittest

from app.services.kit_rules import (
    DrawingFile,
    classify_note,
    clean_text,
    is_valid_code,
    match_files,
    normalize_code,
    split_filename,
)


def file(drawing_id, name):
    return DrawingFile(drawing_id=drawing_id, original_filename=name, stored_filename=f"stored-{drawing_id}")


class FilenameMatchingTests(unittest.TestCase):
    def test_code_only_matches(self):
        result = match_files("SK.CNC.0003", "外框", [file(1, "SK.CNC.0003.pdf")])
        self.assertEqual(result["status"], "exists")
        self.assertEqual(result["matches"][0]["match_form"], "code")

    def test_code_name_separators_match(self):
        files = [
            file(1, "SK.CNC.0003-外框.pdf"),
            file(2, "SK.CNC.0003_外框.pdf"),
            file(3, "SK.CNC.0003 外框.pdf"),
        ]
        result = match_files("SK.CNC.0003", "外框", files)
        self.assertEqual(result["status"], "exists")
        self.assertEqual({m["match_form"] for m in result["matches"]}, {"code-name", "code_name", "code name"})

    def test_dots_leading_zeroes_and_extension_case(self):
        result = match_files("sk.cnc.0003", "外框", [file(1, "SK.CNC.0003-外框.PDF")])
        self.assertEqual(result["status"], "exists")
        self.assertEqual(result["matches"][0]["drawing_id"], 1)

    def test_boundary_punctuation_is_normalized_consistently(self):
        result = match_files("（SK.CNC.0003）", "外框", [file(1, "SK.CNC.0003-外框.pdf")])
        self.assertEqual(result["status"], "exists")
        self.assertEqual(normalize_code("SK.CNC.0003."), "SK.CNC.0003")
        self.assertTrue(is_valid_code("（SK.CNC.0003）"))

    def test_boundary_not_substring(self):
        result = match_files("SK.CNC.0003", "外框", [file(1, "SK.CNC.00030-外框.pdf")])
        self.assertEqual(result["status"], "missing")

    def test_name_only_is_not_a_match(self):
        result = match_files("SK.CNC.0003", "外框", [file(1, "外框.pdf")])
        self.assertEqual(result["status"], "missing")

    def test_same_code_different_name_is_candidate_not_exists(self):
        result = match_files("SK.CNC.0003", "外框", [file(1, "SK.CNC.0003-内框.pdf")])
        self.assertEqual(result["status"], "candidate")
        self.assertEqual(len(result["candidates"]), 1)

    def test_multiple_matching_files_are_all_retained(self):
        result = match_files("SK.CNC.0003", "外框", [
            file(1, "SK.CNC.0003-外框.pdf"),
            file(2, "SK.CNC.0003-外框.PDF"),
            file(3, "SK.CNC.0003.pdf"),
        ])
        self.assertEqual(result["status"], "exists")
        self.assertEqual(len(result["matches"]), 3)

    def test_model_files_are_ignored_and_do_not_satisfy_a_row(self):
        result = match_files("SK.CNC.0003", "外框", [
            file(1, "SK.CNC.0003-外框.SLDPRT"),
            file(2, "SK.CNC.0003-外框.SLDASM"),
        ])
        self.assertEqual(result["status"], "missing")
        self.assertTrue(all("模型" in item["reason"] for item in result["ignored"]))

    def test_temp_files_are_ignored(self):
        result = match_files("SK.CNC.0001", "SK数控铣床", [file(1, "~$SK.CNC.0001-SK数控铣床.SLDASM")])
        self.assertEqual(result["status"], "missing")
        self.assertIn("临时文件", result["ignored"][0]["reason"])

    def test_full_width_and_extra_spaces(self):
        result = match_files("ＳＫ．ＣＮＣ．０００３", "　外框　", [file(1, "SK.CNC.0003-外框.pdf")])
        self.assertEqual(result["status"], "exists")

    def test_internal_hyphen_in_name_is_preserved(self):
        result = match_files("ABC-01", "左-右支架", [file(1, "ABC-01-左-右支架.pdf")])
        self.assertEqual(result["status"], "exists")

    def test_duplicate_code_does_not_collapse_to_first_file_silently(self):
        result = match_files("SK.CNC.0003", "外框", [
            file(9, "SK.CNC.0003-外框.pdf"),
            file(3, "SK.CNC.0003-外框.pdf"),
        ])
        self.assertEqual([item["drawing_id"] for item in result["matches"]], [3, 9])

    def test_empty_code_is_unchecked(self):
        result = match_files("", "外框", [file(1, "SK.CNC.0003-外框.pdf")])
        self.assertEqual(result["status"], "unchecked")

    def test_split_filename_keeps_dots_and_leading_zeroes(self):
        self.assertEqual(split_filename("SK.CNC.0003-外框.PDF"), ("SK.CNC.0003-外框", ".pdf"))
        self.assertEqual(normalize_code(" sk.cnc.0003 "), "SK.CNC.0003")
        self.assertEqual(clean_text("  Ａ  "), "A")


class NoteClassificationTests(unittest.TestCase):
    def test_exempt_values(self):
        for note in ("标准件", "外购件", "无图"):
            result = classify_note(note)
            self.assertTrue(result["exempt"], note)
            self.assertFalse(result["review"], note)

    def test_label_prefix_and_wrappers(self):
        self.assertTrue(classify_note("备注：标准件")["exempt"])
        self.assertTrue(classify_note("【外购件】")["exempt"])
        self.assertTrue(classify_note("（无图）")["exempt"])
        self.assertTrue(classify_note("【备注：标准件】")["exempt"])

    def test_multiple_explicit_tags(self):
        result = classify_note("标准件、外购件")
        self.assertTrue(result["exempt"])
        self.assertIn("标准件", result["reason"])
        self.assertTrue(classify_note("标准件 外购件")["exempt"])

    def test_non_standard_is_not_exempt_and_needs_review(self):
        result = classify_note("非标准件")
        self.assertFalse(result["exempt"])
        self.assertTrue(result["review"])

    def test_compound_note_is_not_exempt(self):
        result = classify_note("标准件；表面处理")
        self.assertFalse(result["exempt"])
        self.assertTrue(result["review"])

    def test_empty_note_is_checked(self):
        result = classify_note("")
        self.assertFalse(result["exempt"])
        self.assertFalse(result["review"])

    def test_ordinary_note_is_checked_without_noise(self):
        result = classify_note("表面发黑")
        self.assertFalse(result["exempt"])
        self.assertFalse(result["review"])


if __name__ == "__main__":
    unittest.main()

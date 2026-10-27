import unittest

from app.services.segments import build_segments, source_hash


class TranslationSegmentTests(unittest.TestCase):
    def test_title_fields_are_split_and_header_rows_skipped(self):
        title = """版次REV. | 状态STATUS | 说明DESCRIPTION | 编制PREPARED | 校对CHECKED | 审核APPROVED | 批准AUTHORIZED | 日期DATE
安徽某机械集团 | 图名DRAWING NAME | 面板8
项目PROJECT | 某核电项目 | 图号DRAWING NO. | BJ-SG2-1-09A-12
质量/kgMASS | 0.18 | 材料MATERIAL | 06Cr19Ni10 | 比例SCALE | 1:5 | 图幅SIZE | A3 | 第1页/共1页SHEET: OF 1
A | CFC | 初版 |"""
        segments = build_segments(title_text=title)
        labels = {segment["label"]: segment["source_text"] for segment in segments}
        self.assertEqual(labels["图名"], "面板8")
        self.assertEqual(labels["图号"], "BJ-SG2-1-09A-12")
        self.assertEqual(labels["材料"], "06Cr19Ni10")
        self.assertEqual(labels["图幅"], "A3")
        self.assertEqual(labels["页码"], "第1页/共1页")
        self.assertEqual(labels["版次记录"], "A | CFC | 初版")
        self.assertNotIn("状态STATUS", labels)
        self.assertFalse(next(item for item in segments if item["label"] == "图号")["translatable"])
        self.assertFalse(next(item for item in segments if item["label"] == "页码")["translatable"])

    def test_qwen_plain_and_colon_title_rows_are_parsed(self):
        for title in ("图名：面板8\n图号：LJT01.01", "图名 面板8\n图号 LJT01.01", "设计 | 张三\n图名 | 面板8"):
            with self.subTest(title=title):
                segments = build_segments(title_text=title)
                labels = {segment["label"]: segment["source_text"] for segment in segments}
                self.assertTrue(any(label in labels for label in ("图名", "设计")))
        segments = build_segments("设计 | 张三\n图名 | 面板8")
        labels = {segment["label"]: segment["source_text"] for segment in segments}
        self.assertEqual(labels["设计"], "张三")
        mixed = build_segments("图名：面板8 | 图号：LJT01.01")
        mixed_labels = {segment["label"]: segment["source_text"] for segment in mixed}
        self.assertEqual(mixed_labels["图名"], "面板8")
        self.assertEqual(mixed_labels["图号"], "LJT01.01")

    def test_all_text_only_fallback_recovers_title_and_requirements(self):
        all_text = "技术要求\n1. 去毛刺\n2. 热处理\n图名 | 面板8\n折弯图"
        segments = build_segments("", "", all_text)
        self.assertEqual(
            [(segment["section"], segment["label"], segment["source_text"]) for segment in segments],
            [
                ("title", "图名", "面板8"),
                ("tech", "第 1 条", "去毛刺"),
                ("tech", "第 2 条", "热处理"),
                ("extra", "全文补充", "折弯图"),
            ],
        )

    def test_all_text_fallback_rejects_parameter_table_fields(self):
        all_text = """齿廓 | 渐开线 | 齿数 | z | 29
检测项目(组) | 代号 | 允许值
配对齿轮 | 图号 |
45 | 合肥工业大学
圆柱直齿轮
标记 | 处数 | 分区 | 更改文件号 | 签名 | 年月日
设计 | (签名) | (年月日) | 标准化 | (签名) | (年月日) | 阶段标记 | 质量 | 比例
制图 | LJT01.01
审核 |
工艺 | 批准 | 共 张 第 张 | # #
技术要求
1. 去毛刺
2. 热处理
"""
        segments = build_segments("", "", all_text)
        labels = {segment["label"]: segment["source_text"] for segment in segments if segment["section"] == "title"}
        self.assertEqual(labels["材料"], "45")
        self.assertEqual(labels["单位"], "合肥工业大学")
        self.assertEqual(labels["图名"], "圆柱直齿轮")
        self.assertEqual(labels["图号"], "LJT01.01")
        self.assertNotIn("配对齿轮", labels.get("图号", ""))
        self.assertNotIn("代号", labels.get("项目", ""))

    def test_technical_notes_keep_numbering_and_merge_continuations(self):
        tech = """技术要求：
1. 去毛刺、去毛边。
   不锈钢表面不得有划痕。
2. 焊接前检查表面质量。
"""
        segments = build_segments(tech_text=tech)
        self.assertEqual([item["label"] for item in segments], ["第 1 条", "第 2 条"])
        self.assertIn("不锈钢表面不得有划痕", segments[0]["source_text"])
        self.assertIn("焊接前检查", segments[1]["source_text"])

    def test_extra_text_removes_title_tech_duplicates_and_keeps_unique_labels(self):
        title = "图名 | 面板8"
        tech = "1. 去毛刺，锐边倒钝，表面不得有影响使用的缺陷。"
        all_text = """技术要求：
1. 去毛刺，锐边倒钝。
图名 | 面板8
质量/kgMASS | 0.18 | 材料MATERIAL | 06Cr19Ni10
折弯图
下料图
三维图
用的缺陷。
"""
        segments = build_segments(title, tech, all_text)
        extras = [item["source_text"] for item in segments if item["section"] == "extra"]
        self.assertEqual(extras, ["折弯图", "下料图", "三维图"])

    def test_atlas_style_vertical_title_and_inline_math_are_normalized(self):
        title = "45 | 合肥工业大学\n圆柱直齿轮\n制图 | LJT01.01"
        tech = "1. 未注倒角为 \\(C2\\)。"
        segments = build_segments(title, tech)
        labels = {segment["label"]: segment["source_text"] for segment in segments}
        self.assertEqual(labels["材料"], "45")
        self.assertEqual(labels["图名"], "圆柱直齿轮")
        self.assertEqual(labels["图号"], "LJT01.01")
        self.assertNotIn("\\(", labels["第 1 条"])
        self.assertIn("C2", labels["第 1 条"])

    def test_alphanumeric_material_grade_is_recognized(self):
        segments = build_segments("Q235A | 合肥工业大学\n法兰盘\n制图 | LJT04.17")
        labels = {segment["label"]: segment["source_text"] for segment in segments}
        self.assertEqual(labels["材料"], "Q235A")
        self.assertEqual(labels["图名"], "法兰盘")
        self.assertEqual(labels["图号"], "LJT04.17")

    def test_source_hash_is_stable_and_source_sensitive(self):
        self.assertEqual(source_hash("去毛刺"), source_hash(" 去毛刺 "))
        self.assertNotEqual(source_hash("去毛刺"), source_hash("去毛边"))

    def test_segments_have_stable_keys_and_unique_ordinals(self):
        segments = build_segments("图名 | 面板8", "1. 去毛刺", "折弯图")
        self.assertEqual([item["segment_key"] for item in segments], ["title:001", "tech:001", "extra:001"])
        self.assertEqual([item["ordinal"] for item in segments], [1, 2, 3])
        self.assertEqual(len({item["segment_key"] for item in segments}), len(segments))


if __name__ == "__main__":
    unittest.main()

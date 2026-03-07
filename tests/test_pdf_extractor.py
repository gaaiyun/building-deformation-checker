import unittest

from src.tools.pdf_extractor import _clean_ocr_markdown


class PdfExtractorTests(unittest.TestCase):
    def test_clean_ocr_markdown_compresses_html_heavy_tables(self):
        markdown = """
<div style='font-size:14px'>监测成果表</div>
<div style='font-size:12px'>第 1 页</div>
<table style='width:100%'>
  <tr>
    <td style='text-align:center;word-wrap:break-word;'>点号</td>
    <td style='text-align:center;word-wrap:break-word;'>本次测值</td>
    <td style='text-align:center;word-wrap:break-word;'>累计变化</td>
  </tr>
  <tr>
    <td>S1</td>
    <td>12.30</td>
    <td>0.50</td>
  </tr>
</table>
<div><img src='x.jpg' /></div>
        """

        clean_text, stats = _clean_ocr_markdown(markdown)

        self.assertIn("监测成果表", clean_text)
        self.assertIn("| 点号 | 本次测值 | 累计变化 |", clean_text)
        self.assertIn("| S1 | 12.30 | 0.50 |", clean_text)
        self.assertLess(len(clean_text), len(markdown))
        self.assertEqual(stats["table_count"], 1)
        self.assertGreater(stats["markup_ratio"], 0.5)

    def test_clean_ocr_markdown_drops_chart_axis_noise(self):
        markdown = """
<div>监测数据成果曲线图</div>
<table>
  <tr><td>10-0-10-20-30-40-50</td><td>2024-03-01</td><td>2024-03-26</td></tr>
</table>
<div>【支护结构顶部水平位移】监测数据成果表</div>
<table>
  <tr><td colspan="3">监测数据成果曲线图</td></tr>
  <tr><td>18-16-14-12-10-8-6-4-2-0</td><td>2024-03-08</td><td>2024-03-26</td></tr>
  <tr><td>![](images/chart.jpg)</td><td></td><td></td></tr>
  <tr><td>测点编号</td><td>累计变化量(mm)</td><td>变化速率(mm/d)</td></tr>
  <tr><td>S7</td><td>13.2</td><td>0.82</td></tr>
</table>
        """

        clean_text, stats = _clean_ocr_markdown(markdown)

        self.assertNotIn("10-0-10-20-30-40-50", clean_text)
        self.assertNotIn("18-16-14-12-10-8-6-4-2-0", clean_text)
        self.assertNotIn("![](images/chart.jpg)", clean_text)
        self.assertIn("【支护结构顶部水平位移】监测数据成果表", clean_text)
        self.assertIn("| 测点编号 | 累计变化量(mm) | 变化速率(mm/d) |", clean_text)
        self.assertIn("| S7 | 13.2 | 0.82 |", clean_text)
        self.assertEqual(stats["table_count"], 1)
        self.assertEqual(stats["dropped_table_count"], 1)


if __name__ == "__main__":
    unittest.main()

"""验证 Step 7/8 超时配置修复 — 不依赖真实 PDF 和 LLM API"""
import unittest
from unittest.mock import MagicMock, patch, call
import time

class TestConfigNoDuplicates(unittest.TestCase):
    """确保 config.py 中没有重复定义导致值被覆盖"""

    def test_self_verify_timeout_is_120(self):
        import src.config as cfg
        self.assertEqual(cfg.SELF_VERIFY_TIMEOUT_SEC, 120,
            f"SELF_VERIFY_TIMEOUT_SEC 应为120，实际为{cfg.SELF_VERIFY_TIMEOUT_SEC}（可能被重复定义覆盖）")

    def test_self_verify_max_total_is_360(self):
        import src.config as cfg
        self.assertEqual(cfg.SELF_VERIFY_MAX_TOTAL_SEC, 360,
            f"SELF_VERIFY_MAX_TOTAL_SEC 应为360，实际为{cfg.SELF_VERIFY_MAX_TOTAL_SEC}")

    def test_final_review_timeout_is_180(self):
        import src.config as cfg
        self.assertEqual(cfg.FINAL_REVIEW_TIMEOUT_SEC, 180,
            f"FINAL_REVIEW_TIMEOUT_SEC 应为180，实际为{cfg.FINAL_REVIEW_TIMEOUT_SEC}")

    def test_config_enrich_timeout_is_120(self):
        import src.config as cfg
        self.assertEqual(cfg.CONFIG_ENRICH_TIMEOUT_SEC, 120,
            f"CONFIG_ENRICH_TIMEOUT_SEC 应为120，实际为{cfg.CONFIG_ENRICH_TIMEOUT_SEC}")

    def test_final_review_max_retries_is_1(self):
        import src.config as cfg
        self.assertEqual(cfg.FINAL_REVIEW_MAX_RETRIES, 1,
            f"FINAL_REVIEW_MAX_RETRIES 应为1，实际为{cfg.FINAL_REVIEW_MAX_RETRIES}")

    def test_self_verify_max_retries_is_1(self):
        import src.config as cfg
        self.assertEqual(cfg.SELF_VERIFY_MAX_RETRIES, 1,
            f"SELF_VERIFY_MAX_RETRIES 应为1，实际为{cfg.SELF_VERIFY_MAX_RETRIES}")


class TestSelfVerifierTimeoutPropagation(unittest.TestCase):
    """验证自验证器正确传递超时值到 LLM 调用"""

    @patch("openai.OpenAI")
    def test_batch_timeout_uses_config_value(self, mock_openai_cls):
        """批次请求应使用 SELF_VERIFY_TIMEOUT_SEC=120"""
        # 需要重新 import 以使 mock 生效
        import importlib
        import src.tools.self_verifier as sv_mod
        importlib.reload(sv_mod)
        from src.tools.self_verifier import _verify_batch_task
        from src.models.data_models import CheckIssue

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = '[{"error_idx":0,"verdict":"confirm","reason":"ok","suspected_origin":"report"}]'
        mock_client.chat.completions.create.return_value = mock_resp

        issue = CheckIssue(
            table_name="测试表", point_id="P1", field_name="累计变化",
            expected_value="1.0", actual_value="2.0",
            message="测试", severity="error"
        )

        result = _verify_batch_task(
            "测试原文",
            [issue],
            timeout_sec=120,
            max_retries=0,
            backoff_sec=5,
            context_chars=200,
        )

        create_call = mock_client.chat.completions.create.call_args
        self.assertEqual(create_call.kwargs.get("timeout"), 120,
            "批次请求超时应为120秒")

    @patch("openai.OpenAI")
    def test_single_retry_timeout_not_capped_at_30(self, mock_openai_cls):
        """单条重试超时不应被限制在30秒"""
        import importlib
        import src.tools.self_verifier as sv_mod
        importlib.reload(sv_mod)
        from src.tools.self_verifier import _verify_batch_task
        from src.models.data_models import CheckIssue

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        call_count = [0]
        def side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] <= 1:
                raise Exception("Request timed out.")
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = '[{"error_idx":0,"verdict":"confirm","reason":"ok","suspected_origin":"report"}]'
            return mock_resp

        mock_client.chat.completions.create.side_effect = side_effect

        issues = [
            CheckIssue(
                table_name="测试表", point_id=f"P{i}", field_name="累计变化",
                expected_value="1.0", actual_value="2.0",
                message="测试", severity="error"
            )
            for i in range(2)
        ]

        result = _verify_batch_task(
            "测试原文",
            issues,
            timeout_sec=120,
            max_retries=0,
            backoff_sec=5,
            context_chars=200,
        )

        calls = mock_client.chat.completions.create.call_args_list
        for c in calls[1:]:
            timeout_val = c.kwargs.get("timeout")
            self.assertGreaterEqual(timeout_val, 120,
                f"单条重试超时应 >= 120秒（与批次相同），实际为{timeout_val}")


class TestFinalReviewTimeoutPropagation(unittest.TestCase):
    """验证最终审核正确传递超时值"""

    @patch("src.tools.llm_parser.call_chat_completion")
    def test_final_review_uses_180s_timeout(self, mock_call):
        """最终审核应使用 FINAL_REVIEW_TIMEOUT_SEC=180"""
        mock_call.return_value = "审核通过，无重大问题。"

        from src.tools.llm_parser import verify_report_with_llm
        result = verify_report_with_llm("# 检查报告\n测试内容", "原始文本")

        self.assertIn("审核通过", result)
        # 验证超时参数
        call_kwargs = mock_call.call_args.kwargs
        self.assertEqual(call_kwargs.get("timeout"), 180,
            f"最终审核超时应为180秒，实际为{call_kwargs.get('timeout')}")


if __name__ == "__main__":
    unittest.main()

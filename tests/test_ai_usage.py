import json
import os
import tempfile
import unittest
from unittest import mock

import ai_usage
import server


class AiUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.session_dir = os.path.join(self.temp_dir.name, "session")
        self.pricing_path = os.path.join(self.temp_dir.name, "pricing.json")
        with open(self.pricing_path, "w", encoding="utf-8") as f:
            json.dump({
                "schemaVersion": 1,
                "version": "test",
                "currency": "USD",
                "tokenUnit": 1_000_000,
                "providers": {
                    "openai": {
                        "models": {
                            "priced-model": {
                                "inputPerMillionTokens": 2.0,
                                "cachedInputPerMillionTokens": 0.2,
                                "outputPerMillionTokens": 10.0,
                            }
                        }
                    }
                },
            }, f)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_extracts_chat_and_cached_token_usage(self):
        usage = ai_usage.extract_response_usage({
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 30,
                "total_tokens": 150,
                "prompt_tokens_details": {"cached_tokens": 20},
            }
        })
        self.assertEqual(usage["inputTokens"], 120)
        self.assertEqual(usage["outputTokens"], 30)
        self.assertEqual(usage["cachedInputTokens"], 20)
        self.assertTrue(usage["tokenUsageAvailable"])

    def test_records_and_aggregates_by_stage_and_model(self):
        response = {"usage": {"input_tokens": 1_000_000, "output_tokens": 100_000}}
        event = ai_usage.record_response_usage(
            self.session_dir,
            stage="final_recap",
            model="priced-model",
            provider="openai",
            response_payload=response,
            pricing_path=self.pricing_path,
            metadata={"selectedNotesOnly": True},
        )
        self.assertEqual(event["estimatedCost"], 3.0)

        summary = ai_usage.summarize_usage(self.session_dir, self.pricing_path)
        recap = next(item for item in summary["stages"] if item["stage"] == "final_recap")
        self.assertEqual(recap["requests"], 1)
        self.assertEqual(recap["inputTokens"], 1_000_000)
        self.assertEqual(recap["outputTokens"], 100_000)
        self.assertEqual(recap["estimatedCost"], 3.0)
        self.assertEqual(recap["models"][0]["model"], "priced-model")

    def test_unknown_price_is_explicitly_unestimated(self):
        ai_usage.record_response_usage(
            self.session_dir,
            stage="transcription",
            model="unknown-model",
            provider="openai",
            response_payload={"duration": 120.0},
            pricing_path=self.pricing_path,
        )
        summary = ai_usage.summarize_usage(self.session_dir, self.pricing_path)
        transcription = next(item for item in summary["stages"] if item["stage"] == "transcription")
        self.assertEqual(transcription["requests"], 1)
        self.assertIsNone(transcription["estimatedCost"])
        self.assertEqual(transcription["unestimatedRequests"], 1)
        self.assertEqual(transcription["audioSeconds"], 120.0)

    def test_total_only_usage_is_not_mispriced_as_zero(self):
        event = ai_usage.record_response_usage(
            self.session_dir,
            stage="final_recap",
            model="priced-model",
            provider="openai",
            response_payload={"usage": {"total_tokens": 500}},
            pricing_path=self.pricing_path,
        )
        self.assertFalse(event["tokenUsageAvailable"])
        self.assertIsNone(event["estimatedCost"])

    def test_empty_summary_includes_future_reconciliation_stage(self):
        summary = ai_usage.summarize_usage(self.session_dir, self.pricing_path)
        reconciliation = next(item for item in summary["stages"] if item["stage"] == "session_reconciliation")
        self.assertEqual(reconciliation["requests"], 0)
        self.assertEqual(reconciliation["estimatedCost"], 0.0)

    def test_server_chat_wrapper_records_response_usage_offline(self):
        session_id = "12345678"
        response = json.dumps({
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        })
        with (
            mock.patch.object(server, "UPLOADS_DIR", self.temp_dir.name),
            mock.patch.object(server, "AI_PRICING_PATH", self.pricing_path),
            mock.patch.object(server, "_openai_api_key", return_value="test-key-not-used"),
            mock.patch.object(server, "_urlopen_with_retry", return_value=response),
        ):
            result = server._chat_complete_text(
                "system",
                "user",
                "priced-model",
                usage_stage="final_recap",
                session_id=session_id,
            )

        self.assertEqual(result, "ok")
        summary = ai_usage.summarize_usage(
            os.path.join(self.temp_dir.name, session_id),
            self.pricing_path,
        )
        self.assertEqual(summary["total"]["requests"], 1)
        self.assertEqual(summary["total"]["inputTokens"], 10)


if __name__ == "__main__":
    unittest.main()

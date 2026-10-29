import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import service_providers as providers
import interview_openai as transport
import service_intake as intake
import test_intake as intake_tests


def response(inputs=100, outputs=400, thoughts=300):
    return {"model": "gpt-6-luna", "status": "completed", "error": None,
            "output": [{"type": "reasoning"}, {"type": "message", "role": "assistant",
                        "status": "completed", "content": [{"type": "output_text",
                        "text": json.dumps({"question": "どんな写真展にしたいですか？"})}]}],
            "usage": {"input_tokens": inputs, "output_tokens": outputs, "total_tokens": inputs + outputs,
                      "output_tokens_details": {"reasoning_tokens": thoughts}}}


class OpenAIInterviewTest(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"OPENAI_API_KEY": "test-only-secret",
            "NOTTA_AI_ENABLED": "yes", "NOTTA_INTERVIEW_PROVIDER": "openai", "GEMINI_API_KEY": ""})
        env.start()
        self.addCleanup(env.stop)

    def generate(self):
        return providers.interview_question("写真展を開きたい", [], "目的", 1)

    def test_exact_model_effort_schema_timeouts_and_no_contacts_tools_or_storage(self):
        output = io.StringIO()
        with patch.object(transport, "request_json", side_effect=[{"input_tokens": 120}, response()]) as call, redirect_stdout(output):
            value = self.generate()
        count, generate = call.call_args_list
        self.assertEqual(count.args[0], "https://api.openai.com/v1/responses/input_tokens")
        self.assertEqual(generate.args[0], "https://api.openai.com/v1/responses")
        self.assertEqual((count.kwargs["timeout"], generate.kwargs["timeout"]), (5, 40))
        payload = generate.kwargs["data"]
        self.assertEqual(payload["model"], "gpt-6-luna")
        self.assertEqual(payload["reasoning"], {"effort": "medium"})
        self.assertEqual(payload["max_output_tokens"], 4096)
        self.assertIs(payload["store"], False)
        self.assertIs(payload["text"]["format"]["strict"], True)
        self.assertIs(payload["text"]["format"]["schema"]["additionalProperties"], False)
        self.assertEqual(count.kwargs["data"], {k: v for k, v in payload.items() if k not in ("store", "max_output_tokens")})
        self.assertNotIn("tools", payload)
        self.assertNotIn("email", json.dumps(payload))
        self.assertNotIn("temperature", payload)
        self.assertEqual(value["cost_yen"], 1)
        self.assertTrue(value["question"])
        log = json.loads(output.getvalue())
        self.assertEqual(log["output_tokens"], 400)
        self.assertNotIn("test-only-secret", output.getvalue())
        self.assertNotIn("写真展", output.getvalue())

    def test_maximum_cost_including_reasoning_fits_ten_yen_reservation(self):
        with patch.object(transport, "request_json", side_effect=[{"input_tokens": 32768}, response(32768, 4096, 4000)]), redirect_stdout(io.StringIO()):
            value = self.generate()
        self.assertEqual(value["cost_yen"], 2)

    def test_invalid_count_stops_before_inference(self):
        for counted in (None, True, "100", -1, 0, 32769):
            with self.subTest(count=counted), patch.object(transport, "request_json", return_value={"input_tokens": counted}) as call:
                with self.assertRaises(providers.ProviderError) as caught:
                    self.generate()
                self.assertFalse(caught.exception.uncertain)
                self.assertEqual(call.call_count, 1)

    def test_count_network_failure_releases_reservation(self):
        with patch.object(transport, "request_json", side_effect=providers.ProviderError("count timeout", uncertain=True)) as call:
            with self.assertRaises(providers.ProviderError) as caught:
                self.generate()
        self.assertFalse(caught.exception.uncertain)
        self.assertEqual(call.call_count, 1)

    def test_invalid_or_refused_response_retains_reservation_and_never_retries(self):
        cases = []
        def changed(key, value):
            item = response(); item[key] = value; cases.append(item)
        changed("status", "incomplete")
        changed("model", "another-model")
        changed("usage", {})
        changed("output", [{"type": "message", "role": "assistant", "status": "completed", "content": [{"type": "refusal"}]}])
        changed("output", [{"type": "web_search_call"}])
        for text in ('not json', '{"question":""}', json.dumps({"question": "a" * 601}), '{"question":"ok","extra":true}'):
            item = response(); item["output"][1]["content"][0]["text"] = text; cases.append(item)
        for usage in ({"input_tokens": True, "output_tokens": 400, "total_tokens": 401},
                      {"input_tokens": 100, "output_tokens": 4097, "total_tokens": 4197},
                      {"input_tokens": 100, "output_tokens": 400, "total_tokens": 0},
                      {"input_tokens": 100, "output_tokens": 400, "total_tokens": 500, "output_tokens_details": {"reasoning_tokens": 401}}):
            changed("usage", usage)
        for item in cases:
            with self.subTest(response=item), patch.object(transport, "request_json", side_effect=[{"input_tokens": 100}, item]) as call:
                with self.assertRaises(providers.ProviderError) as caught:
                    self.generate()
                self.assertTrue(caught.exception.uncertain)
                self.assertEqual(call.call_count, 2)

    def test_timeout_after_generation_does_not_retry_or_fall_back(self):
        with patch.object(transport, "request_json", side_effect=[{"input_tokens": 100}, providers.ProviderError("timeout", uncertain=True)]) as call, patch.object(providers, "request_json") as gemini:
            with self.assertRaises(providers.ProviderError) as caught:
                self.generate()
        self.assertTrue(caught.exception.uncertain)
        self.assertEqual(call.call_count, 2)
        gemini.assert_not_called()

    def test_readiness_is_independent_from_legacy_gemini(self):
        self.assertTrue(providers.interview_ready())
        self.assertFalse(providers.ai_ready())
        for values in ({"OPENAI_API_KEY": "", "GEMINI_API_KEY": "present"}, {"NOTTA_AI_ENABLED": "no"}, {"NOTTA_INTERVIEW_PROVIDER": "invalid"}):
            with self.subTest(values=values), patch.dict(os.environ, values), patch.object(transport, "request_json") as call:
                self.assertFalse(providers.interview_ready())
                with self.assertRaises(providers.ProviderError):
                    self.generate()
                call.assert_not_called()
        with patch.dict(os.environ, {"GEMINI_API_KEY": "old-key"}):
            self.assertTrue(providers.ai_ready())


class OpenAIIntakeFlowTest(intake_tests.IntakeTest):
    """Run the existing lifecycle suite through real OpenAI parsing and mocked HTTP."""
    def setUp(self):
        super().setUp()
        self.generate.side_effect = REAL_INTERVIEW
        self.env = patch.dict(os.environ, {"NOTTA_INTERVIEW_PROVIDER": "openai", "OPENAI_API_KEY": "test-only", "NOTTA_AI_ENABLED": "yes"})
        self.env.start()
        self.http = patch.object(transport, "request_json", side_effect=lambda url, **kw: {"input_tokens": 100} if url.endswith("/input_tokens") else response(32768, 4096, 4000)).start()
        self.logs = redirect_stdout(io.StringIO()); self.logs.__enter__()
        self.addCleanup(lambda: self.logs.__exit__(None, None, None))


REAL_INTERVIEW = providers.interview_question


if __name__ == "__main__":
    unittest.main()

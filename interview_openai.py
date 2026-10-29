"""Bounded, stateless OpenAI interview transport. Never retries inference."""
import json
import math
import os

from service_providers import ProviderError, request_json

MODEL = "gpt-6-luna"
BASE = "https://api.openai.com/v1/responses"
INPUT_LIMIT = 32768
OUTPUT_LIMIT = 4096
SCHEMA = {"type": "object", "properties": {"question": {"type": "string"}},
          "required": ["question"], "additionalProperties": False}


def ready():
    return bool(os.getenv("OPENAI_API_KEY") and os.getenv("NOTTA_AI_ENABLED") == "yes")


def question(system, context):
    if not ready():
        raise ProviderError("質問の接続を準備しています。")
    headers = {"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]}
    measured = {"model": MODEL, "instructions": system,
                "input": [{"role": "user", "content": context}],
                "reasoning": {"effort": "medium"},
                "text": {"format": {"type": "json_schema", "name": "interview_question",
                                    "strict": True, "schema": SCHEMA}}}
    try:
        count = request_json(BASE + "/input_tokens", method="POST", data=measured,
                             headers=headers, timeout=5)
    except ProviderError as error:
        error.uncertain = False  # counting does not invoke inference
        raise
    counted = count.get("input_tokens") if isinstance(count, dict) else None
    if type(counted) is not int or not 0 < counted <= INPUT_LIMIT:
        raise ProviderError("入力の量を確認できませんでした。内容を短くしてから再度お試しください。")
    response = request_json(BASE, method="POST", headers=headers, timeout=40,
                            data={**measured, "store": False, "max_output_tokens": OUTPUT_LIMIT})
    try:
        if response["status"] != "completed" or response.get("error") or response.get("incomplete_details"):
            raise ValueError("incomplete")
        if response["model"] != MODEL and not response["model"].startswith(MODEL + "-"):
            raise ValueError("unexpected model")
        usage = response["usage"]
        inputs, outputs = usage["input_tokens"], usage["output_tokens"]
        reasoning = usage.get("output_tokens_details", {}).get("reasoning_tokens", 0)
        cached = usage.get("input_tokens_details", {}).get("cached_tokens", 0)
        if any(type(v) is not int or v < 0 for v in (inputs, outputs, reasoning, cached)):
            raise ValueError("usage")
        if inputs > INPUT_LIMIT or outputs > OUTPUT_LIMIT or reasoning > outputs or cached > inputs:
            raise ValueError("usage bounds")
        if type(usage["total_tokens"]) is not int or usage["total_tokens"] != inputs + outputs:
            raise ValueError("usage total")
        texts = []
        for item in response["output"]:
            if item["type"] == "reasoning":
                continue
            if item["type"] != "message" or item["role"] != "assistant" or item["status"] != "completed":
                raise ValueError("unexpected output")
            for part in item["content"]:
                if part["type"] != "output_text":
                    raise ValueError("refusal or non-text output")
                texts.append(part["text"])
        if len(texts) != 1:
            raise ValueError("one question required")
        result = json.loads(texts[0])
        if not isinstance(result, dict) or set(result) != {"question"}:
            raise ValueError("schema")
        value = result["question"]
        if not isinstance(value, str) or not value.strip() or len(value) > 600:
            raise ValueError("question")
        # Standard input $0.10/M, cache writes $0.125/M, output $0.50/M.
        # Reserve the higher input price for all input; do not assume a cache hit.
        # output_tokens already INCLUDES reasoning. 250 JPY/USD is a budget guard.
        yen = max(1, math.ceil((max(inputs, counted) * .125 + outputs * .50) / 1_000_000 * 250))
        print(json.dumps({"event": "interview_ai_usage", "provider": "openai", "model": MODEL,
                          "reasoning_effort": "medium", "input_tokens": inputs,
                          "output_tokens": outputs, "reasoning_tokens": reasoning,
                          "budget_cost_yen": yen}, sort_keys=True))
        return {"question": value.strip(), "cost_yen": yen}
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        raise ProviderError("質問の生成結果と利用量を確認しています。再送せずお待ちください。", uncertain=True) from None

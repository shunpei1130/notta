"""Opt-in, one fictitious interview inference; never prints credentials or input."""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import service_providers as providers


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--confirm-live", action="store_true", required=True)
    args = parser.parse_args()
    for line in args.env_file.read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("OPENAI_API_KEY="):
            os.environ["OPENAI_API_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")
    os.environ.update(NOTTA_AI_ENABLED="yes", NOTTA_INTERVIEW_PROVIDER="openai")
    try:
        value = providers.interview_question("架空の写真サークルで小さな写真展を開きたい", [], "目的", 1)
        print(json.dumps({"smoke": "passed", "question_valid": bool(value["question"]) and len(value["question"]) <= 600,
                          "budget_cost_yen": value["cost_yen"]}))
    except providers.ProviderError as error:
        print(json.dumps({"smoke": "failed", "uncertain": error.uncertain, "message": str(error)}, ensure_ascii=True))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

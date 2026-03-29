"""Result Judge – compares Oracle predictions with actual test observations
using an LLM to produce a structured verdict.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional


_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPT_DIR / name).read_text(encoding="utf-8")


class ResultJudge:
    """LLM-based test result judge."""

    def __init__(self):
        from .test_agent import _load_api_config
        cfg = _load_api_config()
        self.model_id = cfg["model_id"]
        self.api_key = cfg["api_key"]
        self.base_url = cfg["base_url"]

    def judge(
        self,
        intent: str,
        ltl_spec: Optional[str],
        oracle_prediction: dict,
        observations: dict,
    ) -> dict[str, Any]:
        """Compare oracle predictions with actual observations.

        Returns a verdict dict with keys: overall, per_packet, reasoning, evidence.
        """
        system_prompt = _load_prompt("judge_system.md")
        user_msg = self._build_user_message(intent, ltl_spec, oracle_prediction, observations)

        from openai import OpenAI
        client = OpenAI(api_key=self.api_key, base_url=self.base_url)

        try:
            response = client.chat.completions.create(
                model=self.model_id,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.1,
                max_tokens=2048,
            )
            content = response.choices[0].message.content or ""
            return self._parse_verdict(content)
        except Exception as exc:
            return {
                "overall": "INCONCLUSIVE",
                "per_packet": [],
                "reasoning": f"Judge failed: {exc}",
                "evidence": [],
            }

    @staticmethod
    def _build_user_message(
        intent: str,
        ltl_spec: Optional[str],
        oracle_prediction: dict,
        observations: dict,
    ) -> str:
        oracle_json = json.dumps(oracle_prediction, indent=2, ensure_ascii=False)
        obs_json = json.dumps(observations, indent=2, ensure_ascii=False)

        return f"""## Natural Language Intent
{intent}

## P4LTL Specification
{ltl_spec or "(not available)"}

## Oracle Predictions
```json
{oracle_json[:3000]}
```

## Actual Observations
```json
{obs_json[:3000]}
```

Please produce your verdict following the output format in your instructions."""

    @staticmethod
    def _parse_verdict(content: str) -> dict[str, Any]:
        import re
        json_match = re.search(r"```json\s*(.*?)\s*```", content, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {
                "overall": "INCONCLUSIVE",
                "per_packet": [],
                "reasoning": content,
                "evidence": [],
            }

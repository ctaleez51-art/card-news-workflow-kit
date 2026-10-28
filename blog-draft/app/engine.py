from __future__ import annotations

import json
import re
from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query


async def ask(prompt: str) -> dict:
    options = ClaudeAgentOptions(
        tools=[],
        allowed_tools=[],
        max_turns=4,
        system_prompt=(
            "학습 기록에서 블로그 초안을 만드는 편집자입니다. 입력 기록에 없는 사실을 만들지 마세요. "
            "반드시 JSON 객체 하나만 답하세요. 근거가 부족하면 부족하다고 답하세요."
        ),
        env={"CLAUDE_AGENT_SDK_CLIENT_APP": "blog-draft-workflow-kit/1.0"},
    )
    texts = []
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            texts.extend(block.text for block in message.content if isinstance(block, TextBlock))
        elif isinstance(message, ResultMessage) and message.structured_output:
            if isinstance(message.structured_output, dict):
                return message.structured_output
    text = "\n".join(texts).strip()
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if match:
        text = match.group(1)
    else:
        text = text[text.find("{"):text.rfind("}") + 1]
    return json.loads(text)

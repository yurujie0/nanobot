"""Context consolidator for intelligent context management."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Union, List, Dict

from loguru import logger


@dataclass
class ConsolidationResult:
    """Result of context consolidation."""
    new_summaries: List[Dict[str, str]] = field(default_factory=list)
    next_goal: str = ""
    needed_msg_ids: List[str] = field(default_factory=list)
    reasoning: str = ""
    parent_turn_ids: List[int] = field(default_factory=list)


class ContextConsolidator:
    """
    Context consolidator for intelligent message summarization and selection.

    This component runs after each tool execution round to:
    1. Generate summaries for new messages
    2. Predict the next reasoning goal
    3. Select relevant message IDs for the next round
    """

    SYSTEM_PROMPT = """You are a context management assistant. Your task is to:

1. **Generate concise summaries** (10-20 words) for new messages that capture their semantic content
2. **Predict the next reasoning goal** (1 sentence) based on current progress
3. **Select relevant message IDs** needed for the next step from history

## Input Format
You will receive:
- `new_messages`: Messages produced in the current turn (need summaries)
- `history_summaries`: Previous messages with their summaries
- `user_query`: The original user request

## Output Format (JSON)
```json
{
  "new_summaries": [
    {"msg_id": "msg_X_Y", "summary": "..."},
    ...
  ],
  "next_goal": "The goal for the next reasoning step",
  "needed_msg_ids": ["msg_X_Y", "msg_A_B", ...],
  "reasoning": "Brief explanation of why these messages are selected"
}
```

## Rules for selecting messages:
- **Include** tool results that contain facts needed for the next step
- **Include** user requirements and constraints
- **Include** relevant context from previous turns
- **Skip** irrelevant historical context
- **Skip** redundant information already captured in other selected messages
- Ensure tool calls and their results are paired
- Keep the total selected messages under 20 for efficiency

## Example
Input:
- new_messages: assistant calls list_dir, tool returns ["src", "tests"]
- history_summaries: [{"msg_id": "msg_0_0", "summary": "User asked to analyze project"}]
- user_query: "Analyze this project"

Output:
{
  "new_summaries": [
    {"msg_id": "msg_0_1", "summary": "Assistant decided to list project root directory"},
    {"msg_id": "msg_0_2", "summary": "Root directory contains src and tests folders"}
  ],
  "next_goal": "Explore the src directory to understand code structure",
  "needed_msg_ids": ["msg_0_2"],
  "reasoning": "To explore src directory, need to know it exists from previous result"
}
"""

    def __init__(
        self,
        provider,
        model: Optional[str] = None,
        temperature: float = 0.3,
    ):
        from nanobot.providers.base import LLMProvider
        self.provider: LLMProvider = provider
        self.model = model or provider.get_default_model()
        self.temperature = temperature
        self._current_turn_id: int = 0

    def _truncate(self, text: str, max_len: int = 200) -> str:
        """Truncate text for display."""
        if not isinstance(text, str):
            text = str(text)
        if len(text) <= max_len:
            return text
        return text[:max_len] + "..."

    def _format_message_for_summary(self, msg: Dict[str, Any]) -> str:
        """Format a message for the consolidator input."""
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        msg_id = msg.get("msg_id", "unknown")

        lines = [f"[{msg_id}] {role}"]

        if role == "assistant":
            # Include content preview
            lines.append(f"  content: {self._truncate(content)}")
            # Include tool calls
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        func = tc.get("function", {})
                        name = func.get("name", "unknown")
                        args = self._truncate(str(func.get("arguments", "")), 100)
                        lines.append(f"  tool_call: {name}({args})")
        elif role == "tool":
            # Include tool name and result preview
            name = msg.get("name", "unknown")
            lines.append(f"  name: {name}")
            lines.append(f"  result: {self._truncate(content)}")
        elif role == "user":
            lines.append(f"  content: {self._truncate(content)}")

        return "\n".join(lines)

    def _format_consolidation_input(
        self,
        new_messages: List[Dict[str, Any]],
        history_summaries: List[Dict[str, str]],
        user_query: str,
    ) -> str:
        """Format input for the consolidation LLM call."""
        lines = ["## Context Consolidation Task", ""]

        # User query
        lines.extend(["### User Query", user_query, ""])

        # New messages
        lines.extend(["### New Messages (generate summaries)", ""])
        for msg in new_messages:
            lines.append(self._format_message_for_summary(msg))
            lines.append("")

        # History summaries
        lines.extend(["### History Summaries", ""])
        if history_summaries:
            for item in history_summaries:
                msg_id = item.get("msg_id", "unknown")
                summary = item.get("summary", "")
                lines.append(f"- [{msg_id}] {summary}")
        else:
            lines.append("(none)")
        lines.append("")

        lines.extend([
            "### Instructions",
            "Generate JSON output following the format specified in your system prompt.",
            "Focus on selecting only messages truly needed for the next reasoning step."
        ])

        return "\n".join(lines)

    def _parse_consolidation_response(self, content: str) -> ConsolidationResult:
        """Parse the LLM response into ConsolidationResult."""
        try:
            # Try direct JSON parsing
            data = json.loads(content)
        except json.JSONDecodeError:
            # Try extracting from markdown code block
            match = re.search(r'```(?:json)?\s*(.*?)\s*```', content, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1))
                except json.JSONDecodeError:
                    raise ValueError(f"Invalid JSON in code block: {content[:200]}")
            else:
                raise ValueError(f"Invalid JSON response: {content[:200]}")

        return ConsolidationResult(
            new_summaries=data.get("new_summaries", []),
            next_goal=data.get("next_goal", ""),
            needed_msg_ids=data.get("needed_msg_ids", []),
            reasoning=data.get("reasoning", ""),
            parent_turn_ids=data.get("parent_turn_ids", []),
        )

    async def consolidate(
        self,
        session: Any,  # EnhancedSession
        current_turn_id: int,
        user_query: str,
    ) -> ConsolidationResult:
        """
        Perform context consolidation.

        This is a lightweight LLM call without tools/skills/memory.
        """
        # Get new messages (current turn)
        new_messages = [
            msg for msg in session.messages
            if msg.get("turn_id") == current_turn_id
        ]

        if not new_messages:
            logger.debug("No new messages to consolidate")
            return ConsolidationResult(
                new_summaries=[],
                next_goal="Continue with the task",
                needed_msg_ids=[],
                reasoning="No new messages",
            )

        # Get history summaries (previous turns)
        history_summaries = [
            {"msg_id": msg.get("msg_id", ""), "summary": msg.get("summary", "")}
            for msg in session.messages
            if msg.get("summary") and msg.get("turn_id") != current_turn_id
        ]

        # Build input
        user_content = self._format_consolidation_input(
            new_messages=new_messages,
            history_summaries=history_summaries,
            user_query=user_query,
        )

        # Make lightweight LLM call (no tools)
        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        try:
            response = await self.provider.chat_with_retry(
                messages=messages,
                model=self.model,
                temperature=self.temperature,
            )

            result = self._parse_consolidation_response(response.content)

            logger.info(
                "Context consolidation: {} new summaries, {} needed messages, goal: '{}'",
                len(result.new_summaries),
                len(result.needed_msg_ids),
                result.next_goal[:50],
            )

            return result

        except Exception as e:
            logger.error("Context consolidation failed: {}", e)
            # Fallback: return conservative result
            return ConsolidationResult(
                new_summaries=[
                    {"msg_id": msg.get("msg_id", ""), "summary": f"{msg.get('role', 'unknown')} message"}
                    for msg in new_messages
                ],
                next_goal="Continue with the task",
                needed_msg_ids=[msg.get("msg_id", "") for msg in new_messages],
                reasoning=f"Consolidation failed: {e}",
            )

    def update_message_summaries(
        self,
        session: Any,
        summaries: List[Dict[str, str]],
    ) -> None:
        """Update message summaries in session."""
        for item in summaries:
            msg_id = item.get("msg_id")
            summary = item.get("summary")
            if msg_id and summary:
                # Find message in session
                for msg in session.messages:
                    if msg.get("msg_id") == msg_id:
                        msg["summary"] = summary
                        logger.debug("Updated summary for {}: {}", msg_id, summary)
                        break

"""Enhanced agent runner with context consolidation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional, Callable, Awaitable
from datetime import datetime

from loguru import logger

from nanobot.agent.runner import AgentRunner, AgentRunSpec, AgentRunResult, AgentHook
from nanobot.agent.context_consolidator import ContextConsolidator
from nanobot.agent.enhanced_session import EnhancedSession
from nanobot.agent.hook import AgentHookContext
from nanobot.utils.helpers import build_assistant_message


@dataclass
class EnhancedAgentRunSpec:
    """Configuration for enhanced agent execution."""
    initial_messages: list[dict[str, Any]]
    tools: Any  # ToolRegistry
    model: str
    max_iterations: int = 40
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    reasoning_effort: Optional[str] = None
    hook: Optional[AgentHook] = None
    error_message: Optional[str] = None
    max_iterations_message: Optional[str] = None
    concurrent_tools: bool = False
    fail_on_tool_error: bool = False

    # Enhanced fields
    session: Optional[EnhancedSession] = None
    context_consolidator: Optional[ContextConsolidator] = None
    user_query: str = ""
    enable_context_consolidation: bool = True


@dataclass
class EnhancedAgentRunResult:
    """Result of enhanced agent execution."""
    final_content: Optional[str]
    messages: list[dict[str, Any]]
    tools_used: list[str]
    usage: dict[str, int]
    stop_reason: str
    error: Optional[str] = None
    tool_events: list[dict[str, str]] = field(default_factory=list)
    # Enhanced fields
    turn_count: int = 0
    consolidation_count: int = 0


class EnhancedAgentRunner(AgentRunner):
    """
    Enhanced agent runner with context consolidation.

    Extends base AgentRunner to add:
    - Turn-based message organization
    - Context consolidation between turns
    - Dynamic message selection for LLM context
    """

    def __init__(self, provider: Any):
        super().__init__(provider)
        self.provider = provider

    def _generate_msg_id(self, session: EnhancedSession, turn_id: int) -> str:
        """Generate unique message ID for current turn."""
        seq = len([m for m in session.messages if m.get("turn_id") == turn_id])
        return f"msg_{turn_id}_{seq}"

    def _build_messages_for_iteration(
        self,
        spec: EnhancedAgentRunSpec,
        session: EnhancedSession,
        iteration: int,
        turn_id: int,
        consolidation_result: Optional[Any],
        full_messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Build message list for current iteration.

        This method follows the same pattern as the base AgentRunner:
        - Messages are progressively appended to the list
        - Message count and order are preserved
        - Only the CONTENT of non-relevant messages is replaced with summaries

        Args:
            spec: Run specification
            session: Enhanced session with message metadata
            iteration: Current iteration number
            turn_id: Current turn ID
            consolidation_result: Result from context consolidation (if available)
            full_messages: Complete message history (from previous iterations)

        Returns:
            Message list for the LLM call
        """
        if iteration == 0:
            # First iteration: use original messages (same as base runner)
            return list(spec.initial_messages)

        # For subsequent iterations, we need to apply compression to the full messages
        # while preserving count and order
        if consolidation_result and consolidation_result.needed_msg_ids:
            # Build a set of message IDs that should be kept in full
            needed_ids = set(consolidation_result.needed_msg_ids)

            # Add messages from the most recent turn (they haven't been summarized yet)
            current_turn_msgs = session.get_messages_by_turn(turn_id)
            for msg in current_turn_msgs:
                msg_id = msg.get("msg_id")
                if msg_id:
                    needed_ids.add(msg_id)

            # Build compressed messages preserving order and count
            compressed_messages = []

            # First, add system messages and user query from initial_messages
            # (these are always needed and not part of session messages)
            for msg in spec.initial_messages:
                if msg.get("role") == "system":
                    compressed_messages.append(dict(msg))
                elif msg.get("role") == "user":
                    # Always keep the user query in full
                    compressed_messages.append(dict(msg))

            # Now process session messages in order
            for msg in session.messages:
                msg_id = msg.get("msg_id")
                role = msg.get("role", "")

                # Skip system messages (already handled above)
                if role == "system":
                    continue

                # Determine if this message should be in full or summarized
                if msg_id and msg_id in needed_ids:
                    # Keep full message
                    entry = {
                        "role": role,
                        "content": msg.get("content", ""),
                    }
                    # Preserve tool-related fields
                    if "tool_calls" in msg:
                        entry["tool_calls"] = msg["tool_calls"]
                    if "tool_call_id" in msg:
                        entry["tool_call_id"] = msg["tool_call_id"]
                    if "name" in msg:
                        entry["name"] = msg["name"]
                    compressed_messages.append(entry)
                else:
                    # Use summary if available, otherwise keep original
                    summary = msg.get("summary")
                    if summary:
                        # Replace content with summary but keep the same structure
                        entry = {"role": role, "content": f"[{summary}]"}
                        # Preserve tool-related fields
                        if "tool_calls" in msg:
                            entry["tool_calls"] = msg["tool_calls"]
                        if "tool_call_id" in msg:
                            entry["tool_call_id"] = msg["tool_call_id"]
                        if "name" in msg:
                            entry["name"] = msg["name"]
                        compressed_messages.append(entry)
                    else:
                        # No summary yet, keep original
                        entry = {
                            "role": role,
                            "content": msg.get("content", ""),
                        }
                        if "tool_calls" in msg:
                            entry["tool_calls"] = msg["tool_calls"]
                        if "tool_call_id" in msg:
                            entry["tool_call_id"] = msg["tool_call_id"]
                        if "name" in msg:
                            entry["name"] = msg["name"]
                        compressed_messages.append(entry)

            logger.debug(
                "Built messages: {} total, {} kept in full, {} summarized",
                len(compressed_messages),
                len(needed_ids),
                len(session.messages) - len(needed_ids),
            )

            return compressed_messages

        # Fallback: return full messages as-is (same as base runner)
        return list(full_messages)

    async def run_enhanced(self, spec: EnhancedAgentRunSpec) -> EnhancedAgentRunResult:
        """
        Run enhanced agent loop with context consolidation.
        """
        hook = spec.hook or AgentHook()
        session = spec.session

        if not session:
            # Fallback to base runner if no session provided
            base_spec = AgentRunSpec(
                initial_messages=spec.initial_messages,
                tools=spec.tools,
                model=spec.model,
                max_iterations=spec.max_iterations,
                temperature=spec.temperature,
                max_tokens=spec.max_tokens,
                reasoning_effort=spec.reasoning_effort,
                hook=spec.hook,
                error_message=spec.error_message,
                max_iterations_message=spec.max_iterations_message,
                concurrent_tools=spec.concurrent_tools,
                fail_on_tool_error=spec.fail_on_tool_error,
            )
            result = await super().run(base_spec)
            return EnhancedAgentRunResult(
                final_content=result.final_content,
                messages=result.messages,
                tools_used=result.tools_used,
                usage=result.usage,
                stop_reason=result.stop_reason,
                error=result.error,
                tool_events=result.tool_events,
            )

        # Initialize turn tracking
        turn_id = session.get_next_turn_id()
        consolidation_count = 0

        # Setup for iteration - full_messages tracks the complete message history
        # This follows the same pattern as the base AgentRunner
        full_messages: list[dict[str, Any]] = list(spec.initial_messages)
        final_content: Optional[str] = None
        tools_used: list[str] = []
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        error: Optional[str] = None
        stop_reason = "completed"
        tool_events: list[dict[str, str]] = []

        consolidation_result = None

        for iteration in range(spec.max_iterations):
            # Build compressed messages for LLM call
            # This preserves message count and order, only replacing content with summaries
            messages_for_llm = self._build_messages_for_iteration(
                spec=spec,
                session=session,
                iteration=iteration,
                turn_id=turn_id,
                consolidation_result=consolidation_result,
                full_messages=full_messages,
            )

            context = AgentHookContext(iteration=iteration, messages=messages_for_llm)
            await hook.before_iteration(context)

            # ========================================
            # Context Consolidation (after first iteration)
            # ========================================
            if (
                iteration > 0
                and spec.enable_context_consolidation
                and spec.context_consolidator
            ):
                try:
                    consolidation_result = await spec.context_consolidator.consolidate(
                        session=session,
                        current_turn_id=turn_id,
                        user_query=spec.user_query,
                    )
                    consolidation_count += 1

                    # Update message summaries
                    spec.context_consolidator.update_message_summaries(
                        session=session,
                        summaries=consolidation_result.new_summaries,
                    )

                    # Complete the turn
                    session.complete_turn(
                        turn_id=turn_id,
                        goal=consolidation_result.next_goal,
                        status="completed",
                    )

                    # Start new turn
                    turn_id = session.get_next_turn_id()

                    # Rebuild compressed messages with updated summaries
                    messages_for_llm = self._build_messages_for_iteration(
                        spec=spec,
                        session=session,
                        iteration=iteration,
                        turn_id=turn_id,
                        consolidation_result=consolidation_result,
                        full_messages=full_messages,
                    )

                except Exception as e:
                    logger.error("Context consolidation failed: {}", e)
                    consolidation_result = None

            # Prepare LLM call
            kwargs: dict[str, Any] = {
                "messages": messages_for_llm,
                "tools": spec.tools.get_definitions() if spec.tools else None,
                "model": spec.model,
            }
            if spec.temperature is not None:
                kwargs["temperature"] = spec.temperature
            if spec.max_tokens is not None:
                kwargs["max_tokens"] = spec.max_tokens
            if spec.reasoning_effort is not None:
                kwargs["reasoning_effort"] = spec.reasoning_effort

            # Make LLM call
            if hook.wants_streaming():
                async def _stream(delta: str) -> None:
                    await hook.on_stream(context, delta)

                response = await self.provider.chat_stream_with_retry(
                    **kwargs,
                    on_content_delta=_stream,
                )
            else:
                response = await self.provider.chat_with_retry(**kwargs)

            # Track usage
            raw_usage = response.usage or {}
            usage = {
                "prompt_tokens": int(raw_usage.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(raw_usage.get("completion_tokens", 0) or 0),
            }
            context.response = response
            context.usage = usage
            context.tool_calls = list(response.tool_calls)

            # ========================================
            # Record Assistant Message with metadata
            # ========================================
            msg_id = self._generate_msg_id(session, turn_id)
            assistant_msg = build_assistant_message(
                response.content or "",
                tool_calls=[tc.to_openai_tool_call() for tc in response.tool_calls] if response.tool_calls else None,
                reasoning_content=response.reasoning_content,
                thinking_blocks=response.thinking_blocks,
            )

            # Build the assistant message entry
            assistant_entry = {
                "role": "assistant",
                "content": assistant_msg.get("content", ""),
            }
            if assistant_msg.get("tool_calls"):
                assistant_entry["tool_calls"] = assistant_msg["tool_calls"]

            # Add to full_messages (following base runner pattern)
            full_messages.append(assistant_entry)

            # Add to session with metadata
            session.add_message(
                role="assistant",
                content=assistant_msg.get("content", ""),
                turn_id=turn_id,
                msg_id=msg_id,
                tool_calls=assistant_msg.get("tool_calls"),
                reasoning_content=response.reasoning_content,
                thinking_blocks=response.thinking_blocks,
            )

            # ========================================
            # Handle Tool Calls or Complete
            # ========================================
            if response.has_tool_calls:
                if hook.wants_streaming():
                    await hook.on_stream_end(context, resuming=True)

                tools_used.extend(tc.name for tc in response.tool_calls)
                await hook.before_execute_tools(context)

                results, new_events, fatal_error = await self._execute_tools(spec, response.tool_calls)
                tool_events.extend(new_events)
                context.tool_results = list(results)
                context.tool_events = list(new_events)

                if fatal_error is not None:
                    error = f"Error: {type(fatal_error).__name__}: {fatal_error}"
                    stop_reason = "tool_error"
                    context.error = error
                    context.stop_reason = stop_reason
                    await hook.after_iteration(context)
                    break

                # Record tool results with metadata (following base runner pattern)
                for tc, result in zip(response.tool_calls, results):
                    tool_entry = {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.name,
                        "content": result,
                    }
                    # Add to full_messages
                    full_messages.append(tool_entry)

                    tool_msg_id = self._generate_msg_id(session, turn_id)
                    session.add_message(
                        role="tool",
                        content=str(result),
                        turn_id=turn_id,
                        msg_id=tool_msg_id,
                        tool_call_id=tc.id,
                        name=tc.name,
                    )

                await hook.after_iteration(context)
                continue

            # ========================================
            # Complete - No tool calls
            # ========================================
            if hook.wants_streaming():
                await hook.on_stream_end(context, resuming=False)

            clean = hook.finalize_content(context, response.content)

            if response.finish_reason == "error":
                final_content = clean or spec.error_message or "Sorry, I encountered an error."
                stop_reason = "error"
                error = final_content
            else:
                final_content = clean
                stop_reason = "completed"

            context.final_content = final_content
            context.stop_reason = stop_reason
            await hook.after_iteration(context)

            # Complete final turn
            if consolidation_result:
                session.complete_turn(
                    turn_id=turn_id,
                    goal=consolidation_result.next_goal,
                    status=stop_reason,
                )

            break

        else:
            # Max iterations reached
            stop_reason = "max_iterations"
            template = spec.max_iterations_message or "I reached the maximum number of iterations."
            final_content = template.format(max_iterations=spec.max_iterations)

        return EnhancedAgentRunResult(
            final_content=final_content,
            messages=full_messages,
            tools_used=tools_used,
            usage=usage,
            stop_reason=stop_reason,
            error=error,
            tool_events=tool_events,
            turn_count=session.current_turn_id,
            consolidation_count=consolidation_count,
        )

    async def _execute_tools(
        self,
        spec: EnhancedAgentRunSpec,
        tool_calls: list[Any],
    ) -> tuple[list[Any], list[dict[str, str]], Optional[BaseException]]:
        """Execute tools (copied from base runner)."""
        if spec.concurrent_tools:
            tool_results = await __import__('asyncio').gather(*(
                self._run_tool(spec, tc)
                for tc in tool_calls
            ))
        else:
            tool_results = [
                await self._run_tool(spec, tc)
                for tc in tool_calls
            ]

        results: list[Any] = []
        events: list[dict[str, str]] = []
        fatal_error: Optional[BaseException] = None

        for result, event, error in tool_results:
            results.append(result)
            events.append(event)
            if error is not None and fatal_error is None:
                fatal_error = error

        return results, events, fatal_error

    async def _run_tool(
        self,
        spec: EnhancedAgentRunSpec,
        tool_call: Any,
    ) -> tuple[Any, dict[str, str], Optional[BaseException]]:
        """Run a single tool (copied from base runner)."""
        try:
            result = await spec.tools.execute(tool_call.name, tool_call.arguments)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": str(exc),
            }
            if spec.fail_on_tool_error:
                return f"Error: {type(exc).__name__}: {exc}", event, exc
            return f"Error: {type(exc).__name__}: {exc}", event, None

        detail = "" if result is None else str(result)
        detail = detail.replace("\n", " ").strip()
        if not detail:
            detail = "(empty)"
        elif len(detail) > 120:
            detail = detail[:120] + "..."

        return result, {
            "name": tool_call.name,
            "status": "error" if isinstance(result, str) and result.startswith("Error") else "ok",
            "detail": detail,
        }, None

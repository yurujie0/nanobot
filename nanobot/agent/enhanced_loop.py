"""Enhanced agent loop with context consolidation support."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from nanobot.agent.loop import AgentLoop, _LoopHook, _LoopHookChain
from nanobot.agent.enhanced_runner import EnhancedAgentRunner, EnhancedAgentRunSpec
from nanobot.agent.enhanced_session import EnhancedSessionManager, EnhancedSession
from nanobot.agent.context_consolidator import ContextConsolidator
from nanobot.agent.hook import AgentHookContext
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.session.manager import SessionManager

if TYPE_CHECKING:
    from nanobot.config.schema import ChannelsConfig, ExecToolConfig, WebSearchConfig
    from nanobot.cron.service import CronService
    from nanobot.providers.base import LLMProvider
    from nanobot.bus.queue import MessageBus


class EnhancedAgentLoop(AgentLoop):
    """
    Enhanced agent loop with context consolidation support.

    This extends the base AgentLoop to add:
    - Turn-based message organization
    - Context consolidation between tool rounds
    - Dynamic message selection for LLM context
    """

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 40,
        context_window_tokens: int = 65_536,
        web_search_config: WebSearchConfig | None = None,
        web_proxy: str | None = None,
        exec_config: ExecToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        channels_config: ChannelsConfig | None = None,
        timezone: str | None = None,
        hooks: list[Any] | None = None,
        # Enhanced parameters
        enable_context_consolidation: bool = True,
        consolidation_model: str | None = None,
    ):
        # Initialize base class
        super().__init__(
            bus=bus,
            provider=provider,
            workspace=workspace,
            model=model,
            max_iterations=max_iterations,
            context_window_tokens=context_window_tokens,
            web_search_config=web_search_config,
            web_proxy=web_proxy,
            exec_config=exec_config,
            cron_service=cron_service,
            restrict_to_workspace=restrict_to_workspace,
            session_manager=session_manager,
            mcp_servers=mcp_servers,
            channels_config=channels_config,
            timezone=timezone,
            hooks=hooks,
        )

        # Enhanced components
        self.enable_context_consolidation = enable_context_consolidation
        self.consolidation_model = consolidation_model

        # Replace runner with enhanced version
        self.enhanced_runner = EnhancedAgentRunner(provider)

        # Create context consolidator if enabled
        self.context_consolidator: ContextConsolidator | None = None
        if enable_context_consolidation:
            self.context_consolidator = ContextConsolidator(
                provider=provider,
                model=consolidation_model or provider.get_default_model(),
                temperature=0.3,
            )

        # Replace session manager with enhanced version
        self.enhanced_sessions = EnhancedSessionManager(workspace)
        if session_manager is None:
            self.sessions = self.enhanced_sessions

    async def _run_enhanced_agent_loop(
        self,
        initial_messages: list[dict],
        user_query: str,
        session: EnhancedSession,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        *,
        channel: str = "cli",
        chat_id: str = "direct",
        message_id: str | None = None,
    ) -> tuple[str | None, list[str], list[dict], int, int]:
        """
        Run the enhanced agent iteration loop.

        Returns: (final_content, tools_used, messages, turn_count, consolidation_count)
        """
        loop_hook = _LoopHook(
            self,
            on_progress=on_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            channel=channel,
            chat_id=chat_id,
            message_id=message_id,
        )
        hook = (
            _LoopHookChain(loop_hook, self._extra_hooks)
            if self._extra_hooks
            else loop_hook
        )

        # Create enhanced run spec
        spec = EnhancedAgentRunSpec(
            initial_messages=initial_messages,
            tools=self.tools,
            model=self.model,
            max_iterations=self.max_iterations,
            hook=hook,
            error_message="Sorry, I encountered an error calling the AI model.",
            concurrent_tools=True,
            # Enhanced fields
            session=session,
            context_consolidator=self.context_consolidator,
            user_query=user_query,
            enable_context_consolidation=self.enable_context_consolidation,
        )

        # Run enhanced loop
        result = await self.enhanced_runner.run_enhanced(spec)

        self._last_usage = result.usage

        if result.stop_reason == "max_iterations":
            logger.warning("Max iterations ({}) reached", self.max_iterations)
        elif result.stop_reason == "error":
            logger.error("LLM returned error: {}", (result.final_content or "")[:200])

        return (
            result.final_content,
            result.tools_used,
            result.messages,
            result.turn_count,
            result.consolidation_count,
        )

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """
        Process a single inbound message with enhanced context management.
        """
        # System messages: use base implementation
        if msg.channel == "system":
            return await super()._process_message(
                msg, session_key, on_progress, on_stream, on_stream_end
            )

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        key = session_key or msg.session_key

        # Get or create enhanced session
        if isinstance(self.sessions, EnhancedSessionManager):
            session = self.sessions.get_or_create(key)
        else:
            # Fallback: create enhanced session manually
            from nanobot.session.manager import Session
            base_session = self.sessions.get_or_create(key)
            # Convert to enhanced
            session = EnhancedSession(key=key)
            session.messages = base_session.messages
            session.created_at = base_session.created_at
            session.updated_at = base_session.updated_at
            session.metadata = base_session.metadata
            session.last_consolidated = base_session.last_consolidated

        # Slash commands
        from nanobot.command import CommandContext
        raw = msg.content.strip()
        ctx = CommandContext(msg=msg, session=session, key=key, raw=raw, loop=self)
        from nanobot.command import CommandRouter
        if result := await self.commands.dispatch(ctx):
            return result

        # Memory consolidation (base behavior)
        await self.memory_consolidator.maybe_consolidate_by_tokens(session)

        # Set tool context
        self._set_tool_context(msg.channel, msg.chat_id, msg.metadata.get("message_id"))
        from nanobot.agent.tools.message import MessageTool
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        # Build initial messages
        history = session.get_history(max_messages=0)
        initial_messages = self.context.build_messages(
            history=history,
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel, chat_id=msg.chat_id,
        )

        async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content=content, metadata=meta,
            ))

        # Run enhanced agent loop
        if self.enable_context_consolidation:
            final_content, tools_used, all_msgs, turn_count, consolidation_count = await self._run_enhanced_agent_loop(
                initial_messages,
                user_query=msg.content,
                session=session,
                on_progress=on_progress or _bus_progress,
                on_stream=on_stream,
                on_stream_end=on_stream_end,
                channel=msg.channel, chat_id=msg.chat_id,
                message_id=msg.metadata.get("message_id"),
            )

            logger.info(
                "Enhanced agent completed: {} turns, {} consolidations",
                turn_count,
                consolidation_count,
            )
        else:
            # Fallback to base implementation
            final_content, tools_used, all_msgs = await self._run_agent_loop(
                initial_messages,
                on_progress=on_progress or _bus_progress,
                on_stream=on_stream,
                on_stream_end=on_stream_end,
                channel=msg.channel, chat_id=msg.chat_id,
                message_id=msg.metadata.get("message_id"),
            )

        if final_content is None:
            final_content = "I've completed processing but have no response to give."

        # Save session
        if isinstance(self.sessions, EnhancedSessionManager):
            self.sessions.save(session)
        else:
            # Sync back to base session
            from nanobot.session.manager import Session
            base_session = self.sessions.get_or_create(key)
            base_session.messages = session.messages
            self.sessions.save(base_session)

        self._schedule_background(self.memory_consolidator.maybe_consolidate_by_tokens(session))

        if (mt := self.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            return None

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)

        meta = dict(msg.metadata or {})
        if on_stream is not None:
            meta["_streamed"] = True
        if self.enable_context_consolidation:
            meta["_enhanced"] = True

        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=final_content,
            metadata=meta,
        )

"""Agent core module."""

from nanobot.agent.context import ContextBuilder
from nanobot.agent.hook import AgentHook, AgentHookContext, CompositeHook
from nanobot.agent.loop import AgentLoop
from nanobot.agent.memory import MemoryStore
from nanobot.agent.skills import SkillsLoader
from nanobot.agent.subagent import SubagentManager

# Enhanced components
from nanobot.agent.context_consolidator import ContextConsolidator, ConsolidationResult
from nanobot.agent.enhanced_session import EnhancedSession, EnhancedSessionManager
from nanobot.agent.enhanced_runner import EnhancedAgentRunner, EnhancedAgentRunSpec, EnhancedAgentRunResult
from nanobot.agent.enhanced_loop import EnhancedAgentLoop

__all__ = [
    "AgentHook",
    "AgentHookContext",
    "AgentLoop",
    "CompositeHook",
    "ContextBuilder",
    "MemoryStore",
    "SkillsLoader",
    "SubagentManager",
    # Enhanced components
    "ContextConsolidator",
    "ConsolidationResult",
    "EnhancedSession",
    "EnhancedSessionManager",
    "EnhancedAgentRunner",
    "EnhancedAgentRunSpec",
    "EnhancedAgentRunResult",
    "EnhancedAgentLoop",
]

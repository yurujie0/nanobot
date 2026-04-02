# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

nanobot is an ultra-lightweight personal AI assistant framework. It's designed to be simple, readable, and extensible with minimal code.

## Development Commands

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run all tests
pytest

# Run a single test file
pytest tests/agent/test_runner.py

# Run with coverage
pytest --cov=nanobot

# Lint code
ruff check nanobot/

# Format code
ruff format nanobot/
```

## Architecture

### Core Components

**Agent Loop** (`nanobot/agent/loop.py`): The central processing engine. Receives messages from the bus, builds context (history, memory, skills), calls the LLM, executes tool calls, and sends responses back. Uses `AgentRunner` for iteration logic and `ToolRegistry` for tool management.

**Message Bus** (`nanobot/bus/`): Simple async queue for routing `InboundMessage` and `OutboundMessage` events between channels and the agent loop.

**Channels** (`nanobot/channels/`): Chat platform integrations (Telegram, Discord, Slack, WhatsApp, Feishu, etc.). Each channel extends `BaseChannel` and implements `start()`, `stop()`, and `send()`. Channels auto-discover via `registry.py` (pkgutil for built-in, entry_points for plugins).

**Providers** (`nanobot/providers/`): LLM provider integrations. The `registry.py` is the single source of truth—adding a new provider requires only adding a `ProviderSpec` to `PROVIDERS` and a field to `ProvidersConfig`. Supports OpenAI-compatible, Anthropic, Azure, and OAuth backends.

**Skills** (`nanobot/skills/` + `nanobot/agent/skills.py`): Markdown-based capabilities. Skills are `SKILL.md` files with YAML frontmatter that teach the agent how to perform tasks. Loaded by `SkillsLoader` which builds a skills summary for the context.

**Tools** (`nanobot/agent/tools/`): Built-in capabilities including filesystem (read/write/edit/list), shell exec, web search/fetch, cron, MCP integration, and subagent spawning.

**Sessions** (`nanobot/session/`): Conversation state management. Sessions store message history and are keyed by `channel:chat_id`.

**Configuration** (`nanobot/config/`): Pydantic-based schema with camelCase aliases. Config file at `~/.nanobot/config.json`.

### Key Architectural Patterns

**Adding a new LLM provider** (2 steps):
1. Add `ProviderSpec` to `nanobot/providers/registry.py:PROVIDERS`
2. Add field to `ProvidersConfig` in `nanobot/config/schema.py`

**Adding a new channel**:
1. Create module in `nanobot/channels/` extending `BaseChannel`
2. Implement `start()`, `stop()`, `send()` methods
3. Auto-discovered via `registry.py`

**Memory consolidation**: The `MemoryConsolidator` periodically archives older messages to `memory.jsonl` when token thresholds are exceeded, keeping recent context in the session.

**Tool execution**: Tools are registered in `ToolRegistry`. The `AgentLoop` calls tools concurrently by default and handles tool result truncation for large outputs.

**Streaming**: Channels opt into streaming via `streaming: true` in config and implementing `send_delta()`. Stream segments are tracked via `_stream_id` metadata.

## Testing Structure

Tests mirror the source structure:
- `tests/agent/` - Agent loop, runner, hooks, memory consolidation
- `tests/channels/` - Channel-specific tests
- `tests/providers/` - LLM provider tests
- `tests/tools/` - Tool functionality and security tests
- `tests/cron/`, `tests/config/`, `tests/security/`, `tests/cli/` - Module-specific tests

Tests use `pytest-asyncio` with `asyncio_mode = "auto"`.

## Code Style

- Line length: 100 characters
- Target: Python 3.11+
- Ruff for linting/formatting (rules: E, F, I, N, W; E501 ignored)
- Prefer readable code over cleverness
- Prefer focused patches over broad rewrites

## Branching Strategy

- `main` - Stable releases (bug fixes, docs)
- `nightly` - Experimental features (new features, refactoring)

Target `nightly` for new features and refactoring. Target `main` for bug fixes and documentation.

## Common Environment Variables

- `NANOBOT_MAX_CONCURRENT_REQUESTS` - Max concurrent agent requests (default: 3)
- `NANOBOT_ENABLE_CONTEXT_CONSOLIDATION` - Enable enhanced context consolidation
- `NANOBOT_CONSOLIDATION_MODEL` - Model for context consolidation

## Important File Locations

- Config: `~/.nanobot/config.json`
- Workspace: `~/.nanobot/workspace/` (configurable)
- Sessions: `{workspace}/.sessions/`
- Skills: Built-in at `nanobot/skills/`, custom at `{workspace}/skills/`
- Cron jobs: `{config_dir}/cron/`

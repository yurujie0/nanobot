"""Enhanced session management with turn-based organization."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from nanobot.session.manager import Session as BaseSession, SessionManager as BaseSessionManager


class EnhancedSession(BaseSession):
    """
    Enhanced session with turn-based organization and message metadata.

    Extends base Session with:
    - msg_id: Unique identifier for each message
    - turn_id: Organizes messages into reasoning turns
    - summary: Semantic summary of message content
    - msg_index: Fast lookup by msg_id
    """

    def __init__(self, key: str):
        super().__init__(key=key)
        self.current_turn_id: int = 0
        self.msg_index: dict[str, dict[str, Any]] = {}
        self.turns: list[dict[str, Any]] = []  # Track completed turns

    def generate_msg_id(self, turn_id: int, seq: int) -> str:
        """Generate unique message ID."""
        return f"msg_{turn_id}_{seq}"

    def get_next_turn_id(self) -> int:
        """Get next turn ID and increment counter."""
        turn_id = self.current_turn_id
        self.current_turn_id += 1
        return turn_id

    def add_message(
        self,
        role: str,
        content: str,
        turn_id: Optional[int] = None,
        msg_id: Optional[str] = None,
        summary: Optional[str] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Add a message with enhanced metadata.

        Returns the created message dict.
        """
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "turn_id": turn_id if turn_id is not None else self.current_turn_id,
            "msg_id": msg_id,
            "summary": summary,
            **kwargs
        }
        self.messages.append(msg)
        self.updated_at = datetime.now()

        # Index by msg_id
        if msg_id:
            self.msg_index[msg_id] = msg

        return msg

    def get_message_by_id(self, msg_id: str) -> Optional[dict[str, Any]]:
        """Get message by its ID."""
        return self.msg_index.get(msg_id)

    def get_messages_by_ids(self, msg_ids: list[str]) -> list[dict[str, Any]]:
        """Get messages by their IDs (preserves order)."""
        result = []
        for msg_id in msg_ids:
            msg = self.msg_index.get(msg_id)
            if msg:
                result.append(msg)
        return result

    def get_messages_by_turn(self, turn_id: int) -> list[dict[str, Any]]:
        """Get all messages for a specific turn."""
        return [msg for msg in self.messages if msg.get("turn_id") == turn_id]

    def get_all_summaries(self) -> list[dict[str, str]]:
        """Get all messages with summaries."""
        return [
            {"msg_id": msg["msg_id"], "summary": msg["summary"]}
            for msg in self.messages
            if msg.get("summary") and msg.get("msg_id")
        ]

    def update_message_summary(self, msg_id: str, summary: str) -> bool:
        """Update summary for a message."""
        msg = self.msg_index.get(msg_id)
        if msg:
            msg["summary"] = summary
            return True
        return False

    def complete_turn(self, turn_id: int, goal: str, status: str = "completed") -> None:
        """Mark a turn as completed and save it."""
        turn_messages = self.get_messages_by_turn(turn_id)
        self.turns.append({
            "turn_id": turn_id,
            "goal": goal,
            "status": status,
            "message_count": len(turn_messages),
            "completed_at": datetime.now().isoformat(),
        })


class EnhancedSessionManager(BaseSessionManager):
    """Session manager that creates EnhancedSession instances."""

    def get_or_create(self, key: str) -> EnhancedSession:
        """Get existing session or create new EnhancedSession."""
        if key in self._cache:
            session = self._cache[key]
            # Ensure it's an EnhancedSession
            if isinstance(session, EnhancedSession):
                return session
            # Convert base Session to EnhancedSession if needed

        # Load or create
        session = self._load(key)
        if session is None:
            session = EnhancedSession(key=key)
        elif not isinstance(session, EnhancedSession):
            # Convert loaded base Session to EnhancedSession
            enhanced = EnhancedSession(key=key)
            enhanced.messages = session.messages
            enhanced.created_at = session.created_at
            enhanced.updated_at = session.updated_at
            enhanced.metadata = session.metadata
            enhanced.last_consolidated = session.last_consolidated
            # Rebuild index
            for msg in enhanced.messages:
                msg_id = msg.get("msg_id")
                if msg_id:
                    enhanced.msg_index[msg_id] = msg
            session = enhanced

        self._cache[key] = session
        return session

    def _load(self, key: str) -> Optional[EnhancedSession]:
        """Load session from disk."""
        path = self._get_session_path(key)
        if not path.exists():
            # Try legacy path
            legacy_path = self._get_legacy_session_path(key)
            if legacy_path.exists():
                try:
                    import shutil
                    shutil.move(str(legacy_path), str(path))
                except Exception:
                    pass

        if not path.exists():
            return None

        try:
            messages = []
            metadata = {}
            created_at = None
            last_consolidated = 0
            current_turn_id = 0
            msg_index = {}

            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    data = json.loads(line)

                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        created_at = datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
                        last_consolidated = data.get("last_consolidated", 0)
                        current_turn_id = data.get("current_turn_id", 0)
                    else:
                        messages.append(data)
                        # Build index
                        msg_id = data.get("msg_id")
                        if msg_id:
                            msg_index[msg_id] = data

            session = EnhancedSession(key=key)
            session.messages = messages
            session.msg_index = msg_index
            session.created_at = created_at or datetime.now()
            session.metadata = metadata
            session.last_consolidated = last_consolidated
            session.current_turn_id = current_turn_id

            return session
        except Exception as e:
            from loguru import logger
            logger.warning("Failed to load session {}: {}", key, e)
            return None

    def save(self, session: EnhancedSession) -> None:
        """Save session to disk."""
        path = self._get_session_path(session.key)

        with open(path, "w", encoding="utf-8") as f:
            # Metadata line
            metadata_line = {
                "_type": "metadata",
                "key": session.key,
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
                "metadata": session.metadata,
                "last_consolidated": session.last_consolidated,
                "current_turn_id": session.current_turn_id,
            }
            import json
            f.write(json.dumps(metadata_line, ensure_ascii=False) + "\n")

            # Messages
            for msg in session.messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

        self._cache[session.key] = session

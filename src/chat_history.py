"""История чата — хранит последние N сообщений для каждого пользователя."""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class ChatMessage:
    role: str  # "user" или "assistant"
    content: str
    timestamp: datetime = field(default_factory=datetime.now)


class ChatHistory:
    """In-memory история чата с TTL и лимитом сообщений."""

    def __init__(self, max_messages: int = 20, ttl_minutes: int = 60):
        self.max_messages = max_messages
        self.ttl = timedelta(minutes=ttl_minutes)
        self._history: dict[int, list[ChatMessage]] = defaultdict(list)

    def add(self, user_id: int, role: str, content: str):
        """Добавить сообщение в историю."""
        self._cleanup(user_id)
        self._history[user_id].append(ChatMessage(role=role, content=content))
        # Лимит
        if len(self._history[user_id]) > self.max_messages:
            self._history[user_id] = self._history[user_id][-self.max_messages:]

    def get(self, user_id: int) -> list[dict]:
        """Получить историю в формате OpenAI messages."""
        self._cleanup(user_id)
        return [{"role": m.role, "content": m.content} for m in self._history[user_id]]

    def clear(self, user_id: int):
        """Очистить историю пользователя."""
        self._history.pop(user_id, None)

    def _cleanup(self, user_id: int):
        """Удалить устаревшие сообщения."""
        now = datetime.now()
        self._history[user_id] = [
            m for m in self._history[user_id]
            if now - m.timestamp < self.ttl
        ]

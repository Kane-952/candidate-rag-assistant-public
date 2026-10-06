from collections import OrderedDict
from time import monotonic

from .models.schemas import Message


class ConversationStore:
    """V1 内存会话；API 的锁保护读-回答-写事务。重启后清空。"""
    def __init__(self, settings):
        self.settings = settings
        self.sessions = OrderedDict()

    def get(self, session_id):
        now = monotonic()
        expired = [key for key, (t, _) in self.sessions.items() if now - t > self.settings.session_ttl_seconds]
        for key in expired:
            del self.sessions[key]
        entry = self.sessions.get(session_id)
        return list(entry[1]) if entry else []

    def append_turn(self, session_id, question, answer):
        messages = self.get(session_id)
        messages.extend([Message(role='user', content=question), Message(role='assistant', content=answer)])
        self.sessions[session_id] = (monotonic(), messages[-self.settings.history_messages:])
        self.sessions.move_to_end(session_id)
        while len(self.sessions) > self.settings.max_sessions:
            self.sessions.popitem(last=False)

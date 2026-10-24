from typing import Callable, Dict, Any
import threading
import logging

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self):
        self._subscribers: Dict[str, list[Callable]] = {}
        self._lock = threading.Lock()

    def subscribe(self, event_type: str, callback: Callable):
        with self._lock:
            self._subscribers.setdefault(event_type, []).append(callback)
            callback_name = getattr(callback, "__qualname__", str(callback))
            logger.debug(
                f"Subscribed to event type: {event_type} with callback: {callback_name}"
            )

    def unsubscribe(self, event_type: str, callback: Callable):
        with self._lock:
            callbacks = self._subscribers.get(event_type)
            if not callbacks:
                return
            try:
                callbacks.remove(callback)
            except ValueError:
                return
            if not callbacks:
                del self._subscribers[event_type]

    def subscriber_count(self, event_type: str | None = None) -> int:
        with self._lock:
            if event_type is None:
                return sum(len(callbacks) for callbacks in self._subscribers.values())
            return len(self._subscribers.get(event_type, []))

    def publish(self, event_type: str, data: Dict[str, Any]):
        with self._lock:
            subscribers = self._subscribers.get(event_type, []).copy()

        if not subscribers:
            logger.debug(f"No subscribers for {event_type}")
            return

        logger.debug(f"Emitting event: {event_type} with data: {data}")

        for callback in subscribers:
            try:
                callback(data)
            except Exception as e:
                cb_name = getattr(callback, "__qualname__", str(callback))
                logger.error(
                    f"Error in subscriber {cb_name} for event {event_type}: {e}"
                )


class Event:
    # Committed lifecycle/roster notifications published by the supervisor.
    SERVER_STARTED = "SERVER_STARTED"
    SERVER_STOPPED = "SERVER_STOPPED"
    SERVER_STATUS = "SERVER_STATUS"
    LIFECYCLE_CHANGED = "LIFECYCLE_CHANGED"

    PLAYER_JOINED = "PLAYER_JOINED"
    PLAYER_LEFT = "PLAYER_LEFT"
    BAN_ADDED = "BAN_ADDED"
    BAN_REMOVED = "BAN_REMOVED"

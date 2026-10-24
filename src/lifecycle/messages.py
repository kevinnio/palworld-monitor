"""Shared lifecycle message and process model types.

This module contains data only: no application logic and no imports from the
rest of the application.
"""

import threading
from dataclasses import dataclass
from typing import Optional


class LifecycleState:
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"


@dataclass(frozen=True)
class ProcessIdentity:
    """A process we own, identified by PID and (when available) creation time."""

    pid: int
    create_time: Optional[float] = None

    def matches(self, other: Optional["ProcessIdentity"]) -> bool:
        if other is None or self.pid != other.pid:
            return False
        if self.create_time is not None and other.create_time is not None:
            return abs(self.create_time - other.create_time) < 0.01
        return True


@dataclass(frozen=True)
class ProcessSnapshot:
    """One I/O observation of the tracked and any discovered PalServer process."""

    tracked: Optional[ProcessIdentity]
    tracked_running: bool
    found: Optional[ProcessIdentity]


@dataclass(frozen=True)
class ProcessObservation:
    """A process scan tagged with the generation it started under."""

    generation: int
    snapshot: ProcessSnapshot


@dataclass(frozen=True)
class LaunchRequest:
    generation: int


@dataclass(frozen=True)
class LaunchResult:
    generation: int
    identity: Optional[ProcessIdentity]


@dataclass(frozen=True)
class PollRequest:
    generation: int
    identity: ProcessIdentity
    seq: int


@dataclass(frozen=True)
class RosterSample:
    generation: int
    identity: ProcessIdentity
    seq: int
    players: Optional[list]  # None means the REST API was unavailable.


@dataclass(frozen=True)
class StopRequest:
    generation: int
    identity: ProcessIdentity


@dataclass(frozen=True)
class StopResult:
    generation: int
    identity: Optional[ProcessIdentity]
    stopped: Optional[bool]  # True=gone, False=failed, None=pending shutdown


@dataclass(frozen=True)
class ProcessExit:
    identity: ProcessIdentity


class Command:
    def __init__(self):
        self.done = threading.Event()
        self.result = False


class StartCommand(Command):
    pass


class StopCommand(Command):
    pass

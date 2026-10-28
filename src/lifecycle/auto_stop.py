"""Pure auto-stop deadline, freshness and revalidation policy.

No I/O, publishing, PlayerManager calls or controller access happens here.
The supervisor owns the returned ``AutoStopState`` and applies the decision.
"""

from dataclasses import dataclass, replace
from typing import Optional


class AutoStopDecision:
    NONE = "NONE"
    CANCEL = "CANCEL"
    REVALIDATE = "REVALIDATE"
    STOP = "STOP"


@dataclass(frozen=True)
class AutoStopConfig:
    enabled: bool
    delay: float
    startup_guard: float = 30.0


@dataclass(frozen=True)
class AutoStopState:
    deadline: Optional[float] = None
    generation: Optional[int] = None
    revalidation: Optional[int] = None
    revalidation_seq: Optional[int] = None
    last_valid_sample_at: Optional[float] = None
    last_valid_sample_generation: Optional[int] = None
    last_valid_roster_empty: bool = False


def cancelled(state: AutoStopState) -> AutoStopState:
    """Clear the deadline while keeping the last successful sample record."""
    return replace(
        state,
        deadline=None,
        generation=None,
        revalidation=None,
        revalidation_seq=None,
    )


def on_roster(
    state: AutoStopState,
    *,
    config: AutoStopConfig,
    now: float,
    generation: int,
    player_count: int,
    sample_seq: int,
    started_at: float,
) -> tuple[AutoStopState, str]:
    state = replace(
        state,
        last_valid_sample_at=now,
        last_valid_sample_generation=generation,
        last_valid_roster_empty=player_count == 0,
    )

    if not config.enabled or player_count > 0:
        return cancelled(state), AutoStopDecision.CANCEL

    if now - started_at < config.startup_guard:
        return cancelled(state), AutoStopDecision.CANCEL

    if state.revalidation == generation:
        if sample_seq >= (state.revalidation_seq or 0):
            return cancelled(state), AutoStopDecision.STOP
        return state, AutoStopDecision.REVALIDATE

    if state.deadline is None or state.generation != generation:
        return (
            replace(state, deadline=now + config.delay, generation=generation),
            AutoStopDecision.NONE,
        )
    return state, AutoStopDecision.NONE


def on_unavailable(state: AutoStopState) -> tuple[AutoStopState, str]:
    return AutoStopState(), AutoStopDecision.CANCEL


def on_expiry(
    state: AutoStopState,
    *,
    config: AutoStopConfig,
    now: float,
    generation: int,
    poll_inflight: bool,
    poll_due_at: Optional[float],
    poll_seq: int,
    poll_interval: float,
) -> tuple[AutoStopState, str]:
    if not config.enabled or state.generation != generation or state.deadline is None:
        return cancelled(state), AutoStopDecision.CANCEL

    if state.revalidation == generation:
        return state, AutoStopDecision.NONE

    if _is_fresh_empty_sample(
        state,
        now=now,
        generation=generation,
        poll_inflight=poll_inflight,
        poll_due_at=poll_due_at,
        poll_interval=poll_interval,
    ):
        return cancelled(state), AutoStopDecision.STOP

    return (
        replace(
            state,
            deadline=None,
            generation=None,
            revalidation=generation,
            revalidation_seq=poll_seq + 1,
        ),
        AutoStopDecision.REVALIDATE,
    )


def _is_fresh_empty_sample(
    state: AutoStopState,
    *,
    now: float,
    generation: int,
    poll_inflight: bool,
    poll_due_at: Optional[float],
    poll_interval: float,
) -> bool:
    return (
        state.last_valid_sample_generation == generation
        and state.last_valid_sample_at is not None
        and now - state.last_valid_sample_at <= max(poll_interval * 2, 1.0)
        and state.last_valid_roster_empty
        and not poll_inflight
        and (poll_due_at is None or poll_due_at > now)
    )

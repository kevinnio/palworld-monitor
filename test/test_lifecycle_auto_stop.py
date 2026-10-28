"""Deterministic tests for the pure auto-stop policy."""

from src.lifecycle.auto_stop import (
    AutoStopConfig,
    AutoStopDecision,
    AutoStopState,
    on_expiry,
    on_roster,
    on_unavailable,
)

CONFIG = AutoStopConfig(enabled=True, delay=60, startup_guard=30)


def armed_state(now=131.0, generation=1):
    state, decision = on_roster(
        AutoStopState(),
        config=CONFIG,
        now=now,
        generation=generation,
        player_count=0,
        sample_seq=1,
        started_at=100.0,
    )
    assert decision == AutoStopDecision.NONE
    assert state.deadline == now + 60
    return state


class TestAutoStopPolicy:
    def test_startup_guard_cancels_without_arming(self):
        state, decision = on_roster(
            AutoStopState(),
            config=CONFIG,
            now=110.0,
            generation=1,
            player_count=0,
            sample_seq=1,
            started_at=100.0,
        )

        assert decision == AutoStopDecision.CANCEL
        assert state.deadline is None
        assert state.last_valid_roster_empty is True

    def test_empty_roster_arms_after_guard(self):
        state = armed_state()
        assert state.generation == 1
        assert state.last_valid_sample_at == 131.0
        assert state.last_valid_roster_empty is True

    def test_players_cancel(self):
        state = armed_state()
        state, decision = on_roster(
            state,
            config=CONFIG,
            now=140.0,
            generation=1,
            player_count=1,
            sample_seq=2,
            started_at=100.0,
        )

        assert decision == AutoStopDecision.CANCEL
        assert state.deadline is None
        assert state.last_valid_roster_empty is False

    def test_unavailable_cancels_and_forgets_sample(self):
        state, decision = on_unavailable(armed_state())

        assert decision == AutoStopDecision.CANCEL
        assert state == AutoStopState()

    def test_disabled_config_cancels(self):
        state, decision = on_roster(
            AutoStopState(),
            config=AutoStopConfig(enabled=False, delay=60),
            now=200.0,
            generation=1,
            player_count=0,
            sample_seq=1,
            started_at=100.0,
        )

        assert decision == AutoStopDecision.CANCEL
        assert state.deadline is None

    def test_fresh_empty_sample_stops_on_expiry(self):
        state, _ = on_roster(
            armed_state(),
            config=CONFIG,
            now=190.0,
            generation=1,
            player_count=0,
            sample_seq=2,
            started_at=100.0,
        )
        state, decision = on_expiry(
            state,
            config=CONFIG,
            now=191.0,
            generation=1,
            poll_inflight=False,
            poll_due_at=195.0,
            poll_seq=5,
            poll_interval=2,
        )

        assert decision == AutoStopDecision.STOP
        assert state.deadline is None

    def test_inflight_poll_revalidates_instead_of_stopping(self):
        state = armed_state()
        state, decision = on_expiry(
            state,
            config=CONFIG,
            now=191.0,
            generation=1,
            poll_inflight=True,
            poll_due_at=None,
            poll_seq=5,
            poll_interval=2,
        )

        assert decision == AutoStopDecision.REVALIDATE
        assert state.revalidation == 1
        assert state.revalidation_seq == 6
        assert state.deadline is None

    def test_due_poll_revalidates(self):
        state = armed_state()
        _state, decision = on_expiry(
            state,
            config=CONFIG,
            now=191.0,
            generation=1,
            poll_inflight=False,
            poll_due_at=191.0,
            poll_seq=5,
            poll_interval=2,
        )

        assert decision == AutoStopDecision.REVALIDATE

    def test_stale_sample_revalidates(self):
        state = armed_state()
        _state, decision = on_expiry(
            state,
            config=CONFIG,
            now=191.0,
            generation=1,
            poll_inflight=False,
            poll_due_at=195.0,
            poll_seq=5,
            poll_interval=2,
        )

        # The armed sample was taken at 131; 60s later it is stale.
        assert decision == AutoStopDecision.REVALIDATE

    def test_generation_change_cancels(self):
        state = armed_state(generation=1)
        _state, decision = on_expiry(
            state,
            config=CONFIG,
            now=191.0,
            generation=2,
            poll_inflight=False,
            poll_due_at=195.0,
            poll_seq=5,
            poll_interval=2,
        )

        assert decision == AutoStopDecision.CANCEL

    def test_old_inflight_sample_requests_another(self):
        state, decision = on_expiry(
            armed_state(),
            config=CONFIG,
            now=191.0,
            generation=1,
            poll_inflight=True,
            poll_due_at=None,
            poll_seq=5,
            poll_interval=2,
        )
        assert decision == AutoStopDecision.REVALIDATE

        state, decision = on_roster(
            state,
            config=CONFIG,
            now=192.0,
            generation=1,
            player_count=0,
            sample_seq=5,
            started_at=100.0,
        )

        assert decision == AutoStopDecision.REVALIDATE
        assert state.revalidation == 1

    def test_current_empty_sample_confirms(self):
        state, _ = on_expiry(
            armed_state(),
            config=CONFIG,
            now=191.0,
            generation=1,
            poll_inflight=True,
            poll_due_at=None,
            poll_seq=5,
            poll_interval=2,
        )
        state, decision = on_roster(
            state,
            config=CONFIG,
            now=192.0,
            generation=1,
            player_count=0,
            sample_seq=6,
            started_at=100.0,
        )

        assert decision == AutoStopDecision.STOP
        assert state.deadline is None

    def test_current_non_empty_sample_cancels_revalidation(self):
        state, _ = on_expiry(
            armed_state(),
            config=CONFIG,
            now=191.0,
            generation=1,
            poll_inflight=True,
            poll_due_at=None,
            poll_seq=5,
            poll_interval=2,
        )
        state, decision = on_roster(
            state,
            config=CONFIG,
            now=192.0,
            generation=1,
            player_count=1,
            sample_seq=6,
            started_at=100.0,
        )

        assert decision == AutoStopDecision.CANCEL
        assert state.deadline is None

"""
Tests for the auth module.
"""

from src.auth import User, LoginAttemptTracker


class TestUser:
    """Test suite for User class."""

    def test_user_initialization(self):
        """Test User initialization."""
        user = User("testuser")
        assert user.id == "testuser"
        assert user.username == "testuser"

    def test_get_id(self):
        """Test get_id method."""
        user = User("testuser")
        assert user.get_id() == "testuser"

    def test_user_mixin_compatibility(self):
        """Test that User is compatible with Flask-Login UserMixin."""
        user = User("testuser")
        # UserMixin provides is_authenticated, is_active, is_anonymous
        # Test that these properties work correctly (not just exist)
        assert user.is_authenticated is True
        assert user.is_active is True
        assert user.is_anonymous is False


class TestLoginAttemptTracker:
    """Test suite for LoginAttemptTracker class."""

    def test_initialization(self):
        """Test LoginAttemptTracker initialization."""
        tracker = LoginAttemptTracker(max_attempts=5, lockout_duration=300)
        assert tracker.max_attempts == 5
        assert tracker.lockout_duration == 300
        assert tracker.get_remaining_attempts("192.168.1.1") == 5
        assert tracker.is_locked_out("192.168.1.1") is False

    def test_is_locked_out_false_initially(self):
        """Test that IP is not locked out initially."""
        tracker = LoginAttemptTracker()
        assert tracker.is_locked_out("192.168.1.1") is False

    def test_record_failed_attempt(self):
        """Test recording a failed login attempt."""
        tracker = LoginAttemptTracker(max_attempts=3)
        tracker.record_failed_attempt("192.168.1.1")
        assert tracker.get_remaining_attempts("192.168.1.1") == 2
        assert tracker.is_locked_out("192.168.1.1") is False

    def test_multiple_failed_attempts(self):
        """Test recording multiple failed attempts."""
        tracker = LoginAttemptTracker(max_attempts=3)

        tracker.record_failed_attempt("192.168.1.1")
        tracker.record_failed_attempt("192.168.1.1")
        tracker.record_failed_attempt("192.168.1.1")

        assert tracker.get_remaining_attempts("192.168.1.1") == 0
        assert tracker.is_locked_out("192.168.1.1") is True

    def test_lockout_after_max_attempts(self):
        """Test that IP gets locked out after max attempts."""
        tracker = LoginAttemptTracker(max_attempts=3, lockout_duration=300)

        tracker.record_failed_attempt("192.168.1.1")
        tracker.record_failed_attempt("192.168.1.1")
        assert tracker.is_locked_out("192.168.1.1") is False

        tracker.record_failed_attempt("192.168.1.1")
        assert tracker.is_locked_out("192.168.1.1") is True

    def test_record_successful_login_clears_attempts(self):
        """Test that successful login clears failed attempts."""
        tracker = LoginAttemptTracker(max_attempts=3)

        tracker.record_failed_attempt("192.168.1.1")
        tracker.record_failed_attempt("192.168.1.1")
        assert tracker.get_remaining_attempts("192.168.1.1") == 1

        tracker.record_successful_login("192.168.1.1")
        assert tracker.get_remaining_attempts("192.168.1.1") == 3
        assert tracker.is_locked_out("192.168.1.1") is False

    def test_record_successful_login_clears_lockout(self):
        """Test that successful login clears lockout."""
        tracker = LoginAttemptTracker(max_attempts=2, lockout_duration=300)

        tracker.record_failed_attempt("192.168.1.1")
        tracker.record_failed_attempt("192.168.1.1")
        assert tracker.is_locked_out("192.168.1.1") is True

        tracker.record_successful_login("192.168.1.1")
        assert tracker.is_locked_out("192.168.1.1") is False

    def test_get_remaining_attempts(self):
        """Test getting remaining attempts."""
        tracker = LoginAttemptTracker(max_attempts=5)

        assert tracker.get_remaining_attempts("192.168.1.1") == 5

        tracker.record_failed_attempt("192.168.1.1")
        assert tracker.get_remaining_attempts("192.168.1.1") == 4

        tracker.record_failed_attempt("192.168.1.1")
        assert tracker.get_remaining_attempts("192.168.1.1") == 3

    def test_get_remaining_attempts_when_locked_out(self):
        """Test that remaining attempts is 0 when locked out."""
        tracker = LoginAttemptTracker(max_attempts=2)

        tracker.record_failed_attempt("192.168.1.1")
        tracker.record_failed_attempt("192.168.1.1")

        assert tracker.get_remaining_attempts("192.168.1.1") == 0

    def test_get_lockout_time_remaining(self):
        """Test getting lockout time remaining."""
        tracker = LoginAttemptTracker(max_attempts=2, lockout_duration=300)

        tracker.record_failed_attempt("192.168.1.1")
        tracker.record_failed_attempt("192.168.1.1")

        remaining = tracker.get_lockout_time_remaining("192.168.1.1")
        assert remaining is not None
        assert 0 < remaining <= 300

    def test_get_lockout_time_remaining_when_not_locked(self):
        """Test that lockout time remaining is None when not locked."""
        tracker = LoginAttemptTracker()
        assert tracker.get_lockout_time_remaining("192.168.1.1") is None

    def test_lockout_expires(self):
        """Test that lockout expires after duration."""
        tracker = LoginAttemptTracker(max_attempts=2, lockout_duration=1)

        tracker.record_failed_attempt("192.168.1.1")
        tracker.record_failed_attempt("192.168.1.1")
        assert tracker.is_locked_out("192.168.1.1") is True

        # Wait for lockout to expire
        import time

        time.sleep(1.1)

        assert tracker.is_locked_out("192.168.1.1") is False

    def test_old_attempts_are_cleaned_up(self):
        """Test that old attempts are cleaned up."""
        tracker = LoginAttemptTracker(max_attempts=5, lockout_duration=1)

        # Record an attempt
        tracker.record_failed_attempt("192.168.1.1")

        # Wait for it to expire
        import time

        time.sleep(1.1)

        # Record another attempt - should clean up the old one
        tracker.record_failed_attempt("192.168.1.1")

        # Only the new attempt counts, not the expired one.
        assert tracker.get_remaining_attempts("192.168.1.1") == 4

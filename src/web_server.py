# Copyright (c) 2024 Nomomo
# Copyright (c) 2026 Kevin Perez - Modified work
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

import os
import sys
import socket
import json
import queue
import re
from flask import Flask, render_template, request, jsonify, redirect, url_for, Response
from flask_login import (
    LoginManager,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from datetime import timedelta
from src.settings import settings
from src.palworld_control import PalWorldController
from src.events import EventBus, Event
from src.auth import User, LoginAttemptTracker
import logging
import threading


class WebServer:
    def __init__(
        self,
        palworld_controller: PalWorldController,
        bus: EventBus,
    ):
        """Initialize the web server with a PalWorld controller instance.

        Args:
            palworld_controller: Instance of PalWorldController to handle server operations
            bus: Notification bus carrying committed lifecycle/roster state
        """
        self.palworld_controller = palworld_controller
        self.bus = bus

        if getattr(sys, "frozen", False):
            base = sys._MEIPASS
        else:
            base = os.path.dirname(__file__)
        self.app = Flask(
            __name__,
            template_folder=os.path.join(base, "templates"),
            static_folder=os.path.join(base, "static"),
        )
        self.ip = "Unknown"

        self._lock = threading.RLock()
        self.snapshot = self._read_snapshot()
        self._subscribed = False

        # Configure session
        self.app.secret_key = settings.sessionSecretKey
        self.app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
            seconds=settings.sessionTimeout
        )

        # Initialize CSRF protection
        self.csrf = CSRFProtect(self.app)

        # Initialize rate limiter
        if settings.rateLimitEnabled:
            self.limiter = Limiter(
                app=self.app,
                key_func=get_remote_address,
                storage_uri="memory://",
                default_limits=[
                    f"{settings.rateLimitRequests} per {settings.rateLimitWindow} seconds"
                ],
            )

        # Initialize Flask-login
        self.login_manager = LoginManager()
        self.login_manager.init_app(self.app)
        self.login_manager.login_view = "login"
        self.login_manager.login_message = "Please log in to access this page."

        # Initialize login attempt tracker
        self.login_tracker = LoginAttemptTracker(
            max_attempts=settings.maxLoginAttempts,
            lockout_duration=settings.lockoutDuration,
        )

        # Register user loader
        @self.login_manager.user_loader
        def load_user(user_id):
            if user_id == settings.webUsername:
                return User(user_id)
            return None

        # Register custom filters
        self._register_filters()

        # Register routes
        self._register_routes()

        self._sse_clients: list[queue.Queue] = []
        self._sse_lock = threading.RLock()

        self._subscribe()

    # ------------------------------------------------------------------
    # Committed state
    # ------------------------------------------------------------------

    def _subscribe(self):
        if self._subscribed:
            return
        self.bus.subscribe(Event.SERVER_STARTED, self._on_committed)
        self.bus.subscribe(Event.SERVER_STOPPED, self._on_committed)
        self.bus.subscribe(Event.SERVER_STATUS, self._on_committed)
        self.bus.subscribe(Event.LIFECYCLE_CHANGED, self._on_committed)
        self.bus.subscribe(Event.BAN_ADDED, self._on_committed)
        self.bus.subscribe(Event.BAN_REMOVED, self._on_committed)
        self._subscribed = True

    def close(self):
        if not self._subscribed:
            return
        self.bus.unsubscribe(Event.SERVER_STARTED, self._on_committed)
        self.bus.unsubscribe(Event.SERVER_STOPPED, self._on_committed)
        self.bus.unsubscribe(Event.SERVER_STATUS, self._on_committed)
        self.bus.unsubscribe(Event.LIFECYCLE_CHANGED, self._on_committed)
        self.bus.unsubscribe(Event.BAN_ADDED, self._on_committed)
        self.bus.unsubscribe(Event.BAN_REMOVED, self._on_committed)
        self._subscribed = False

    def _read_snapshot(self) -> dict:
        snapshot = dict(self.palworld_controller.get_snapshot())
        snapshot["revision"] = snapshot.get("revision", 0)
        snapshot["players"] = list(snapshot.get("players", []))
        snapshot["banned_players"] = list(snapshot.get("banned_players", []))
        return snapshot

    def _refresh_snapshot(self) -> dict:
        """Take one consistent snapshot and cache it for this notification."""
        with self._lock:
            snapshot = self._read_snapshot()
            if snapshot["revision"] >= self.snapshot.get("revision", 0):
                self.snapshot = snapshot
            return snapshot

    def _on_committed(self, data=None):
        # Serialize refresh+broadcast so concurrent notifications and ban
        # request threads cannot emit snapshots out of revision order.
        with self._lock:
            snapshot = self._read_snapshot()
            if snapshot["revision"] < self.snapshot.get("revision", 0):
                return
            self.snapshot = snapshot
            self._broadcast_sse(self._payload(snapshot))

    def _payload(self, snapshot: dict) -> dict:
        return {
            "data": dict(snapshot),
            "autoStopDelay": round(settings.autoStopDelay),
        }

    def _register_filters(self):
        """Register custom Jinja2 filters."""

        @self.app.template_filter("datetime")
        def format_datetime(timestamp):
            """Format timestamp to readable datetime string."""
            try:
                if isinstance(timestamp, (int, float)):
                    from datetime import datetime

                    return datetime.fromtimestamp(timestamp).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                else:
                    return str(timestamp)
            except Exception:
                return str(timestamp)

    def _register_routes(self):
        """Register Flask routes with the application."""

        @self.app.route("/login", methods=["GET", "POST"])
        @self.csrf.exempt
        def login():
            return self._handle_login()

        @self.app.route("/logout", methods=["POST"])
        @login_required
        def logout():
            logout_user()
            return redirect(url_for("login"))

        @self.app.route("/")
        @login_required
        def index():
            return self._handle_index()

        @self.app.route("/action", methods=["POST"])
        @login_required
        def web_server_action():
            return self._handle_action()

        @self.app.route("/kick", methods=["POST"])
        @login_required
        def kick_player():
            return self._handle_kick()

        @self.app.route("/ban", methods=["POST"])
        @login_required
        def ban_player():
            return self._handle_ban()

        @self.app.route("/unban", methods=["POST"])
        @login_required
        def unban_player():
            return self._handle_unban()

        @self.app.route("/banned", methods=["GET"])
        @login_required
        def get_banned_players():
            return self._handle_get_banned()

        @self.app.route("/stream")
        def stream():
            if not current_user.is_authenticated:
                return jsonify(error="Unauthorized"), 401
            return self._handle_stream()

    # ------------------------------------------------------------------
    # SSE helpers
    # ------------------------------------------------------------------

    def _broadcast_sse(self, payload):
        """Push *payload* to every connected SSE client."""
        text = f"data: {json.dumps(payload)}\n\n"
        with self._sse_lock:
            dead = []
            for q in self._sse_clients:
                try:
                    q.put_nowait(text)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._sse_clients.remove(q)

    def _handle_stream(self):
        """Return a streaming response that pushes server-status events."""
        q: queue.Queue = queue.Queue(maxsize=32)
        with self._lock:
            # Capture the initial snapshot before the queue can receive events,
            # all under the same lock as refresh/broadcast.
            snapshot = self._read_snapshot()
            if snapshot["revision"] >= self.snapshot.get("revision", 0):
                self.snapshot = snapshot
            with self._sse_lock:
                self._sse_clients.append(q)
            initial_payload = self._payload(snapshot)

        def generate():
            try:
                yield f"data: {json.dumps(initial_payload)}\n\n"

                while True:
                    try:
                        msg = q.get(timeout=30)
                        yield msg
                    except queue.Empty:
                        # Send a keepalive comment so the connection isn't dropped
                        yield ": keepalive\n\n"
            finally:
                with self._sse_lock:
                    try:
                        self._sse_clients.remove(q)
                    except ValueError:
                        pass

        return Response(generate(), mimetype="text/event-stream")

    def _handle_login(self):
        """Handle login page and authentication."""
        ip_address = request.remote_addr

        # Check if locked out
        if self.login_tracker.is_locked_out(ip_address):
            remaining = self.login_tracker.get_lockout_time_remaining(ip_address)
            return render_template(
                "login.html",
                error=f"Too many failed attempts. Try again in {remaining} seconds.",
                locked_out=True,
            )

        if request.method == "POST":
            username = request.form.get("username")
            password = request.form.get("password")
            remember = request.form.get("remember") == "on"

            if username == settings.webUsername and password == settings.webPassword:
                user = User(username)
                login_user(
                    user,
                    remember=remember,
                    duration=timedelta(days=7 if remember else 0),
                )
                self.login_tracker.record_successful_login(ip_address)

                next_page = request.args.get("redirect", "/")
                return redirect(next_page)
            else:
                self.login_tracker.record_failed_attempt(ip_address)
                remaining = self.login_tracker.get_remaining_attempts(ip_address)

                logging.warning(
                    f"Failed login attempt from {ip_address} for user {username}"
                )

                error_msg = "Invalid credentials."
                warning_msg = None

                if remaining == 0:
                    error_msg = "Too many failed attempts. Account locked."
                elif remaining <= 2:
                    warning_msg = f"{remaining} attempts remaining before lockout"

                return render_template(
                    "login.html", error=error_msg, warning=warning_msg
                )

        return render_template("login.html")

    def _get_server_ip(self):
        """Get the server IP address."""
        try:
            ip = socket.gethostbyname(socket.gethostname())
            ip = ip + ":" + str(settings.webServerPort)
            return ip
        except Exception as e:
            logging.warning(f"Error while getting server IP: {e}")
            return "unknown"

    def _json_state(self, **extra):
        """Build a JSON response dict from one consistent committed snapshot."""
        return jsonify(
            data=self._refresh_snapshot(),
            autoStopDelay=round(settings.autoStopDelay),
            **extra,
        )

    def _handle_index(self):
        """Handle the main page route."""
        snapshot = self._refresh_snapshot()
        current_server_info = dict(snapshot)
        if settings.showServerIPAddress:
            current_server_info["IPAddress"] = self._get_server_ip()
        else:
            current_server_info["IPAddress"] = "Unknown"

        theme = request.cookies.get("theme", "light")
        if theme not in ["light", "dark"]:
            theme = "light"

        return render_template(
            "index.html",
            controlServerThroughWeb=settings.controlServerThroughWeb,
            showServerIPAddress=settings.showServerIPAddress,
            autoStopEnabled=settings.autoStop,
            data=current_server_info,
            autoStopDelay=round(settings.autoStopDelay),
            initialTheme=theme,
            git_hash=settings.get_git_hash(),
            current_user=current_user,
        )

    def _handle_action(self):
        """Handle server action requests."""
        action = request.form.get("action")
        logging.info(
            f"Server action '{action}' by {current_user.username} from {request.remote_addr}"
        )
        if action == "startServer":
            self.palworld_controller.start_server()
        elif action == "stopServer":
            self.palworld_controller.stop_server()
        return self._json_state()

    def _handle_kick(self):
        """Handle player kick request."""
        steam_id = request.form.get("steam_id")
        if not steam_id or not re.fullmatch(r"[A-Za-z0-9_:-]{1,128}", steam_id):
            return jsonify(success=False, message="A valid Steam ID is required"), 400
        logging.info(
            f"Kicked player {steam_id} by {current_user.username} from {request.remote_addr}"
        )
        success = self.palworld_controller.kick_player(steam_id)
        return self._json_state(
            success=success,
            message=f"Player {'kicked successfully' if success else 'kick failed'}",
        )

    def _handle_ban(self):
        """Handle player ban request."""
        steam_id = request.form.get("steam_id")
        if not steam_id or not re.fullmatch(r"[A-Za-z0-9_:-]{1,128}", steam_id):
            return jsonify(success=False, message="A valid Steam ID is required"), 400
        logging.info(
            f"Banned player {steam_id} by {current_user.username} from {request.remote_addr}"
        )
        success = self.palworld_controller.ban_player(steam_id)
        return self._json_state(
            success=success,
            message=f"Player {'banned successfully' if success else 'ban failed'}",
        )

    def _handle_unban(self):
        """Handle player unban request."""
        steam_id = request.form.get("steam_id")
        if not steam_id or not re.fullmatch(r"[A-Za-z0-9_:-]{1,128}", steam_id):
            return jsonify(success=False, message="A valid Steam ID is required"), 400
        logging.info(
            f"Unbanned player {steam_id} by {current_user.username} from {request.remote_addr}"
        )
        success = self.palworld_controller.unban_player(steam_id)
        return self._json_state(
            success=success,
            message=f"Player {'unbanned successfully' if success else 'unban failed'}",
        )

    def _handle_get_banned(self):
        """Handle request to get list of banned players."""
        snapshot = self._refresh_snapshot()
        return jsonify(success=True, banned_players=list(snapshot["banned_players"]))

    def run(self):
        """Start the web server.  Blocks the calling thread."""
        logging.info(
            f"Web server start - listening on 0.0.0.0:{settings.webServerPort}"
        )
        self.app.run(host="0.0.0.0", port=settings.webServerPort, debug=False)

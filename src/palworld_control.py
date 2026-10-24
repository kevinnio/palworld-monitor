"""Public application facade for Palworld lifecycle control."""

import logging

from src.banlist_manager import BanlistManager
from src.events import EventBus
from src.lifecycle.messages import StartCommand, StopCommand
from src.lifecycle.runtime import LifecycleRuntime
from src.lifecycle.supervisor import Supervisor
from src.player_manager import PlayerManager


class PalWorldController:
    def __init__(
        self,
        client,
        process_manager,
        player_manager: PlayerManager,
        banlist_manager: BanlistManager,
        bus: EventBus,
        clock=None,
    ):
        self.client = client
        self.process_manager = process_manager
        self.player_manager = player_manager
        self.banlist_manager = banlist_manager
        self.bus = bus
        self._supervisor = Supervisor(
            process_manager=process_manager,
            player_manager=player_manager,
            banlist_manager=banlist_manager,
            bus=bus,
            clock=clock,
        )
        self._runtime = LifecycleRuntime(
            supervisor=self._supervisor,
            client=client,
            process_manager=process_manager,
        )

    def start(self):
        """Start the supervisor and its workers. Idempotent."""
        self._runtime.start()

    def close(self):
        """Stop the workers and release process resources. Idempotent."""
        self._runtime.close()

    def start_server(self):
        logging.info("Palworld server start command received")
        return self._runtime.submit_command(StartCommand())

    def stop_server(self):
        logging.info("Palworld server stop command received")
        return self._runtime.submit_command(StopCommand())

    @property
    def generation(self) -> int:
        return self._supervisor.generation

    @property
    def state(self) -> str:
        return self._supervisor.state

    def get_snapshot(self) -> dict:
        return self._supervisor.get_snapshot()

    def get_banned_players(self):
        return self.banlist_manager.get_banned_players()

    def is_player_banned(self, steam_id):
        return self.banlist_manager.is_banned(steam_id)

    def kick_player(self, steam_id):
        try:
            return self.client.kick_player({"steam_id": steam_id})
        except Exception as e:
            logging.error(f"Error kicking {steam_id}: {e}")
            return False

    def ban_player(self, steam_id):
        try:
            success = self.client.ban_player({"steam_id": steam_id})
            if success:
                return self.banlist_manager.add_ban(steam_id)

            return success
        except Exception as e:
            logging.error(f"Error banning {steam_id}: {e}")
            return False

    def unban_player(self, steam_id):
        try:
            success = self.client.unban_player({"steam_id": steam_id})
            if success:
                return self.banlist_manager.remove_ban(steam_id)

            return success
        except Exception as e:
            logging.error(f"Error unbanning {steam_id}: {e}")
            return False

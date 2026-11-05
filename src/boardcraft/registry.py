"""Explicit game registration without dynamic discovery."""
from collections.abc import Iterable

from .contracts import GameInfo, GameModule
from .games.dice_circuit.module import DiceCircuitModule
from .games.rentcraft.module import RentcraftModule


class GameRegistry:
    def __init__(self, modules: Iterable[GameModule]) -> None:
        self._modules: dict[str, GameModule] = {}
        for module in modules:
            if module.game_id in self._modules:
                raise ValueError(f'Duplicate game ID: {module.game_id}')
            self._modules[module.game_id] = module

    def get_game(self, game_id: str) -> GameModule:
        return self._modules[game_id]

    def list_games(self) -> tuple[GameInfo, ...]:
        return tuple(module.info() for module in self._modules.values())


_registry = GameRegistry((DiceCircuitModule(), RentcraftModule()))


def get_game(game_id: str) -> GameModule:
    return _registry.get_game(game_id)


def list_games() -> tuple[GameInfo, ...]:
    return _registry.list_games()

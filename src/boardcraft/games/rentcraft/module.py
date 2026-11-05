"""Rentcraft play metadata, settings and shared English resources."""
from dataclasses import asdict
from copy import deepcopy
from typing import Mapping

from boardcraft.contracts import GameConfig, GameInfo, PlayCapabilities
from boardcraft.settings import normalize_settings
from .board import (AUCTION_MAX_BID, BID_STEPS, BOARD, GO_SALARY, GROUPS, JAIL_FINE, RAILROAD_RENTS,
                    TOTAL_HOUSES, TOTAL_HOTELS, UTILITY_MULTIPLIERS, resource)
from .cards import DECKS
from .state import GameConfig as RuleSettings
from .text import describe_action
from .bots import BOT_DESCRIPTIONS


def integer(title, minimum, default, order, presets=None):
    field = {"type": "integer", "title": title, "minimum": minimum, "default": default, "order": order}
    if presets:
        field["presets"] = [{"value": value, "label": str(value)} for value in presets]
    return field


# `order` and `presets` only shape the "New table" form: one row of fields with a few suggested values each.
SETTINGS = {"type": "object", "additionalProperties": False, "properties": {
    "start_cash": integer("Starting cash", 0, 1500, 1),
    "max_rounds": {"type": ["integer", "null"], "title": "Round limit", "minimum": 1, "default": None, "order": 5,
                   "presets": [{"value": None, "label": "no limit"}, {"value": 100, "label": "100"},
                               {"value": 200, "label": "200"}]},
    "max_trades_per_turn": integer("Trades per turn", 0, 3, 6, (1, 3, 5)),
    "max_trade_exchanges": integer("Offers per negotiation", 0, 4, 7, (2, 4, 6)),
    "trade_windows": {"type": "string", "title": "Offer windows", "enum": ["off", "payments", "full"],
                      "enumLabels": {"off": "off", "payments": "on payments", "full": "full"},
                      "default": "full", "order": 3,
                      "description": "Before a payment, after buying a space, after a trade and at the end of a turn "
                                     "the other players may each propose a trade in turn."},
}}


def game_settings(config: GameConfig, seed: int) -> RuleSettings:
    if config.game_id != "rentcraft" or config.variant != "base" or not 2 <= config.players <= 4:
        raise ValueError("Rentcraft requires the base variant and two to four players")
    if type(seed) is not int:
        raise ValueError("A game seed must be an integer")
    overrides = normalize_settings(SETTINGS, config.settings)
    settings = {name: spec["default"] for name, spec in SETTINGS["properties"].items()}
    return RuleSettings(config.players, seed, **{**settings, **overrides})


class RentcraftModule:
    game_id = "rentcraft"
    description: str = 'Buy, auction and trade properties, build sets and manage cash to outlast the other players.'
    bot_descriptions: Mapping[str, str] = BOT_DESCRIPTIONS
    rules_version = engine_version = observation_version = action_version = "1"

    def info(self):
        return GameInfo(self.game_id, self.rules_version, self.engine_version, "Rentcraft",
                        ("base",), 2, 4, tuple(item.kind for item in self.bot_info()), PlayCapabilities(neural_inference=self._profiles_ready(), parameterized_actions=True, seat_operations=True,
                                                             stateful_bots=True))

    def simulation_defaults(self):
        return {"max_rounds": 200}

    def decode_result(self, data: dict, players: int):
        from .engine import RentcraftResult
        if not isinstance(data, dict) or set(data) != {"scores", "placements", "reason", "details"}:
            raise ValueError("Invalid Rentcraft record result fields")
        for key in ("scores", "placements"):
            if (type(data[key]) is not list or len(data[key]) != players
                    or any(type(v) is not int for v in data[key])):
                raise ValueError("Invalid Rentcraft record player results")
        details = data["details"]
        if (sorted(data["placements"]) != list(range(1, players + 1))
                or not isinstance(details, dict) or set(details) != {"winner", "rounds", "by_round_limit"}
                or type(details["winner"]) is not int or details["winner"] != data["placements"].index(1)
                or type(details["rounds"]) is not int or details["rounds"] < 1
                or type(details["by_round_limit"]) is not bool
                or data["reason"] != ("round_limit" if details["by_round_limit"] else "last_player")):
            raise ValueError("Invalid Rentcraft record outcome")
        return RentcraftResult(tuple(data["scores"]), tuple(data["placements"]), data["reason"], dict(details))

    def settings_schema(self):
        return deepcopy(SETTINGS)

    def new(self, config: GameConfig, seed: int):
        from .engine import RentcraftEngine
        return RentcraftEngine.new(config, seed)

    def restore(self, snapshot: dict):
        from .engine import RentcraftEngine
        return RentcraftEngine.restore(snapshot)

    def board_spec(self):
        return {"spaces": [{**asdict(s), "kind": s.kind.value, "rents": list(s.rents),
                            "mortgage_value": s.mortgage_value} for s in BOARD],
                "cards": {name: [asdict(c) for c in cards] for name, cards in DECKS.items()},
                "houses": TOTAL_HOUSES, "hotels": TOTAL_HOTELS, "start_salary": GO_SALARY,
                "jail_fine": JAIL_FINE, "station_rents": list(RAILROAD_RENTS),
                "utility_multipliers": list(UTILITY_MULTIPLIERS), "bid_steps": list(BID_STEPS),
                "maximum_bid": AUCTION_MAX_BID}

    def sheet_spec(self):
        return self.board_spec()

    def rules_text(self):
        return resource("content/rules.en.md")

    def bot_info(self):
        from .bots import BUILTINS
        return BUILTINS

    def bot_settings_schema(self, kind):
        from .bots.settings import settings_schema
        return settings_schema(kind)

    def bot(self, kind, seed, *, options=None):
        from .bots.adapter import RentcraftBot
        return RentcraftBot(kind, seed, {} if options is None else options)

    def _profiles_ready(self):
        from .inference.profiles import MINI, V7
        try:
            return all(self.inference_profile(name, 1).id == name for name in (MINI, V7))
        except (ValueError, ImportError, AttributeError):
            return False

    def inference_profile(self, id, version):
        from .inference.profiles import resolve_profile
        return resolve_profile(id, version)

    def inference_bot(self, profile, policy, seed):
        from .inference.bot import RentcraftInferenceBot
        return RentcraftInferenceBot(profile, policy, seed)

    def encoder(self):
        return None

    describe_action = staticmethod(describe_action)

    def decision_context(self, engine, viewer):
        """Facts for the viewer's current decision: the bank financing of a debt the viewer must raise funds for."""
        debt = engine.debt_summary(viewer)
        return {"debt": debt} if debt else None

    def table_round(self, engine):
        """The round in play and the round limit (None without one), for the table list."""
        return engine.game.state.round, engine.game.state.config.max_rounds

    def replay_metrics(self, observation):
        """Public summary of one replay position: net worth, complete colour sets, hotels and bankruptcy per seat."""
        data = observation.data
        owner, mortgaged, buildings = data['owner'], data['mortgaged'], data['buildings']
        worth, sets, hotels = [], [], []
        for player in data['players']:
            pid, total = player['id'], player['cash']
            for i, o in enumerate(owner):
                if o == pid:
                    total += BOARD[i].price // 2 if mortgaged[i] else BOARD[i].price
                    total += buildings[i] * BOARD[i].house_cost
            worth.append(total)
            sets.append(sum(all(owner[i] == pid for i in members) for members in GROUPS.values()))
            hotels.append(sum(1 for i, o in enumerate(owner) if o == pid and buildings[i] == 5))
        return {'net_worth': worth, 'sets': sets, 'hotels': hotels, 'bankrupt': [p['bankrupt'] for p in data['players']]}


"""Settings are derived from the profile and strategy of the selected player."""
from dataclasses import fields, replace

from boardcraft.settings import normalize_settings
from ..board import GROUPS
from .absurd import ABSURD_BASES, make_absurd
from .bonjour import (AUCTIONS, CALCULATOR_VARIANTS, RESISTANT, VARIANTS, WINDOWS,
                      AuctionBonjourBot, BonjourBot, CalculatorBonjourBot, ResistantBonjourBot, WindowBonjourBot)
from .calculator import CalculatorBot
from .controller import ControllerBot
from .fast_simulator import FastSimulatorBot
from .gpt_style import GptStyleBot
from .master import MasterBot
from .profiles import PROFILES
from .random_bot import RandomBot
from .rule_bot import RuleBot
from .simulator import SimulatorBot
from .sol_sets import SolSetsBot
from .tactician import TacticianBot
from .wary import WaryBot

STRATEGIES = {"calculator": CalculatorBot, "wary-calculator": WaryBot, "controller": ControllerBot,
              "master": MasterBot, "gpt-style": GptStyleBot, "tactician": TacticianBot, "sol-sets": SolSetsBot}
SEEDED = {"simulator": SimulatorBot, "fast-simulator": FastSimulatorBot, "random": RandomBot}
BONJOUR = {key: factory for family, factory in (
    (VARIANTS, BonjourBot), (RESISTANT, ResistantBonjourBot), (WINDOWS, WindowBonjourBot),
    (AUCTIONS, AuctionBonjourBot), (CALCULATOR_VARIANTS, CalculatorBonjourBot)) for key in family}


def strategy(kind, seed):
    if kind in STRATEGIES:
        return STRATEGIES[kind]()
    if kind in SEEDED:
        return SEEDED[kind](seed)
    if kind in BONJOUR:
        return BONJOUR[kind](kind)
    if kind in ABSURD_BASES:
        return make_absurd(kind, seed)
    if kind in ("miser", "investor", "collector", "cautious"):
        return RuleBot(PROFILES[kind])
    raise ValueError("Unknown Rentcraft bot kind")


def parameters(bot):
    values = {}
    for group in ("profile", "params"):
        obj = getattr(bot, group, None)
        if obj is not None:
            values.update({f.name: (group, getattr(obj, f.name)) for f in fields(obj) if f.name not in ("id", "name")})
    return values


def field_schema(name, value):
    prop = {"title": name.replace("_", " ").capitalize(), "default": value}
    if type(value) is tuple:
        if name in ("preferred_groups", "avoid", "high_priority", "low_priority"):
            groups = list(GROUPS)
            if name in ("high_priority", "low_priority"):
                groups.extend(("railroad", "utility"))
            items = {"type": "string", "enum": groups}
        else:
            items = {"type": "number"}
            if name == "price_steps":
                items["minimum"] = 0
        prop.update(type="array", items=items, default=list(value))
        if name in ("station_bonuses", "utility_bonuses"):
            prop["minItems"] = 1
    elif type(value) is bool:
        prop["type"] = "boolean"
    else:
        low, high = BOUNDS[name]
        prop.update(type="integer" if type(value) is int else "number", minimum=low, maximum=high)
    return prop


def _schema(values):
    return {"type": "object", "additionalProperties": False,
            "properties": {name: field_schema(name, value) for name, (_, value) in values.items()}}


def settings_schema(kind):
    return _schema(parameters(strategy(kind, 0)))


def configured_strategy(kind, seed, options):
    bot = strategy(kind, seed)
    values = parameters(bot)
    overrides = normalize_settings(_schema(values), options)
    for group in ("profile", "params"):
        chosen = {k: tuple(v) if isinstance(v, list) else v for k, v in overrides.items() if values[k][0] == group}
        if chosen:
            setattr(bot, group, replace(getattr(bot, group), **chosen))
    if hasattr(bot, "reseed"):
        bot.reseed(seed)
    return bot, overrides



BOUNDS = {
    "trade_variation": (0, 1),
    "random_move_probability": (0, 1),
    "buy_reserve": (0, 3000),
    "build_reserve": (0, 3000),
    "max_bid_factor": (0, 5),
    "preferred_bid_factor": (0, 5),
    "build_target": (0, 5),
    "raise_share": (0, 1),
    "feed_ratio": (0, 5),
    "bid_jump": (0, 2),
    "build_buffer": (0, 5),
    "min_reserve": (0, 3000),
    "max_reserve": (0, 5000),
    "worst_share": (0, 2),
    "complete_factor": (0, 5),
    "block_factor": (0, 5),
    "rail_factor": (0, 5),
    "utility_factor": (0, 5),
    "mortgage_gain_ratio": (0, 10),
    "unmortgage_margin": (0, 3000),
    "jail_unowned": (0, 28),
    "jail_exp": (0, 1000),
    "swap_factor": (0, 5),
    "starter_factor": (0, 5),
    "next_factor": (0, 5),
    "starter_left": (0, 2000),
    "threat_left": (0, 2000),
    "build_money_share": (0, 5),
    "horizon": (1, 200),
    "worth_ratio": (0, 5),
    "jail_stay": (0, 2000),
    "jail_stay_strong": (0, 2000),
    "near_share": (0, 5),
    "far_share": (0, 5),
    "rail_share": (0, 5),
    "progress_factor": (0, 5),
    "go_increment": (0, 3000),
    "blocking_limit": (0, 10),
    "blocking_limit_with_set": (0, 10),
    "willing_seller_threshold": (0, 5),
    "neutral_seller_threshold": (0, 5),
    "reluctant_seller_threshold": (0, 5),
    "repeat_cash_change": (0, 5000),
    "opening_reserve": (0, 3000),
    "developed_reserve": (0, 3000),
    "threat_reserve": (0, 3000),
    "minimum_cash": (0, 3000),
    "cash_share": (0, 1),
    "minimum_buffer": (0, 3000),
    "buffer_share": (0, 2),
    "offensive_cash_floor": (0, 3000),
    "knockout_threshold": (0, 1),
    "income_horizon": (0, 100),
    "nearby_rent_weight": (0, 1),
    "baseline_rent_weight": (0, 1),
    "asset_weight": (0, 5),
    "knockout_weight": (0, 10000),
    "reserve_gap_weight": (0, 20),
    "liquidation_risk_weight": (0, 20000),
    "partner_gain_weight": (0, 10),
    "offer_gain_threshold": (0, 1000),
    "rival_risk_threshold": (0, 1),
    "rescue_factor": (0, 1),
    "threat_percentile": (0, 1),
    "strong_rent": (0, 2000),
    "blocking_bonus_cap": (0, 5000),
    "blocking_share": (0, 5),
    "weak_blocking_share": (0, 5),
    "weak_blocking_cap": (0, 5000),
    "urgent_card_value": (0, 500),
    "acquisition_card_value": (0, 500),
    "developed_card_value": (0, 500),
    "acquisition_unowned": (0, 28),
    "cash_grid": (1, 1000),
    "offer_skeletons": (0, 1000),
    "planning_states": (1, 10000),
    "ordinary_search_factor": (0, 20),
    "key_search_factor": (0, 20),
    "concession": (0, 1),
    "retry_cash_change": (0, 5000),
    "rescue_package": (1, 5),
    "rival_property_estimate": (0, 20),
    "last_property_estimate": (0, 20),
    "auction_block_factor": (0, 5),
    "cheap_property": (0, 1000),
    "bail_cash_floor": (0, 3000),
    "endgame_rounds": (0, 50),
    "management_steps": (1, 1000),
    "early_reserve": (0, 2000),
    "shortage": (0, 32),
    "rich_builds": (0, 20),
    "orange_factor": (0, 5),
    "preferred_factor": (0, 5),
    "avoid_factor": (0, 5),
    "max_level": (1, 5),
    "blockade": (0, 32),
    "blockade_reserve": (0, 3000),
}

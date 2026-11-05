"""Prioritize orange and other high-return groups while controlling the supply of houses."""
from dataclasses import dataclass

from ..board import BOARD, GROUPS, HOTEL, Kind
from ..actions import Action, ActionKind, Decision
from ..state import GameState
from ..buildings import unmortgage_cost
from . import Choice, describe_trade
from .profiles import PROFILES
from .rule_bot import ROLL, RuleBot, _completes_group, _early_game, _in_full_group
from .trading import proposals

PROFILE = PROFILES['master']


BUILD_PRIORITY = ("orange", "red", "light_blue", "yellow", "pink", "dark_blue", "green", "brown")

AVOID = ("dark_blue",)
EARLY_RESERVE = 50
SHORTAGE = 8
BLOCKADE = 10


@dataclass(frozen=True)
class MasterParams:
    early_reserve: int = EARLY_RESERVE
    shortage: int = SHORTAGE

    rich_builds: int = 4

    complete_factor: float = 2.0
    block_factor: float = 2.0
    orange_factor: float = 1.5
    preferred_factor: float = 1.4
    rail_factor: float = 1.3
    avoid_factor: float = 0.8

    avoid: tuple[str, ...] = AVOID
    max_level: int = HOTEL - 1

    blockade: int = BLOCKADE
    blockade_reserve: int = EARLY_RESERVE


MASTER_PARAMS = MasterParams()


def rivals_need_houses(state: GameState, me: int, can_pay: bool = False) -> bool:
    owner, players = state.owner, state.players
    for spaces in GROUPS.values():
        o = owner[spaces[0]]
        if o is None or o == me or players[o].bankrupt or any(owner[i] != o or state.mortgaged[i] for i in spaces):
            continue
        if all(state.buildings[i] >= HOTEL - 1 for i in spaces):
            continue
        if can_pay and players[o].cash < BOARD[spaces[0]].house_cost:
            continue
        return True
    return False


def _blocks_rival(state: GameState, me: int, space: int) -> bool:
    group = BOARD[space].group
    if group is None:
        return False
    others = [state.owner[i] for i in GROUPS[group] if i != space]
    return bool(others) and others[0] is not None and others[0] != me and all(o == others[0] for o in others)


class MasterBot(RuleBot):
    def __init__(self):
        super().__init__(PROFILE)
        self.params = MASTER_PARAMS

    def _buy(self, state: GameState, d: Decision) -> Choice:
        me = d.player
        space = state.pending_buy
        s = BOARD[space]
        left = state.players[me].cash - s.price
        buy, decline = Action(ActionKind.BUY), Action(ActionKind.DECLINE)
        if buy not in d.actions:
            return Choice(decline, 'Decline. I cannot afford it')
        if _completes_group(state, me, space) and left >= 0:
            return Choice(buy, 'Buy to complete a buildable set')
        if _blocks_rival(state, me, space) and left >= self.params.early_reserve:
            return Choice(buy, 'Buy to prevent a rival from completing a set')
        if s.group in self.params.avoid:
            return Choice(decline, 'Decline a rarely visited street and keep cash for houses elsewhere')
        if _early_game(state):
            if left >= self.params.early_reserve:
                return Choice(buy, f'Buy an early bargaining asset, leaving ${left}')
            return Choice(decline, f'Decline. Remaining cash would be only ${left}')
        if s.kind is Kind.RAILROAD and left >= self.params.early_reserve:
            return Choice(buy, 'Buy the station. Stations offer the best return without buildings')
        if s.group in self.profile.preferred_groups and left >= self.params.early_reserve:
            return Choice(buy, 'Buy. Orange, red and light blue houses offer the best returns')
        if s.kind is not Kind.UTILITY and left >= self.profile.buy_reserve:
            return Choice(buy, f'Buy, leaving ${left}, preserving the reserve')
        return Choice(decline, f'Decline. Cash after buying would be ${left}, and my late-game reserve is ${self.profile.buy_reserve}')

    def _value_factor(self, state: GameState, me: int, space: int) -> tuple[float, str]:
        s = BOARD[space]
        if _completes_group(state, me, space):
            return self.params.complete_factor, 'completes my set'
        if _blocks_rival(state, me, space):
            return self.params.block_factor, "blocks a rival's set"
        if s.group == "orange":
            return self.params.orange_factor, 'orange streets are visited most often after jail'
        if s.group in self.profile.preferred_groups:
            return self.params.preferred_factor, 'good return on houses'
        if s.kind is Kind.RAILROAD:
            return self.params.rail_factor, 'stations earn rent without houses'
        if s.group in self.params.avoid:
            return self.params.avoid_factor, 'the most expensive streets are the least visited'
        return 1.0, 'ordinary property'

    def _auction(self, state: GameState, d: Decision) -> Choice:
        a = state.auction
        factor, why = self._value_factor(state, d.player, a.space)
        limit = min(int(BOARD[a.space].price * factor), self._auction_cash(state, d.player) - self.params.early_reserve)
        bid = self._bid(state, d, limit)
        if bid is not None:
            return Choice(bid, f'Raise the bid to ${a.high_bid + bid.amount}: {why} (limit ${limit})')
        return Choice(Action(ActionKind.PASS_BID), f'Pass. Above my limit of ${limit} ({why})')

    def _jail(self, state: GameState, d: Decision) -> Choice | None:
        if _early_game(state):
            for a, why in (
                (Action(ActionKind.USE_JAIL_CARD), 'Use a jail release card to acquire properties early in the game'),
                (Action(ActionKind.PAY_JAIL_FINE), 'Pay the jail fine to acquire properties early in the game'),
            ):
                if a in d.actions:
                    return Choice(a, why)
            return None
        return Choice(ROLL, 'Stay in jail. Avoid paying rent while my properties earn income')

    def _manage(self, state: GameState, d: Decision) -> Choice | None:
        me = d.player
        p = state.players[me]
        acts = d.actions
        for a in acts:
            if a.kind is ActionKind.UNMORTGAGE and _in_full_group(state, me, a.space):
                if p.cash - unmortgage_cost(a.space) >= 2 * self.profile.build_reserve:
                    return Choice(a, f'Repay the mortgage on {BOARD[a.space].name}. Restores my set')
        if not any(a.kind is ActionKind.BUILD for a in acts):
            return None
        shortage = state.bank_houses <= self.params.shortage
        rich = p.cash >= self.params.rich_builds * self.profile.build_reserve

        on = self.params.blockade > 0
        blockade = on and state.bank_houses <= self.params.blockade and rivals_need_houses(state, me, can_pay=True)

        def rank(a: Action):
            g = BOARD[a.space].group
            return (state.buildings[a.space], BUILD_PRIORITY.index(g) if g in BUILD_PRIORITY else 99, a.space)

        safety = self._build_floor(state, me, acts)
        normal = max(self.profile.build_reserve, safety)
        floor = max(self.params.blockade_reserve, safety) if blockade else normal
        top = self.params.max_level
        if top >= HOTEL and on and rivals_need_houses(state, me):
            top = HOTEL - 1
        builds = [a for a in acts if a.kind is ActionKind.BUILD and state.buildings[a.space] < top
                  and p.cash - BOARD[a.space].house_cost >= floor]

        to3 = [a for a in builds if state.buildings[a.space] < 3]
        if to3:
            a = min(to3, key=rank)
            if p.cash - BOARD[a.space].house_cost < normal:
                return Choice(a, f'Build {state.buildings[a.space] + 1} houses on {BOARD[a.space].name}. House shortage. The bank has {state.bank_houses} houses and a rival has a buildable set')
            return Choice(a, f'Build {state.buildings[a.space] + 1} houses on {BOARD[a.space].name}. Build to three houses for the largest rent increase')

        to4 = [a for a in builds if state.buildings[a.space] >= 3]
        if to4 and (rich or shortage or blockade):
            a = min(to4, key=rank)
            if blockade:
                why = (f'House shortage. The bank has {state.bank_houses} houses and a rival can build. I take the houses first')
            else:
                why = 'Few houses remain. Take them before rivals can' if shortage else 'cash is plentiful'
            if state.buildings[a.space] == HOTEL - 1:
                return Choice(a, f'Build a hotel on {BOARD[a.space].name}: {why}')
            return Choice(a, f'Build the fourth house on {BOARD[a.space].name}: {why} (four houses restrict the bank supply)')
        return None

    def _candidates(self, state: GameState, pid: int) -> list:
        offers = proposals(state, pid, self.params.early_reserve)

        def key(o):
            orange = any(BOARD[s].group == "orange" for s in o.get_spaces)
            return (not orange, state.players[o.target].cash)

        return sorted(offers, key=key)

    def _proposal_reason(self, state: GameState, offer, names: list[str]) -> str:
        poor = state.players[offer.target].cash < 300
        tail = ' , a good time to offer cash to this player' if poor else ""
        return f'Propose a trade to complete my set{tail}: {describe_trade(offer, names)}'

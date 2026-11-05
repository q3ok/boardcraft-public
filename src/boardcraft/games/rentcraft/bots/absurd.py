"""Unfavorable trade proposals layered over the player's normal policy."""
import random

from ..board import BOARD, BUYABLE, GROUPS
from ..actions import Action, ActionKind, Decision, Phase
from ..state import GameState
from ..text import seat_label
from . import Choice, describe_trade
from ..trade import TradeOffer, validate_offer
from .trading import trade_score

KINDS = ('cash for nothing', 'property for pennies', 'card for nothing', 'junk for a fortune')
MIN_BADNESS = -100


def _tradeable(state: GameState, space: int, who: int) -> bool:
    if state.owner[space] != who:
        return False
    group = BOARD[space].group
    return not (group and any(state.buildings[j] for j in GROUPS[group]))


def absurd_offer(state: GameState, pid: int, rng: random.Random) -> tuple[TradeOffer, str] | None:
    rivals = [i for i, p in enumerate(state.players) if i != pid and not p.bankrupt]
    me = state.players[pid]
    for _ in range(16):
        if not rivals:
            return None
        target = rng.choice(rivals)
        them = state.players[target]
        kind = rng.choice(KINDS)
        offer = None
        if kind == 'cash for nothing' and them.cash >= 25:
            amount = rng.choice([25, 50, 100, 150, 200, 300, 500])
            offer = TradeOffer(pid, target, get_cash=min(amount, them.cash // 25 * 25))
        elif kind == 'property for pennies':
            theirs = [s for s in BUYABLE if _tradeable(state, s, target)]
            if theirs:
                sp = rng.choice(theirs)
                price = max(10, int(BOARD[sp].price * rng.uniform(0.05, 0.3)) // 10 * 10)
                if me.cash >= price:
                    offer = TradeOffer(pid, target, get_spaces=(sp,), give_cash=price)
        elif kind == 'card for nothing' and them.jail_cards:
            offer = TradeOffer(pid, target, get_cards=1)
        elif kind == 'junk for a fortune':
            mine = [s for s in BUYABLE if _tradeable(state, s, pid)]
            if mine and them.cash >= 100:
                sp = min(mine, key=lambda s: (not state.mortgaged[s], BOARD[s].price))
                ask = min(them.cash // 10 * 10, int(BOARD[sp].price * rng.uniform(2.0, 4.0)) // 10 * 10)
                offer = TradeOffer(pid, target, give_spaces=(sp,), get_cash=ask)

        if offer is not None and validate_offer(state, offer) is None and trade_score(state, offer, target) < MIN_BADNESS:
            return offer, kind
    return None


class AbsurdOffers:
    per_turn = 1

    def _absurd_init(self, label: str, seed: int) -> None:
        self.label = label
        self._rng = random.Random(seed)
        self._absurd_sent: tuple[int, set] = (-1, set())

    def _turn(self, state: GameState, d: Decision) -> Choice:
        phases = (Phase.POST_ROLL, Phase.PRE_ROLL)[: self.per_turn]
        turn, sent = self._absurd_sent
        if turn != state.turn:
            sent = set()
            self._absurd_sent = (state.turn, sent)
        if d.phase in phases and d.phase.value not in sent and state.trades_this_turn < state.config.max_trades_per_turn:
            sent.add(d.phase.value)
            made = absurd_offer(state, d.player, self._rng)
            if made is not None:
                offer, kind = made
                names = [seat_label(i) for i in range(len(state.players))]
                return Choice(Action(ActionKind.PROPOSE_TRADE, trade=offer),
                              f'One-sided offer ({kind}): {describe_trade(offer, names)}')
        return super()._turn(state, d)

    def _window(self, state: GameState, d: Decision) -> Choice:
        turn, sent = self._absurd_sent
        if turn != state.turn:
            sent = set()
            self._absurd_sent = (state.turn, sent)
        if 'window' not in sent:
            sent.add('window')
            made = absurd_offer(state, d.player, self._rng)
            if made is not None:
                offer, kind = made
                names = [seat_label(i) for i in range(len(state.players))]
                return Choice(Action(ActionKind.PROPOSE_TRADE, trade=offer),
                              f'One-sided offer during the trade window ({kind}): {describe_trade(offer, names)}')
        return super()._window(state, d)


class AbsurdOffersBonjour(AbsurdOffers):
    def _window(self, state: GameState, d: Decision) -> Choice:
        c = super(AbsurdOffers, self)._window(state, d)
        if c.action.kind is not ActionKind.PASS:
            return c
        turn, sent = self._absurd_sent
        if turn != state.turn:
            sent = set()
            self._absurd_sent = (state.turn, sent)
        if 'window' not in sent:
            sent.add('window')
            made = absurd_offer(state, d.player, self._rng)
            if made is not None:
                offer, kind = made
                names = [seat_label(i) for i in range(len(state.players))]
                return Choice(Action(ActionKind.PROPOSE_TRADE, trade=offer),
                              f'One-sided offer during the trade window ({kind}): {describe_trade(offer, names)}')
        return c


ABSURD_BASES: dict[str, str] = {
    'absurd-gpt-style': 'gpt-style',
    'absurd-calculator': 'calculator',
    'absurd-investor': 'investor',
    'hustler': 'calculator',
    'absurd-wary-calculator': 'wary-calculator',
    'absurd-tactician': 'tactician',
    'absurd-resistant-bonjour-b': 'resistant-bonjour-b',
    'absurd-resistant-bonjour-c': 'resistant-bonjour-c',
    'absurd-bonjour-mixed-auctions': 'bonjour-mixed-auctions',
}


def make_absurd(bot_id: str, seed: int):
    from .calculator import CalculatorBot

    if bot_id == 'hustler':
        class Hustler(AbsurdOffers, CalculatorBot):
            per_turn = 2

        bot = Hustler()
    elif bot_id == 'absurd-calculator':
        class AbsurdCalculator(AbsurdOffers, CalculatorBot):
            pass

        bot = AbsurdCalculator()
    elif bot_id == 'absurd-gpt-style':
        from .gpt_style import GptStyleBot

        class AbsurdSol(AbsurdOffers, GptStyleBot):
            pass

        bot = AbsurdSol()
    elif bot_id == 'absurd-investor':
        from .profiles import PROFILES
        from .rule_bot import RuleBot

        class AbsurdInvestor(AbsurdOffers, RuleBot):
            pass

        bot = AbsurdInvestor(PROFILES['investor'])
    elif bot_id == 'absurd-wary-calculator':
        from .wary import WaryBot

        class AbsurdWary(AbsurdOffers, WaryBot):
            pass

        bot = AbsurdWary()
    elif bot_id == 'absurd-tactician':
        from .tactician import TacticianBot

        class AbsurdTactician(AbsurdOffers, TacticianBot):
            pass

        bot = AbsurdTactician()
    elif bot_id in ('absurd-resistant-bonjour-b', 'absurd-resistant-bonjour-c'):
        from .bonjour import ResistantBonjourBot

        class AbsurdResistantBonjour(AbsurdOffersBonjour, ResistantBonjourBot):
            pass

        bot = AbsurdResistantBonjour(ABSURD_BASES[bot_id])
    elif bot_id == 'absurd-bonjour-mixed-auctions':
        from .bonjour import AuctionBonjourBot

        class AbsurdAuctionBonjour(AbsurdOffersBonjour, AuctionBonjourBot):
            pass

        bot = AbsurdAuctionBonjour(ABSURD_BASES[bot_id])
    else:
        raise ValueError(f'Unknown bot {bot_id}.')
    bot._absurd_init(bot_id, seed)
    if hasattr(bot, "name"):
        from .profiles import PROFILES

        bot.name = PROFILES[bot_id].name
    return bot

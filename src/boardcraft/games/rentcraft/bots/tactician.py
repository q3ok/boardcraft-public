"""Require a positive trade gain and protect the mortgage value of outgoing deeds."""
from dataclasses import replace

from ..board import BOARD
from ..actions import Action, ActionKind, Decision
from ..state import GameState
from . import Choice
from .gpt_style import GPT_STYLE_PARAMS, GptStyleBot
from .profiles import PROFILES


def below_mortgage(state: GameState, offer, me: int) -> bool:
    give_sp, get_sp = (offer.give_spaces, offer.get_spaces) if me == offer.proposer else (offer.get_spaces, offer.give_spaces)
    get_cash, give_cash = (offer.get_cash, offer.give_cash) if me == offer.proposer else (offer.give_cash, offer.get_cash)
    get_cards = offer.get_cards if me == offer.proposer else offer.give_cards
    floor = sum(BOARD[s].mortgage_value for s in give_sp if not state.mortgaged[s])
    if not floor:
        return False
    received = get_cash - give_cash + 50 * get_cards + sum(BOARD[s].mortgage_value for s in get_sp)
    return received < floor


TACTICIAN_PARAMS = replace(GPT_STYLE_PARAMS, min_reserve=200, worth_ratio=0.8)


class TacticianBot(GptStyleBot):
    def __init__(self):
        super().__init__()
        self.profile = PROFILES['tactician']
        self.params = TACTICIAN_PARAMS
        self.label = 'tactician'

    def _worth_it(self, mine: float, theirs: float, shift: float = 0.0) -> bool:
        return mine > 0 and super()._worth_it(mine, theirs, shift)

    def _respond(self, state: GameState, d: Decision) -> Choice:
        if below_mortgage(state, state.trade.offer, d.player):
            return Choice(Action(ActionKind.REJECT_TRADE), 'Reject. The bank would lend more on these properties')
        return super()._respond(state, d)

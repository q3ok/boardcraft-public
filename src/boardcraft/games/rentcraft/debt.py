"""Charges, bankruptcy, creditor shares and net worth."""

import math
from dataclasses import dataclass

from .actions import Event, Phase
from .board import BOARD, HOTEL, MORTGAGE_INTEREST
from .cards import JAIL_FREE_ID
from .state import Debt, GameState


@dataclass(frozen=True)
class GameResult:
    winner: int
    ranking: tuple[int, ...]
    rounds: int
    by_round_limit: bool
    net_worth: tuple[int, ...]


def charge(state: GameState, debtor: int, creditor: int | None, amount: int) -> list[Event]:
    if amount <= 0:
        return []
    p = state.players[debtor]
    if p.cash >= amount:
        p.cash -= amount
        _credit(state, creditor, amount)
        return [Event("pay", debtor, {"to": creditor, "amount": amount})]
    state.debts.append(Debt(debtor, creditor, amount))
    state.phase = Phase.RAISE_FUNDS
    return [Event("debt", debtor, {"to": creditor, "amount": amount})]


def _credit(state: GameState, creditor: int | None, amount: int) -> None:
    if creditor is not None and not state.players[creditor].bankrupt:
        state.players[creditor].cash += amount


def pay_debt(state: GameState, debt: Debt) -> Event:
    state.players[debt.debtor].cash -= debt.amount
    _credit(state, debt.creditor, debt.amount)
    return Event("pay", debt.debtor, {"to": debt.creditor, "amount": debt.amount})


def interest(space: int) -> int:
    return math.ceil(BOARD[space].mortgage_value * MORTGAGE_INTEREST)


def _sell_buildings(state: GameState, debtor: int, owned: list[int]) -> None:
    p = state.players[debtor]
    for i in owned:
        level = state.buildings[i]
        if level == 0:
            continue
        if level == HOTEL:
            state.bank_hotels += 1
        else:
            state.bank_houses += level
        p.cash += level * (BOARD[i].house_cost // 2)
        state.buildings[i] = 0


def _to_bank(state: GameState, debtor: int, owned: list[int]) -> None:
    p = state.players[debtor]
    for i in owned:
        state.owner[i] = None
        state.mortgaged[i] = False
    state.pending_auctions.extend(owned)
    for deck in p.jail_cards:
        state.decks[deck].append(JAIL_FREE_ID[deck])


def _finish_bankrupt(state: GameState, debtor: int) -> None:
    p = state.players[debtor]
    p.jail_cards = []
    p.cash = 0
    p.bankrupt = True
    state.bankrupt_count += 1
    p.bankrupt_order = state.bankrupt_count
    state.debts = [d for d in state.debts if d.debtor != debtor]


def declare_bankruptcy(state: GameState, debtor: int, creditor: int | None) -> list[Event]:
    p = state.players[debtor]
    events: list[Event] = []
    owned = [i for i, o in enumerate(state.owner) if o == debtor]
    _sell_buildings(state, debtor, owned)
    if creditor is not None and not state.players[creditor].bankrupt:
        heir = state.players[creditor]
        heir.cash += p.cash
        for i in owned:
            state.owner[i] = creditor
        heir.jail_cards.extend(p.jail_cards)
        events.append(Event("bankrupt", debtor, {"to": creditor, "spaces": owned}))
        mortgaged = [i for i in owned if state.mortgaged[i]]
        state.pending_takeovers.extend((creditor, i) for i in mortgaged)
    else:
        _to_bank(state, debtor, owned)
        events.append(Event("bankrupt", debtor, {"to": None, "spaces": owned}))
    _finish_bankrupt(state, debtor)
    return events


def split_shares(cash: int, owed: dict, order: list[int]) -> dict:
    total = sum(owed.values())
    cash = max(0, min(cash, total))
    shares = {c: (cash * a // total if total else 0) for c, a in owed.items()}
    rest = cash - sum(shares.values())
    players = [c for c in order if c in owed]
    while rest > 0:
        short = [c for c in players if shares[c] < owed[c]]
        if not short:
            break
        for c in short:
            if rest == 0:
                break
            shares[c] += 1
            rest -= 1
    return shares


def split_bankruptcy(state: GameState, debtor: int) -> list[Event]:
    p = state.players[debtor]
    events: list[Event] = []
    owned = [i for i, o in enumerate(state.owner) if o == debtor]
    _sell_buildings(state, debtor, owned)
    owed: dict = {}
    for d in state.debts:
        if d.debtor == debtor:
            key = d.creditor if d.creditor is not None and not state.players[d.creditor].bankrupt else None
            owed[key] = owed.get(key, 0) + d.amount
    if not owed:
        owed[None] = 0
    n = len(state.players)
    order = [(debtor + k) % n for k in range(1, n)]
    shares = split_shares(p.cash, owed, order)
    _to_bank(state, debtor, owned)
    events.append(Event("bankrupt", debtor, {"to": None, "spaces": owned, "creditors": dict(shares)}))
    for c in [x for x in order if x in owed] + ([None] if None in owed else []):
        if shares[c] > 0:
            p.cash -= shares[c]
            _credit(state, c, shares[c])
            events.append(Event("pay", debtor, {"to": c, "amount": shares[c], "reason": "bankruptcy"}))
    _finish_bankrupt(state, debtor)
    return events


def debt_creditors(state: GameState, debtor: int) -> list:
    out = []
    for d in state.debts:
        key = d.creditor if d.creditor is not None and not state.players[d.creditor].bankrupt else None
        if d.debtor == debtor and key not in out:
            out.append(key)
    return out


def net_worth(state: GameState, player: int) -> int:
    total = state.players[player].cash
    for i, o in enumerate(state.owner):
        if o != player:
            continue
        s = BOARD[i]
        total += s.price // 2 if state.mortgaged[i] else s.price
        total += state.buildings[i] * s.house_cost
    return total

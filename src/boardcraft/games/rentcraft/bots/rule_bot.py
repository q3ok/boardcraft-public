"""Shared play and trade decisions for players with distinct cash and bidding profiles."""
from ..board import BOARD, BUYABLE, GROUPS, Kind
from ..actions import Action, ActionKind, Decision, Phase
from ..state import GameState
from ..buildings import unmortgage_cost
from ..trade import TradeOffer, validate_offer
from . import Choice, describe_trade, variation
from ..text import seat_label
from .profiles import BotProfile
from .safety import (
    RESCUE_MARGIN, bid_step, build_floor, feed_price, feeds_rival, liquidation_after, owed_by, rescue_price,
    rescue_shortfall,
)
from .trading import (
    buy_raise_mortgage, counter_for, debtor_sales, field_value, field_worth_raising, liquidation_cash, payer_purchases, proposals,
    raise_mortgage, raise_sales, spending_cash, takeover_payoff, takeover_set_field, trade_score,
)

ROLL = Action(ActionKind.ROLL)
END_TURN = Action(ActionKind.END_TURN)
PASS = Action(ActionKind.PASS)
DECLARE_BANKRUPTCY = Action(ActionKind.DECLARE_BANKRUPTCY)

WINDOW_MANAGE_LIMIT = 12


_BASE_RENT = {Kind.RAILROAD: 25, Kind.UTILITY: 10}


def _base_rent(space: int) -> int:
    s = BOARD[space]
    return s.rents[0] if s.kind is Kind.PROPERTY else _BASE_RENT[s.kind]


def _completes_group(state: GameState, player: int, space: int) -> bool:
    group = BOARD[space].group
    if group is None:
        return False
    return all(state.owner[i] == player for i in GROUPS[group] if i != space)


def _early_game(state: GameState) -> bool:
    return sum(1 for i in BUYABLE if state.owner[i] is None) >= 8


def _in_full_group(state: GameState, player: int, space: int) -> bool:
    group = BOARD[space].group
    return group is not None and all(state.owner[i] == player for i in GROUPS[group])


class RuleBot:
    def __init__(self, profile: BotProfile):
        self.profile = profile
        self.label = profile.id

        self._raising = self._win_slot = None
        self._win_steps = 0
        self._tried: set = set()
        self._tried_owners: tuple = ()

        self._variation_rng = variation.new_rng(0)

    def reseed(self, seed: int) -> None:
        self._variation_rng = variation.new_rng(seed)

    def choose(self, state: GameState, decision: Decision) -> Choice:
        if decision.phase is Phase.TRADE_RESPONSE:
            rescue = self._rescue_response(state, decision)
            if rescue is not None:
                return rescue
        eps = self.profile.random_move_probability
        if eps > 0 and self._variation_rng.random() < eps:
            a = variation.random_action(state, decision, self._variation_rng, lambda a: self._refused(state, decision, a))
            if a is not None:
                return Choice(a, f'Random move (random move probability {eps:g})')
        return self._decide(state, decision)

    def _refused(self, state: GameState, d: Decision, a: Action) -> bool:
        return a.kind is ActionKind.ACCEPT_TRADE and feeds_rival(state, state.trade.offer, d.player, self.profile.feed_ratio)

    def _decide(self, state: GameState, decision: Decision) -> Choice:
        phase = decision.phase
        if phase is Phase.BUY_DECISION:
            return self._buy_or_raise(state, decision)
        if phase is Phase.AUCTION:
            return self._auction(state, decision)
        if phase is Phase.TRADE_RESPONSE:
            return self._respond(state, decision)
        if phase is Phase.RAISE_FUNDS:
            sale = self._debtor_sale(state, decision)
            if sale is not None:
                return sale
            if DECLARE_BANKRUPTCY in decision.actions:
                return Choice(DECLARE_BANKRUPTCY, 'No player will buy my properties and the bank offers no more funds')
            return self._raise_funds(state, decision)
        if phase is Phase.TRADE_WINDOW:
            return self._window(state, decision)
        if phase is Phase.MORTGAGE_TAKEOVER:
            return self._takeover(state, decision)
        return self._turn(state, decision)

    def _takeover_reserve(self, state: GameState, me: int) -> int:
        return max(300, self.profile.buy_reserve + self.profile.build_reserve)

    def _takeover(self, state: GameState, d: Decision) -> Choice:
        me = d.player
        space = state.pending_takeovers[0][1]
        name = BOARD[space].name
        payoff = Action(ActionKind.UNMORTGAGE, space=space)
        payoff = payoff if payoff in d.actions else None
        reserve = self._takeover_reserve(state, me)
        if payoff is not None and takeover_payoff(state, me, space, reserve):
            return Choice(payoff, f'Repay the mortgage on {name} now. This property belongs to my set and delaying would cost another 10%')
        if takeover_set_field(state, me, space):
            need = unmortgage_cost(space) + reserve // 2
            if payoff is not None and state.players[me].cash >= need:
                return Choice(payoff, f'Repay the mortgage on {name} now. The mortgage prevents me from developing my set')
            m = raise_mortgage(state, me, need, space, d.actions)
            if m is not None:
                return Choice(m, f'Mortgage {BOARD[m.space].name} to repay the mortgage on {name}. The mortgage prevents me from building on this set')
            sale = self._raise_sale(state, me, need, space, f'repay the mortgage on {name} (a property in my full set)')
            if sale is not None:
                return sale
        return Choice(PASS, 'Pay only interest and keep cash for buildings and rent')

    def _raise_sale(self, state: GameState, me: int, need: int, space: int, goal: str) -> Choice | None:
        offer = self._first_untried(state, raise_sales(state, me, need, space))
        if offer is None:
            return None
        names = [seat_label(i) for i in range(len(state.players))]
        return Choice(Action(ActionKind.PROPOSE_TRADE, trade=offer),
                      f'Mortgages are insufficient. Sell a property to {goal}. {describe_trade(offer, names)}')

    def _wanted(self, state: GameState, player: int, space: int) -> bool:
        return BOARD[space].group in self.profile.preferred_groups or _completes_group(state, player, space)

    def _buy_or_raise(self, state: GameState, d: Decision) -> Choice:
        space = state.pending_buy
        me = d.player
        buy = Action(ActionKind.BUY)
        price = BOARD[space].price
        if buy not in d.actions or getattr(self, "_raising", None) == (state.turn, space):
            if field_worth_raising(state, me, space):
                if buy in d.actions:
                    return Choice(buy, f'Buy {BOARD[space].name}. Important property within mortgage capacity')
                m = buy_raise_mortgage(state, me, space, d.actions)
                if m is not None:
                    self._raising = (state.turn, space)
                    return Choice(m, f'Mortgage {BOARD[m.space].name} to buy {BOARD[space].name} for ${price}. The property advances a set or blocks a rival')
                sale = self._raise_sale(state, me, price, space, f'buy {BOARD[space].name} for ${price} (advances a set or blocks a rival)')
                if sale is not None:
                    self._raising = (state.turn, space)
                    return sale
        return self._buy(state, d)

    def _buy(self, state: GameState, d: Decision) -> Choice:
        prof = self.profile
        space = state.pending_buy
        price = BOARD[space].price
        left = state.players[d.player].cash - price
        buy = Action(ActionKind.BUY)
        if buy in d.actions:
            if left >= prof.buy_reserve:
                return Choice(buy, f'Buy while maintaining a reserve of ${prof.buy_reserve} preserved')

            if _early_game(state) and left >= prof.buy_reserve // 2:
                return Choice(buy, f'Buy early, leaving ${left} (half the reserve is sufficient)')
            if self._wanted(state, d.player, space) and left >= prof.buy_reserve // 2:
                return Choice(buy, f'Buy an important color group, leaving ${left} (half the reserve is sufficient)')
        return Choice(
            Action(ActionKind.DECLINE),
            f'Decline. Cash after buying would be ${left}, below my reserve of ${prof.buy_reserve}',
        )

    def _auction(self, state: GameState, d: Decision) -> Choice:
        prof = self.profile
        a = state.auction
        factor = prof.preferred_bid_factor if self._wanted(state, d.player, a.space) else prof.max_bid_factor
        limit = min(int(BOARD[a.space].price * factor), self._auction_cash(state, d.player) - prof.buy_reserve // 2)
        bid = self._bid(state, d, limit)
        if bid is not None:
            return Choice(bid, f'Raise the bid to ${a.high_bid + bid.amount} (my limit ${limit})')
        return Choice(Action(ActionKind.PASS_BID), f'Pass. Price above my limit of ${limit}')

    def _bid(self, state: GameState, d: Decision, limit: int) -> Action | None:
        return bid_step(state, d.player, limit, d.actions, self.profile.bid_jump)

    def _auction_cash(self, state: GameState, me: int) -> int:
        return spending_cash(state, me, self.profile.raise_share)

    def _debtor_sale(self, state: GameState, d: Decision) -> Choice | None:
        sales = debtor_sales(state, d.player, bank_choice=self._raise_funds)
        owed = sum(x.amount for x in state.debts if x.debtor == d.player)
        if liquidation_cash(state, d.player) >= owed:
            sales = [o for o in sales if not feeds_rival(state, o, d.player, self.profile.feed_ratio)
                     and self._offer_ok(state, o)]
        else:
            sales = self._rescue_sales(state, d.player, sales)
        if not sales:
            return None
        offer = sales[0]
        names = [seat_label(i) for i in range(len(state.players))]
        space = offer.give_spaces[0]
        bank = 0 if state.mortgaged[space] else BOARD[space].mortgage_value
        why = 'Bank financing falls short of the debt' if liquidation_cash(state, d.player) < owed else 'I would have to sell buildings to raise cash at the bank'
        return Choice(
            Action(ActionKind.PROPOSE_TRADE, trade=offer),
            f'{why}. Selling to a player yields more than the bank (${bank}): {describe_trade(offer, names)}',
        )

    def _rescue_sales(self, state: GameState, me: int, sales: list) -> list:
        owed = owed_by(state, me)
        out = []
        for o in sales:
            sp = o.give_spaces[0]
            ask = rescue_price(state, me, sp)
            if o.get_cash > ask:
                o = TradeOffer(me, o.target, give_spaces=o.give_spaces, get_cash=ask)
            covers = liquidation_after(state, o, me) >= owed
            out.append((not covers, field_value(state, me, sp), o.get_cash, sp, o.target, o))
        out.sort(key=lambda t: t[:5])
        return [t[-1] for t in out]

    def _rescue_response(self, state: GameState, d: Decision) -> Choice | None:
        me = d.player
        short = rescue_shortfall(state, me)
        if short <= 0:
            return None
        offer = state.trade.offer
        owed = owed_by(state, me)
        after = liquidation_after(state, offer, me)
        accept = Action(ActionKind.ACCEPT_TRADE)
        if after >= owed and accept in d.actions:
            return Choice(accept, f'Accept to avoid bankruptcy (shortfall ${short} above bank financing), and this trade covers the debt')
        other = offer.proposer
        target = me == offer.target
        gives, gets = (offer.get_spaces, offer.give_spaces) if target else (offer.give_spaces, offer.get_spaces)
        cash_in, cash_out = (offer.give_cash, offer.get_cash) if target else (offer.get_cash, offer.give_cash)
        net = cash_in - cash_out + int(owed - after + RESCUE_MARGIN + 9) // 10 * 10
        asked = [o.get_cash - o.give_cash for o in state.trade.history if o.proposer == me]
        if asked:
            net = min(net, min(asked))
        if net > cash_in - cash_out and state.trade.exchanges < state.config.max_trade_exchanges:
            counter = TradeOffer(me, other, give_spaces=gives, get_spaces=gets, give_cash=max(0, -net), get_cash=max(0, net),
                                 give_cards=offer.get_cards if target else offer.give_cards,
                                 get_cards=offer.give_cards if target else offer.get_cards)
            if validate_offer(state, counter) is None and state.players[other].cash >= counter.get_cash:
                return Choice(Action(ActionKind.COUNTER_TRADE, trade=counter),
                              f'Counteroffer. Request just enough to avoid bankruptcy (${short} more than the bank would pay)')
        if after > liquidation_cash(state, me) and accept in d.actions:
            return Choice(accept, f'Accept to reduce the shortfall before bankruptcy (${short})')
        return None

    def _raise_funds(self, state: GameState, d: Decision) -> Choice:
        pid = d.player
        mortgages = [a for a in d.actions if a.kind is ActionKind.MORTGAGE]
        loose = [a for a in mortgages if not _in_full_group(state, pid, a.space)]
        if loose:
            a = min(loose, key=lambda a: (_base_rent(a.space), a.space))
            return Choice(a, f'Mortgage {BOARD[a.space].name}. Lowest rent outside my sets')
        sells = [a for a in d.actions if a.kind is ActionKind.SELL_BUILDING]
        if sells:
            a = min(sells, key=lambda a: (BOARD[a.space].rents[0], a.space))
            return Choice(a, f'Sell a building on {BOARD[a.space].name}. Cheapest color group')
        a = min(mortgages, key=lambda a: (_base_rent(a.space), a.space))
        return Choice(a, f'Mortgage {BOARD[a.space].name}. No other assets can be liquidated')

    def _turn(self, state: GameState, d: Decision) -> Choice:
        pid = d.player
        acts = d.actions
        if d.phase is Phase.PRE_ROLL and state.players[pid].in_jail:
            c = self._jail(state, d)
            if c is not None:
                return c
        c = self._manage(state, d)
        if c is not None:
            return c
        if d.phase is Phase.PRE_ROLL:
            offer = self._proposal(state, pid)
            if offer is not None:
                names = [seat_label(i) for i in range(len(state.players))]
                return Choice(Action(ActionKind.PROPOSE_TRADE, trade=offer), self._proposal_reason(state, offer, names))
        if ROLL in acts:
            return Choice(ROLL, 'Roll the dice')
        return Choice(END_TURN, 'End my turn')

    def _proposal_reason(self, state: GameState, offer, names: list[str]) -> str:
        return f'Propose a trade to complete my set: {describe_trade(offer, names)}'

    def _jail(self, state: GameState, d: Decision) -> Choice | None:
        if self.profile.leave_jail_early or _early_game(state):
            for a, why in (
                (Action(ActionKind.USE_JAIL_CARD), 'Use a jail release card'),
                (Action(ActionKind.PAY_JAIL_FINE), 'Pay the jail fine to buy properties'),
            ):
                if a in d.actions:
                    return Choice(a, why)
            return None
        return Choice(ROLL, 'Stay in jail and try for doubles. Safer late in the game')

    def _manage(self, state: GameState, d: Decision) -> Choice | None:
        prof = self.profile
        pid = d.player
        p = state.players[pid]
        acts = d.actions
        for a in acts:
            if a.kind is ActionKind.UNMORTGAGE and (
                _in_full_group(state, pid, a.space) or BOARD[a.space].group in prof.preferred_groups
            ):
                if p.cash - unmortgage_cost(a.space) >= 2 * prof.build_reserve:
                    return Choice(a, f'Repay the mortgage on {BOARD[a.space].name}. Ample cash remains')

        floor = self._build_floor(state, pid, acts)
        builds = [
            a
            for a in acts
            if a.kind is ActionKind.BUILD
            and state.buildings[a.space] < prof.build_target
            and p.cash - BOARD[a.space].house_cost >= max(prof.build_reserve, floor)
        ]
        if builds:
            a = min(
                builds,
                key=lambda a: (
                    BOARD[a.space].group not in prof.preferred_groups,
                    -BOARD[a.space].rents[3],
                    -BOARD[a.space].price,
                ),
            )
            return Choice(a, f'Build on the set with cash above the reserve of ${max(prof.build_reserve, floor)}')
        return None

    def _build_floor(self, state: GameState, me: int, actions=None) -> int:
        if actions is not None and not any(a.kind is ActionKind.BUILD for a in actions):
            return 0
        return build_floor(state, me, self.profile.build_buffer)

    def _proposal(self, state: GameState, pid: int):
        if state.trades_this_turn >= min(1, state.config.max_trades_per_turn):
            self._untried(state, [])
            return None
        return self._first_untried(state, self._candidates(state, pid))

    def _untried(self, state: GameState, offers: list) -> list:
        owners = tuple(state.owner)
        if owners != self._tried_owners:
            self._tried, self._tried_owners = set(), owners
        return [o for o in offers if o not in self._tried]

    def _first_untried(self, state: GameState, offers: list):
        z = self.profile.trade_variation
        ok = []
        for offer in self._untried(state, offers):
            if feeds_rival(state, offer, offer.proposer, self.profile.feed_ratio):
                continue
            if not self._offer_ok(state, offer):
                continue
            ok.append(offer)
            if z <= 0:
                break
        if not ok:
            return None
        offer = ok[variation.pick(self._variation_rng, len(ok), z)]
        self._tried.add(offer)
        self._on_proposed(offer)
        return self._vary(state, offer, offer.proposer)

    def _vary(self, state: GameState, offer: TradeOffer, me: int, ok=None) -> TradeOffer:
        z = self.profile.trade_variation
        if z <= 0 or not (offer.give_cash or offer.get_cash):
            return offer
        if ok is None:
            positive = trade_score(state, offer, me) > 0
            ok = lambda o: not positive or trade_score(state, o, me) > 0
        ratio = self.profile.feed_ratio

        mine = offer.give_cash if me == offer.proposer else offer.get_cash
        cap = max(mine, state.players[me].cash - self.profile.buy_reserve // 2)

        def fine(o: TradeOffer) -> bool:
            pay = o.give_cash if me == o.proposer else o.get_cash
            return pay <= cap and not feeds_rival(state, o, me, ratio) and ok(o)

        return variation.varied(state, self._variation_rng, offer, z, fine)

    def _accept_shift(self, offer: TradeOffer) -> float:
        return variation.threshold(self._variation_rng, offer, self.profile.trade_variation)

    def _offer_ok(self, state: GameState, offer: TradeOffer) -> bool:
        return True

    def _on_proposed(self, offer) -> None:
        """Specialized strategies may record an attempted purchase here."""

    def _window(self, state: GameState, d: Decision) -> Choice:
        slot = (state.window.serial if state.window is not None else -1, d.player)
        if getattr(self, "_win_slot", None) != slot:
            self._win_slot, self._win_steps = slot, 0
        if self._win_steps < WINDOW_MANAGE_LIMIT:
            c = self._manage(state, d)
            if c is not None:
                self._win_steps += 1
                return Choice(c.action, f'Trade window: {c.explanation}')
        offer = self._window_proposal(state, d.player)
        if offer is None:
            return Choice(PASS, 'No offer is worth sending now')
        names = [seat_label(i) for i in range(len(state.players))]
        return Choice(Action(ActionKind.PROPOSE_TRADE, trade=offer), self._window_reason(state, offer, names))

    def _window_proposal(self, state: GameState, pid: int):
        w = state.window
        first = payer_purchases(state, pid, self.profile.buy_reserve // 2) if w is not None and w.kind == 'payment' else []
        return self._first_untried(state, first + list(self._candidates(state, pid)))

    def _window_reason(self, state: GameState, offer, names: list[str]) -> str:
        w = state.window
        if w is not None and w.kind == 'payment' and offer.target == w.trigger and offer.get_spaces and not offer.give_spaces:
            return f'Before payment, buy a property from the payer. {describe_trade(offer, names)}'
        return f'Trade window: {self._proposal_reason(state, offer, names)}'

    def _candidates(self, state: GameState, pid: int) -> list:
        return proposals(state, pid, self.profile.buy_reserve // 2)

    def _feed_response(self, state: GameState, d: Decision) -> Choice | None:
        offer = state.trade.offer
        me = d.player
        ratio = self.profile.feed_ratio
        if not feeds_rival(state, offer, me, ratio):
            return None
        extra = feed_price(state, offer, me, ratio)
        if state.trade.exchanges < state.config.max_trade_exchanges:
            net = offer.give_cash - offer.get_cash + extra
            counter = TradeOffer(me, offer.proposer, give_spaces=offer.get_spaces, get_spaces=offer.give_spaces,
                                 give_cash=max(0, -net), get_cash=max(0, net),
                                 give_cards=offer.get_cards, get_cards=offer.give_cards)
            if (validate_offer(state, counter) is None and not feeds_rival(state, counter, me, ratio)
                    and state.players[offer.proposer].cash >= counter.get_cash):
                cash = state.players[offer.proposer].cash
                counter = self._vary(state, counter, me, lambda o: o.get_cash <= cash)
                adjustment = (counter.get_cash - counter.give_cash) - (offer.give_cash - offer.get_cash)
                return Choice(Action(ActionKind.COUNTER_TRADE, trade=counter),
                              f'The rival would gain a set or a second property. Request ${adjustment} more')
        return Choice(Action(ActionKind.REJECT_TRADE), 'Reject. The price fails to cover the set or second property given to a rival')

    def _respond(self, state: GameState, d: Decision) -> Choice:
        feed = self._feed_response(state, d)
        if feed is not None:
            return feed
        offer = state.trade.offer
        me = d.player
        score = trade_score(state, offer, me)
        left = state.players[me].cash - (offer.get_cash if me == offer.target else offer.give_cash)
        if score > self._accept_shift(offer) and left >= self.profile.buy_reserve // 2:
            if score > 0:
                return Choice(Action(ActionKind.ACCEPT_TRADE), f'Accept the trade, score +{score:.0f}')
            return Choice(Action(ActionKind.ACCEPT_TRADE), f'Accept a marginal trade, score {score:.0f}')
        if state.trade.exchanges < state.config.max_trade_exchanges:
            counter = counter_for(state, offer, me)
            if counter is not None:
                cash = state.players[offer.proposer].cash
                counter = self._vary(state, counter, me, lambda o: trade_score(state, o, me) > 0 and o.get_cash <= cash)
                return Choice(Action(ActionKind.COUNTER_TRADE, trade=counter), 'Request a cash adjustment to make the trade worthwhile')
        return Choice(Action(ActionKind.REJECT_TRADE), f'Reject the trade with score {score:.0f}')

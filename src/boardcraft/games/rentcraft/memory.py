"""Public play history derived from accepted moves and revealed cards."""

from dataclasses import dataclass, field, replace

from .board import BOARD, GROUPS

RECENT = 6
LOG_KEEP = 256
PAIR_KEEP = 8
LOG_SPACES = 3
DECK_SIZE = 16
OWN_ACT_KEEP = 64
OWNERSHIP = ("buy", "auction_won", "trade", "bankrupt")

NOTABLE = (
    "buy", "auction_won", "pay_player", "build", "sell_building", "mortgage", "unmortgage",
    "trade_offer", "trade_counter", "trade", "trade_reject", "bankrupt", "jail", "card",
)

LOG_KINDS: tuple[str, ...] = (
    "buy", "decline", "auction_won", "auction_unsold", "pay", "card", "build", "sell_building", "mortgage",
    "unmortgage", "trade_offer", "trade_counter", "trade", "trade_reject", "bankrupt", "jail", "jail_release", "pass_go",
)

@dataclass(frozen=True)
class LogEntry:

    kind: str
    who: int | None = None
    to: int | None = None
    space: int | None = None
    card: tuple[str, int] | None = None
    give: tuple[int, ...] = ()
    get: tuple[int, ...] = ()
    truncated: bool = False
    bids: tuple[int, ...] = ()
    amount: int = 0
    round: int = 1
    turn: int = -1

    def __copy__(self) -> "LogEntry":
        return self

    def __deepcopy__(self, memo) -> "LogEntry":
        return self


@dataclass
class PairOffer:

    give: tuple[int, ...]
    get: tuple[int, ...]
    cash: int
    cards: tuple[int, int]
    outcome: str
    round: int
    counter: bool
    ctx: tuple[int, tuple[int, ...], tuple[int, ...]] | None = field(default=None, compare=False)


def round_of(state) -> int:
    return state.round


@dataclass(frozen=True)
class ModelAct:
    """An accepted action, from its actor's perspective and pre-move context."""

    kind: str
    who: int
    turn: int
    to: int | None = None
    space: int | None = None
    give: tuple[int, ...] = ()
    get: tuple[int, ...] = ()
    amount: int = 0
    cards: tuple[int, int] = (0, 0)
    outcome: str = ""
    phase: str = ""
    window: str = ""
    trigger: int | None = None


@dataclass
class ModelSnapshot:
    cash: list[int]
    worth: list[int]
    owned: list[int]
    buildings: list[int]
    mortgaged: list[int]
    paid: list[list[int]]


@dataclass
class ModelHistory:
    """Bounded engine evidence; only Game.new certifies a complete start."""

    schema: int = 1
    complete_from_start: bool = False
    snaps: dict[int, ModelSnapshot] = field(default_factory=dict)
    last_current: int = -1
    own_acts: list[ModelAct] = field(default_factory=list)


@dataclass
class Memory:
    proposals: list[int]
    accepted: list[int]
    rejected: list[int]
    auctions_won: list[int]
    auction_paid: list[int]
    auction_list: list[int]
    paid: list[list[int]]
    recent: list[tuple[str, int | None]] = field(default_factory=list)
    offers: list[list[int]] = field(default_factory=list)
    rejects: list[list[int]] = field(default_factory=list)
    streak: list[list[int]] = field(default_factory=list)
    last_cash: list[list[int]] = field(default_factory=list)
    last_turn: list[list[int]] = field(default_factory=list)
    changed: dict[int, int] = field(default_factory=dict)
    changed_turn: int = -1
    seen_cards: dict[str, set[int]] = field(default_factory=lambda: {"opportunity": set(), "community_fund": set()})
    sent_offers: set[tuple[int, int, tuple[int, ...], tuple[int, ...], int, int, int, int]] = field(default_factory=set)
    sent_turn: int = -1
    log: list[LogEntry] = field(default_factory=list)
    accepted_pair: list[list[int]] = field(default_factory=list)
    countered: list[list[int]] = field(default_factory=list)
    declined: list[int] = field(default_factory=list)
    card_order: dict[str, list[int]] = field(default_factory=lambda: {"opportunity": [], "community_fund": []})
    known_bottom: dict[str, list[int]] = field(default_factory=lambda: {"opportunity": [], "community_fund": []})
    cards_drawn: dict[str, int] = field(default_factory=lambda: {"opportunity": 0, "community_fund": 0})
    pair_offers: list[list[list[PairOffer]]] = field(default_factory=list)
    auction_bids: dict[int, int] = field(default_factory=dict)
    model_history: ModelHistory | None = None

    @classmethod
    def new(cls, n: int) -> "Memory":
        z = lambda: [0] * n
        zz = lambda v=0: [[v] * n for _ in range(n)]
        return cls(z(), z(), z(), z(), z(), z(), zz(), offers=zz(), rejects=zz(), streak=zz(), last_cash=zz(), last_turn=zz(-1),
                   accepted_pair=zz(), countered=zz(), declined=z(), pair_offers=[[[] for _ in range(n)] for _ in range(n)])


def remember(state, events, held=None) -> None:
    if state.memory is None:
        state.memory = Memory.new(len(state.players))
    m = state.memory
    rnd = round_of(state)
    if m.changed_turn != state.turn:
        m.changed, m.changed_turn = {}, state.turn
    for e in events:
        kind, p = e.kind, e.player
        if kind in ("build", "unmortgage", "sell_building", "mortgage"):
            sign = 1 if kind in ("build", "unmortgage") else -1
            spaces = (e.data["space"],) if e.data.get("space") is not None else GROUPS.get(e.data.get("group"), ())
            for sp in spaces:
                m.changed[sp] = sign
        if kind in OWNERSHIP:
            for row in m.streak:
                row[:] = [0] * len(row)
        _log(m, e, rnd, len(state.players), state.turn)
        if kind in ("trade_offer", "trade_counter"):
            m.proposals[p] += 1
            o = e.data["offer"]
            if kind == "trade_counter":
                m.countered[o.target][o.proposer] += 1
                _close_pair_offer(m, o.target, o.proposer, "countered")
            hist = m.pair_offers[o.proposer][o.target]
            ctx = (state.players[o.target].cash,
                   tuple(i for i, w in enumerate(state.owner) if w == o.proposer),
                   tuple(i for i, w in enumerate(state.owner) if w == o.target))
            hist.append(PairOffer(o.give_spaces, o.get_spaces, o.give_cash - o.get_cash, (o.give_cards, o.get_cards),
                                  "pending", rnd, kind == "trade_counter", ctx))
            del hist[:-PAIR_KEEP]
            if m.sent_turn != state.turn:
                m.sent_offers, m.sent_turn = set(), state.turn
            m.sent_offers.add(offer_key(o))
            m.offers[o.proposer][o.target] += 1
            m.streak[o.proposer][o.target] += 1
            m.last_cash[o.proposer][o.target] = o.give_cash - o.get_cash
            m.last_turn[o.proposer][o.target] = state.turn
        elif kind == "card":
            m.seen_cards.setdefault(e.data["deck"], set()).add(e.data["card"])
            _draw(m, e.data["deck"], e.data["card"])
        elif kind == "decline":
            m.declined[p] += 1
        elif kind == "trade":
            o = e.data["offer"]
            m.accepted[o.target] += 1
            m.accepted_pair[o.proposer][o.target] += 1
            _close_pair_offer(m, o.proposer, o.target, "accepted")
        elif kind == "trade_reject":
            m.rejected[p] += 1
            o = e.data["offer"]
            m.rejects[o.proposer][o.target] += 1
            _close_pair_offer(m, o.proposer, o.target, "rejected")
        elif kind == "bid":
            m.auction_bids[p] = e.data["bid"]
        elif kind == "auction_won":
            m.auctions_won[p] += 1
            m.auction_paid[p] += e.data["price"]
            m.auction_list[p] += BOARD[e.data["space"]].price
        elif kind == "pay" and p is not None and e.data.get("to") is not None:
            m.paid[p][e.data["to"]] += e.data["amount"]
            kind = "pay_player"
        if kind in NOTABLE:
            m.recent.append((kind, p))
    del m.recent[:-RECENT]
    if held is not None:
        _returned_jail_cards(state, m, held, rnd)
    h = m.model_history
    if h is not None and h.last_current != state.current:
        if h.last_current >= 0:
            h.snaps[h.last_current] = model_snapshot(state)
        h.last_current = state.current


def model_snapshot(state) -> ModelSnapshot:
    from .debt import net_worth

    owned = [[i for i, owner in enumerate(state.owner) if owner == pid] for pid in range(len(state.players))]
    return ModelSnapshot(
        [p.cash for p in state.players], [net_worth(state, pid) for pid in range(len(state.players))],
        [len(spaces) for spaces in owned], [sum(state.buildings[i] for i in spaces) for spaces in owned],
        [sum(state.mortgaged[i] for i in spaces) for spaces in owned], [row[:] for row in state.memory.paid],
    )


def act_context(state) -> tuple[str, str, int | None]:
    window = state.window
    return state.phase.value, window.kind if window else "", window.trigger if window else None


def note_turn(state, turn, actor, phase, action, events, takeover, context) -> None:
    """Remember accepted actions after public events, without any bot draft state."""
    h = state.memory.model_history
    if h is None:
        return
    # Actions from a completed turn may remain until the next accepted action, but
    # observations expose only actions whose turn equals the current game turn.
    if h.own_acts and h.own_acts[-1].turn != turn:
        h.own_acts.clear()

    def note(kind, who=actor, **values):
        h.own_acts.append(ModelAct(kind, who, turn, phase=context[0], window=context[1], trigger=context[2], **values))
        if sum(a.who == who for a in h.own_acts) > OWN_ACT_KEEP:
            del h.own_acts[next(i for i, a in enumerate(h.own_acts) if a.who == who)]

    def offer_act(kind, offer, mine):
        give, get = (offer.give_spaces, offer.get_spaces) if mine else (offer.get_spaces, offer.give_spaces)
        cash = offer.give_cash - offer.get_cash
        cards = (offer.give_cards, offer.get_cards)
        note(kind, to=offer.target if mine else offer.proposer, give=tuple(give), get=tuple(get),
             amount=cash if mine else -cash, cards=cards if mine else cards[::-1], outcome="pending" if mine else "")

    for e in events:
        if e.kind in ("trade", "trade_reject", "trade_counter"):
            o = e.data["offer"]
            proposer, target = (o.target, o.proposer) if e.kind == "trade_counter" else (o.proposer, o.target)
            outcome = {"trade": "accepted", "trade_reject": "rejected", "trade_counter": "countered"}[e.kind]
            for i in range(len(h.own_acts) - 1, -1, -1):
                a = h.own_acts[i]
                if a.who == proposer and a.to == target and a.kind in ("trade_offer", "trade_counter") and a.outcome == "pending":
                    h.own_acts[i] = replace(a, outcome=outcome)
                    break
    for e in events:
        d = e.data
        if e.kind == "auction_won":
            note("auction_won", e.player, space=d["space"], amount=d["price"])
        if e.player != actor:
            continue
        if e.kind in ("trade_offer", "trade_counter"):
            offer_act(e.kind, d["offer"], True)
        elif e.kind in ("buy", "decline", "build", "sell_building", "mortgage", "unmortgage", "bid"):
            note(e.kind, space=d.get("space"), amount=d.get("bid", d.get("price", d.get("cost", d.get("refund", d.get("amount", 0))))))
        elif e.kind == "pay" and d.get("reason") == "jail_fine":
            note("jail_fine", amount=d["amount"])
    if action.kind.value in ("accept_trade", "reject_trade"):
        o = next((e.data["offer"] for e in events if e.kind in ("trade", "trade_reject")), None)
        if o is not None and o.target == actor:
            offer_act(action.kind.value, o, False)
    elif action.kind.value == "use_jail_card":
        note("use_jail_card")
    elif action.kind.value == "pass" and phase.value == "trade_window":
        note("pass_window")
    elif action.kind.value == "pass" and phase.value == "mortgage_takeover" and takeover is not None:
        from .debt import interest

        note("interest", space=takeover, amount=interest(takeover))


def _close_pair_offer(m: Memory, a: int, b: int, outcome: str) -> None:
    hist = m.pair_offers[a][b]
    if hist and hist[-1].outcome == "pending":
        hist[-1].outcome = outcome


def _draw(m: Memory, deck: str, card: int) -> None:
    from .cards import JAIL_FREE_ID

    # Ordinary cards return to the bottom; a held jail release card leaves the deck.
    known = m.known_bottom[deck]
    if card in known:
        known.remove(card)
    if card != JAIL_FREE_ID[deck]:
        known.append(card)
    m.cards_drawn[deck] = m.cards_drawn.get(deck, 0) + 1
    order = m.card_order.setdefault(deck, [])
    if card in order:
        order.clear()
    order.append(card)
    if len(order) >= DECK_SIZE:
        order.clear()


def _log(m: Memory, e, rnd: int, n: int, turn: int = -1) -> None:
    kind, d = e.kind, e.data
    if kind not in LOG_KINDS:
        return
    to, amount, card, give, get, truncated, bids = None, 0, None, (), (), False, ()
    if kind in ("buy", "auction_won"):
        amount = d["price"]
    elif kind == "pay":
        to, amount = d.get("to"), d["amount"]
    elif kind == "card":
        card = (d["deck"], d["card"])
    elif kind == "build":
        amount = d["cost"]
    elif kind == "sell_building":
        amount = d["refund"]
    elif kind in ("mortgage", "unmortgage", "pass_go"):
        amount = d["amount"]
    elif kind == "bankrupt":
        to = d.get("to")
    elif kind in ("trade_offer", "trade_counter", "trade", "trade_reject"):
        o = d["offer"]
        to = o.target if e.player == o.proposer else o.proposer
        give, get = tuple(o.give_spaces[:LOG_SPACES]), tuple(o.get_spaces[:LOG_SPACES])
        truncated = len(o.give_spaces) > LOG_SPACES or len(o.get_spaces) > LOG_SPACES
        amount = o.give_cash - o.get_cash
    if kind in ("auction_won", "auction_unsold"):
        bids = tuple(m.auction_bids.get(i, 0) for i in range(n))
        m.auction_bids = {}
    entry = LogEntry(kind, e.player, to, d.get("space"), card, give, get, truncated, bids, amount, rnd, turn)
    m.log.append(entry)
    del m.log[:-LOG_KEEP]


def held_jail_cards(state) -> list[list[str]] | None:
    if not any(p.jail_cards for p in state.players):
        return None
    return [list(p.jail_cards) for p in state.players]


def _returned_jail_cards(state, m: Memory, held: list[list[str]], rnd: int) -> None:
    from .cards import JAIL_FREE_ID

    if sum(len(p.jail_cards) for p in state.players) >= sum(len(h) for h in held):
        return
    for deck in JAIL_FREE_ID:
        before = sum(h.count(deck) for h in held)
        now = sum(p.jail_cards.count(deck) for p in state.players)
        losers = [pid for pid, h in enumerate(held) if h.count(deck) > state.players[pid].jail_cards.count(deck)]
        for k in range(before - now):
            who = losers[min(k, len(losers) - 1)] if losers else None
            m.known_bottom[deck].append(JAIL_FREE_ID[deck])
            m.log.append(LogEntry("jail_card_return", who, card=(deck, JAIL_FREE_ID[deck]), round=rnd, turn=state.turn))
            del m.log[:-LOG_KEEP]


def offer_key(o) -> tuple:
    return (o.proposer, o.target, tuple(sorted(o.give_spaces)), tuple(sorted(o.get_spaces)), o.give_cash, o.get_cash,
            o.give_cards, o.get_cards)


def sent_this_turn(state, offer) -> bool:
    m = state.memory
    return m is not None and m.sent_turn == state.turn and offer_key(offer) in m.sent_offers

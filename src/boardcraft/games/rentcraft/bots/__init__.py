"""Public rule-player catalogue and shared decision descriptions."""
from dataclasses import dataclass

from boardcraft.contracts import BuiltinBotInfo
from ..actions import Action
from ..board import BOARD

BEHAVIOR_VERSION = "1"
DISPLAY_NAMES = (
    "Miser",
    "Investor",
    "Collector",
    "Cautious",
    "Master",
    "Calculator",
    "GPT-Style",
    "Random",
    "Absurd GPT-Style",
    "Absurd Calculator",
    "Absurd Investor",
    "Hustler",
    "Tactician",
    "Simulator",
    "Fast Simulator",
    "Bonjour A",
    "Bonjour B",
    "Bonjour C",
    "Resistant Bonjour A",
    "Resistant Bonjour B",
    "Resistant Bonjour C",
    "Bonjour Calculator",
    "Bonjour Calculator Pure",
    "Bonjour Calculator Auctions",
    "Bonjour Mixed Auctions",
    "Resistant Bonjour B + Windows",
    "Resistant Bonjour C + Windows",
    "Wary Calculator",
    "Absurd Resistant Bonjour B",
    "Absurd Resistant Bonjour C",
    "Absurd Bonjour Mixed Auctions",
    "Absurd Wary Calculator",
    "Absurd Tactician",
    "Controller",
    "Sol Sets",
)
BUILTINS = tuple(BuiltinBotInfo(name.lower().replace(" + ", "-plus-").replace(" ", "-"),
                                 name, BEHAVIOR_VERSION) for name in DISPLAY_NAMES)

BOT_DESCRIPTIONS = {
    'miser': 'Keeps cash reserves and limits spending on purchases, bids and development.',
    'investor': 'Spends on acquisitions and development with small cash reserves.',
    'collector': 'Prioritizes orange and red groups and bids more for preferred properties.',
    'cautious': 'Maintains cash reserves and limits bidding and development exposure.',
    'master': 'Prioritizes selected groups and manages the shared supply of houses.',
    'calculator': 'Values properties using landing probabilities, expected rent and available cash.',
    'gpt-style': 'Acquires starting groups, finances houses and negotiates for missing streets.',
    'random': 'Chooses uniformly among enumerated legal moves.',
    'absurd-gpt-style': 'Adds deliberately unfavorable trade proposals to the GPT-Style policy.',
    'absurd-calculator': 'Adds deliberately unfavorable trade proposals to the Calculator policy.',
    'absurd-investor': 'Adds deliberately unfavorable trade proposals to the Investor policy.',
    'hustler': 'Layers repeated unfavorable trade proposals over a property valuation policy.',
    'tactician': 'Requires positive trade gains and protects the mortgage value of outgoing deeds.',
    'simulator': 'Compares candidate actions using paired hypothetical games played by Calculator.',
    'fast-simulator': 'Compares paired hypothetical games in batches and eliminates inferior candidates.',
    'bonjour-a': 'Uses a common priority policy for purchasing, bidding, trading and development.',
    'bonjour-b': 'Uses priority rules favoring railroads and dark blue while deprioritizing utilities.',
    'bonjour-c': 'Uses priority rules favoring railroads, orange and light blue groups.',
    'resistant-bonjour-a': 'Adds checks on gains given to rivals to the Bonjour A policy.',
    'resistant-bonjour-b': 'Adds checks on gains given to rivals to the Bonjour B policy.',
    'resistant-bonjour-c': 'Adds checks on gains given to rivals to the Bonjour C policy.',
    'bonjour-calculator': 'Combines Bonjour priorities with property valuation calculations.',
    'bonjour-calculator-pure': 'Uses property valuation calculations within a Bonjour strategy.',
    'bonjour-calculator-auctions': 'Uses property valuation calculations for Bonjour auction decisions.',
    'bonjour-mixed-auctions': 'Combines Bonjour priorities and calculated auction valuations.',
    'resistant-bonjour-b-plus-windows': 'Extends Resistant Bonjour B with trade window decisions.',
    'resistant-bonjour-c-plus-windows': 'Extends Resistant Bonjour C with trade window decisions.',
    'wary-calculator': 'Values partial progress toward groups as well as completed sets.',
    'absurd-resistant-bonjour-b': 'Adds deliberately unfavorable proposals to Resistant Bonjour B.',
    'absurd-resistant-bonjour-c': 'Adds deliberately unfavorable proposals to Resistant Bonjour C.',
    'absurd-bonjour-mixed-auctions': 'Adds deliberately unfavorable proposals to Bonjour Mixed Auctions.',
    'absurd-wary-calculator': 'Adds deliberately unfavorable proposals to Wary Calculator.',
    'absurd-tactician': 'Adds deliberately unfavorable proposals to Tactician.',
    'controller': 'Blocks rival sets and uses public negotiation history to price future offers.',
    'sol-sets': 'Chooses acquisitions, financing and trades that support a developable set.',
}


@dataclass(frozen=True)
class Choice:
    action: Action
    explanation: str


def describe_trade(offer, names):
    def side(spaces, cash, cards):
        parts = [BOARD[i].name for i in spaces]
        if cash:
            parts.append(f"${cash}")
        if cards:
            parts.append(f"{cards} jail release card(s)")
        return ", ".join(parts) or "nothing"

    give = side(offer.give_spaces, offer.give_cash, offer.give_cards)
    receive = side(offer.get_spaces, offer.get_cash, offer.get_cards)
    return f"{names[offer.proposer]} offers {give} to {names[offer.target]} for {receive}."


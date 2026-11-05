"""Cash reserves, bidding limits and policy defaults for the named rule players."""
from dataclasses import dataclass, replace

from .bonjour import CALCULATOR_VARIANTS as _CALCULATOR_VARIANTS, AUCTIONS as _AUCTIONS, WINDOWS as _WINDOWS
from .controller import CONTROLLER as _CONTROLLER


@dataclass(frozen=True)
class BotProfile:
    id: str
    name: str

    buy_reserve: int

    build_reserve: int

    max_bid_factor: float
    preferred_groups: tuple[str, ...]

    preferred_bid_factor: float

    build_target: int
    leave_jail_early: bool

    raise_share: float = 0.5

    feed_ratio: float = 1.25
    bid_jump: float = 0.25
    build_buffer: float = 0.5

    trade_variation: float = 0.0
    random_move_probability: float = 0.0


PROFILES: dict[str, BotProfile] = {
    'miser': BotProfile('miser', 'Miser', 400, 500, 0.8, (), 0.8, 3, False, raise_share=0.2, trade_variation=0.2),
    'investor': BotProfile('investor', 'Investor', 0, 100, 1.2, (), 1.2, 5, True, raise_share=0.75, trade_variation=0.3),
    'collector': BotProfile('collector', 'Collector', 150, 150, 1.0, ("orange", "red"), 1.6, 5, True, raise_share=0.75,
                              build_buffer=0.25, trade_variation=0.3),
    'cautious': BotProfile('cautious', 'Cautious', 300, 300, 0.9, (), 0.9, 3, False, raise_share=0.25, trade_variation=0.2),

    'master': BotProfile('master', 'Master', 150, 100, 1.0, ("orange", "red", "light_blue"), 1.7, 3, True, trade_variation=0.15),

    'calculator': BotProfile('calculator', 'Calculator', 50, 50, 1.0, (), 1.0, 5, True, trade_variation=0.1),

    'gpt-style': BotProfile('gpt-style', 'GPT-Style', 250, 250, 1.0, ("brown", "light_blue", "pink", "orange"), 1.3, 5, True,
                           bid_jump=0.4, build_buffer=0.75, trade_variation=0.15),
}


PROFILES.update({
    bid: replace(PROFILES[base], id=bid, name=name)
    for bid, base, name in (
        ('absurd-gpt-style', 'gpt-style', 'Absurd GPT-Style'),
        ('absurd-calculator', 'calculator', 'Absurd Calculator'),
        ('absurd-investor', 'investor', 'Absurd Investor'),
        ('hustler', 'calculator', 'Hustler'),
        ('tactician', 'gpt-style', 'Tactician'),
        ('simulator', 'calculator', 'Simulator'),
        ('fast-simulator', 'calculator', 'Fast Simulator'),
        ('wary-calculator', 'calculator', 'Wary Calculator'),
    )
})

PROFILES.update({
    bid: replace(PROFILES['investor'], id=bid, name=name)
    for bid, name in (("bonjour-a", "Bonjour A"), ("bonjour-b", "Bonjour B"), ("bonjour-c", "Bonjour C"),
                      ('resistant-bonjour-a', 'Resistant Bonjour A'), ('resistant-bonjour-b', 'Resistant Bonjour B'),
                      ('resistant-bonjour-c', 'Resistant Bonjour C'))
})

PROFILES.update({bid: replace(PROFILES['investor'], id=bid, name=name)
                 for bid, (name, _) in (*_CALCULATOR_VARIANTS.items(), *_AUCTIONS.items(), *_WINDOWS.items(), *_CONTROLLER.items())})

PROFILES['sol-sets'] = replace(PROFILES['investor'], id='sol-sets', name='Sol Sets')

PROFILES['tactician'] = replace(PROFILES['tactician'], raise_share=0.75, bid_jump=0.25, build_buffer=0.5)

PROFILES.update({
    bid: replace(PROFILES[base], id=bid, name=name)
    for bid, base, name in (
        ('absurd-resistant-bonjour-b', 'resistant-bonjour-b', 'Absurd Resistant Bonjour B'),
        ('absurd-resistant-bonjour-c', 'resistant-bonjour-c', 'Absurd Resistant Bonjour C'),
        ('absurd-bonjour-mixed-auctions', 'bonjour-mixed-auctions', 'Absurd Bonjour Mixed Auctions'),
        ('absurd-wary-calculator', 'wary-calculator', 'Absurd Wary Calculator'),
        ('absurd-tactician', 'tactician', 'Absurd Tactician'),
    )
})

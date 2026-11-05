from boardcraft.contracts import Action
from boardcraft.games.rentcraft.board import BOARD
from boardcraft.games.rentcraft.cards import DECKS
from boardcraft.games.rentcraft.module import RentcraftModule


def test_board_and_rules():
    mod = RentcraftModule()
    spec = mod.board_spec()
    assert [space["index"] for space in spec["spaces"]] == list(range(40))
    assert [row["name"] for row in spec["spaces"]] == [s.name for s in BOARD]
    assert BOARD[39].rents == (50, 200, 600, 1400, 1700, 2000)
    assert BOARD[39].name in mod.describe_action(Action("build:39", "build", {"space": 39}))
    assert "Community Fund" in mod.rules_text() and "Opportunity" in mod.rules_text()
    assert mod.encoder() is None


def test_card_effects_and_values():
    opportunity = [("advance_to", 39), ("advance_to", 0), ("advance_to", 24), ("advance_to", 11),
                   ("advance_nearest_railroad", 0), ("advance_nearest_railroad", 0),
                   ("advance_nearest_utility", 0), ("collect", 50), ("jail_free", 0), ("back_three", 0),
                   ("go_to_jail", 0), ("repairs", (25, 100)), ("pay", 15), ("advance_to", 5),
                   ("pay_each", 50), ("collect", 150)]
    community = [("advance_to", 0), ("collect", 200), ("pay", 50), ("collect", 50), ("jail_free", 0),
                 ("go_to_jail", 0), ("collect", 100), ("collect", 20), ("collect_each", 10),
                 ("collect", 100), ("pay", 100), ("pay", 50), ("collect", 25), ("repairs", (40, 115)),
                 ("collect", 10), ("collect", 100)]
    for deck, expected in [("opportunity", opportunity), ("community_fund", community)]:
        cards = DECKS[deck]
        assert [c.id for c in cards] == list(range(16))
        assert [(c.effect, c.target if c.target is not None else (c.value, c.value2) if c.value2 else c.value)
                for c in cards] == expected



def test_all_street_deeds_and_special_spaces():
    # Price, building cost and the six rent levels on each title.
    deeds = {
        1: (60, 50, 2, 10, 30, 90, 160, 250), 3: (60, 50, 4, 20, 60, 180, 320, 450),
        6: (100, 50, 6, 30, 90, 270, 400, 550), 8: (100, 50, 6, 30, 90, 270, 400, 550),
        9: (120, 50, 8, 40, 100, 300, 450, 600), 11: (140, 100, 10, 50, 150, 450, 625, 750),
        13: (140, 100, 10, 50, 150, 450, 625, 750), 14: (160, 100, 12, 60, 180, 500, 700, 900),
        16: (180, 100, 14, 70, 200, 550, 750, 950), 18: (180, 100, 14, 70, 200, 550, 750, 950),
        19: (200, 100, 16, 80, 220, 600, 800, 1000), 21: (220, 150, 18, 90, 250, 700, 875, 1050),
        23: (220, 150, 18, 90, 250, 700, 875, 1050), 24: (240, 150, 20, 100, 300, 750, 925, 1100),
        26: (260, 150, 22, 110, 330, 800, 975, 1150), 27: (260, 150, 22, 110, 330, 800, 975, 1150),
        29: (280, 150, 24, 120, 360, 850, 1025, 1200), 31: (300, 200, 26, 130, 390, 900, 1100, 1275),
        32: (300, 200, 26, 130, 390, 900, 1100, 1275), 34: (320, 200, 28, 150, 450, 1000, 1200, 1400),
        37: (350, 200, 35, 175, 500, 1100, 1300, 1500), 39: (400, 200, 50, 200, 600, 1400, 1700, 2000),
    }
    assert {s.index: (s.price, s.house_cost, *s.rents) for s in BOARD if s.group} == deeds
    assert {s.index: s.price for s in BOARD if s.kind.value == "railroad"} == dict.fromkeys((5, 15, 25, 35), 200)
    assert {s.index: s.price for s in BOARD if s.kind.value == "utility"} == {12: 150, 28: 150}
    assert {s.index: s.tax for s in BOARD if s.tax} == {4: 200, 38: 100}
    assert [s.index for s in BOARD if s.kind.value == "opportunity"] == [7, 22, 36]
    assert [s.index for s in BOARD if s.kind.value == "community_fund"] == [2, 17, 33]

"""Board spaces, title deeds, building supply and payment schedules."""
import json
from dataclasses import dataclass
from enum import Enum
from importlib.resources import files


class Kind(Enum):
    START = "start"
    PROPERTY = "property"
    RAILROAD = "railroad"
    UTILITY = "utility"
    TAX = "tax"
    OPPORTUNITY = "opportunity"
    COMMUNITY_FUND = "community_fund"
    JAIL = "jail"
    FREE_PARKING = "free_parking"
    GO_TO_JAIL = "go_to_jail"


@dataclass(frozen=True)
class Space:
    index: int
    name: str
    kind: Kind
    group: str | None
    price: int
    rents: tuple[int, ...]
    house_cost: int
    tax: int

    @property
    def mortgage_value(self) -> int:
        return self.price // 2


def resource(name: str) -> str:
    return files("boardcraft").joinpath("games", "rentcraft", name).read_text(encoding="utf-8")


START_CASH = 1500
GO_SALARY = 200
JAIL_FINE = 50
MAX_JAIL_TURNS = 3
TOTAL_HOUSES = 32
TOTAL_HOTELS = 12
HOTEL = 5
MORTGAGE_INTEREST = 0.10
RAILROAD_RENTS = (25, 50, 100, 200)
UTILITY_MULTIPLIERS = (4, 10)
BID_STEPS = (1, 10, 50, 100)
AUCTION_MAX_BID = 10_000
GO_INDEX = 0
JAIL_INDEX = 10
GO_TO_JAIL_INDEX = 30

BOARD = tuple(Space(**{**row, "kind": Kind(row["kind"]), "rents": tuple(row["rents"])})
              for row in json.loads(resource("data/board.en.json")))
GROUPS = {s.group: tuple(tile.index for tile in BOARD if tile.group == s.group)
          for s in BOARD if s.group}
RAILROADS = tuple(s.index for s in BOARD if s.kind is Kind.RAILROAD)
UTILITIES = tuple(s.index for s in BOARD if s.kind is Kind.UTILITY)
BUYABLE = frozenset(s.index for s in BOARD if s.kind in (Kind.PROPERTY, Kind.RAILROAD, Kind.UTILITY))


"""English descriptions of player choices using the board's canonical names."""
from .board import BOARD


def seat_label(player):
    return f"Seat {player + 1}"


def describe_action(action):
    kind, params = action.kind, action.params
    if "space" in params:
        name = BOARD[params["space"]].name
        return {"build": "Build on", "sell_building": "Sell a building on",
                "mortgage": "Mortgage", "unmortgage": "Repay the mortgage on"}[kind] + " " + name
    if kind == "bid":
        return f"Raise by ${params['amount']}"
    if kind in ("propose_trade", "counter_trade"):
        label = "Propose a trade" if kind == "propose_trade" else "Counter the offer"
        if not params:
            return label
        sides = []
        for key in ("give", "get"):
            items = [BOARD[i].name for i in params.get(key + "_spaces", [])]
            if cash := params.get(key + "_cash"):
                items.append(f"${cash}")
            if cards := params.get(key + "_cards"):
                items.append(f"{cards} jail release card(s)")
            sides.append(", ".join(items) or "nothing")
        return f"{label}. Give {sides[0]} and receive {sides[1]}."
    labels = {"roll": "Roll the dice", "buy": "Buy the property", "decline": "Send to auction",
              "pass_bid": "Leave the auction", "end_turn": "End turn", "pass": "Pass",
              "pay_jail_fine": "Pay the jail fine", "use_jail_card": "Use a jail release card",
              "accept_trade": "Accept the offer", "reject_trade": "Reject the offer",
              "request_trade": "Request a trade window", "declare_bankruptcy": "Declare bankruptcy",
              "resign": "Resign and settle debts",
              "liquidate": "Full bank liquidation: mortgages first, then building sales, until the debt is paid"}
    return labels[kind]


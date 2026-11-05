# Rentcraft

Buy titles, collect rent and negotiate with the other seats. The last player still solvent wins. If your table has a round limit, the richest remaining player wins when that round finishes.

## Starting the game

Two to four players start on Start with $1,500 each unless the table sets another starting amount. The bank has 32 houses and 12 hotels. Shuffle the 16 Opportunity cards and the 16 Community Fund cards separately. Each player rolls two dice. The highest total starts. If the highest totals tie, only those players reroll until one wins. Play proceeds in seat order from the winner.

## Moving and buying

Roll two dice and move clockwise by their sum. Collect $200 whenever you pass or reach Start going forward. A double grants another roll after the landing is resolved. A third consecutive double sends you directly to jail. Free Parking and visiting Jail have no cost or reward.

On an unowned street, station or utility, you may buy at the printed price if you can pay in full. Declining sends the title to auction. On another player's unmortgaged title, pay rent automatically. Income Tax costs $200 and Luxury Tax costs $100.

* A street's deed lists its rent for bare land, one through four houses and a hotel. Owning the entire color doubles the bare-land rent.
* Station rent is $25, $50, $100 or $200 for one through four stations owned.
* Utility rent is four times the dice total with one utility owned, or ten times the total with both. A mortgaged title earns no rent.

### Auctions

The next active seat after the current player opens the auction. Each bidder may raise the highest bid by $1, $10, $50 or $100, or leave the auction. A bid may exceed the bidder's cash, up to a total of $10,000. The last remaining bidder with the highest bid takes the title and pays the bank. If all pass without a winning bid, the bank keeps the title. A winner short of cash owes the bank the bid and can use the new title while raising money.

## Cards and jail

Draw the top card of the indicated deck and follow its instructions. Ordinary cards go to the bottom of their deck. Keep a jail release card until used, traded or surrendered in bankruptcy. Trading it preserves its deck of origin. Using it returns it to the bottom of that deck.

When a card moves you forward to a named space, collect $200 if you pass Start and resolve the destination. A trip to the next station charges double normal rent if another player owns it. A trip to the next utility calls for a fresh roll and charges ten times the total if another player owns it. Moving back three spaces also resolves the destination. Going directly to jail skips the Start salary. Repair bills count a hotel once at its hotel rate.

In jail, choose before rolling whether to pay $50 or use a jail release card. Either choice releases you for a normal roll. Alternatively, try for doubles. A successful jail roll moves you by the total with no extra roll. Two unsuccessful attempts leave you in jail. On your third failed attempt you owe $50, then move by that roll. Your properties continue to earn rent while you are in jail.

## Managing property

A complete color group with no mortgages can be developed. Each addition costs the building price printed on that street. Build on one of the least developed streets in the group. After four houses, the next level is a hotel. Exchange four houses for one bank hotel and pay one building price. A building must be available in the bank. The first eligible buyer gets it.

Sell from one of the most developed streets in a color, receiving half the building price for each level removed. Selling a hotel normally puts four houses on the street. If the bank lacks four houses, remove the group's hotels and redistribute its existing houses and the bank's available houses evenly, up to four per street. Extra houses go to the earlier board spaces. The bank pays half price for every level lost across the group.

Mortgage a title for half its purchase price. First sell all buildings in its color group. To lift a mortgage, pay the mortgage principal plus 10% interest rounded up to a whole dollar. When you receive a mortgaged title in a trade or bankruptcy, choose separately for each title. You may pay the full redemption amount now, or pay just the interest and leave the title mortgaged. Keeping the mortgage means paying principal and interest when you later redeem it. You can manage assets and negotiate before choosing. Interest you cannot pay becomes a bank debt.

## Trading

A proposal names one other active player and sets out what each side gives. Offer any combination of eligible titles, cash and jail release cards. At least one side must give something. Each title appears once and belongs to the side giving it. Sell the buildings in a title's color before including it. A proposer must hold the cash they offer. You may request cash the other player could raise by selling buildings and mortgaging titles. To accept, both players must actually hold everything promised.

The recipient may manage assets, accept, reject or counter. A counteroffer reverses the roles. All submitted offers and counters are public. Acceptance checks ownership, buildings, cards and cash again. If a title has become mortgaged, its new owner must make the takeover choice.

Negotiations are possible before or after rolling, when deciding to buy, on your auction turn, while raising cash for a debt, when handling a mortgage takeover and in trade windows. The default allowance is three negotiations during a turn. A debtor has a separate allowance for each debt, a bidder for each auction and a new owner for each mortgage takeover. A negotiation allows four offers including the opening proposal by default. Reaching the cap leaves acceptance or rejection available. A table can change the negotiation and offer limits.

With full windows, other active seats can act before a payment by the player on turn, after a title is acquired, after a negotiation, and before a turn ends. Payment-only tables keep the payment windows. Tables can also turn automatic windows off. Each seat called in a window can manage property, make one proposal or pass. Once its negotiation ends, the window moves to the next seat. After the window or negotiation, play resumes at the interrupted decision.

A seat may request a trade window even with automatic windows off. An existing window can pick up the request. Otherwise, requests wait in order until a safe point. Starting another negotiation or requesting a window cannot interrupt an outstanding response. Each seat has a separate request allowance per turn, equal to the table's turn negotiation limit.

## Debt, bankruptcy and resignation

If you cannot pay, raise cash by selling buildings, mortgaging titles or negotiating with another player. Once you have enough, the debt is paid. When the bank will lend no more, you may still negotiate within your allowance or declare bankruptcy. If no options remain, the engine resolves bankruptcy.

Bankrupt players sell buildings to the bank for half price. For a single player creditor, transfer the remaining titles, cash and jail release cards to that creditor. For a bank debt, return the titles free of mortgages and return the cards to their decks. Auction the returned titles in board order, starting each auction with the next active seat after the player on turn.

For multiple unpaid creditors, divide the available cash in proportion to the amounts owed. Round shares down, then distribute spare dollars to player creditors in seat order after the debtor, up to the sums owed. Return titles to the bank for auction and cards to their decks.

You may resign from any active seat. Resignation first settles your outstanding debts, including delayed payments in a window. If bank liquidation could cover the total, mortgage titles and sell buildings as needed to pay creditors before returning the remaining estate to the bank. If the bank cannot cover it, settle bankruptcy with the actual creditor or creditors.

## Live table timing

An unclaimed trade window normally stays open for three seconds at a table with human or agent seats. The table can set a wait from zero to sixty seconds. When it expires, the seat passes. Requesting a trade in that window replaces the short window timer with the seat's normal decision timer.

Agents normally have sixty seconds per decision. A table can change that limit or disable it. Human seats have a decision timer only when autopilot is enabled. Without autopilot, an agent who runs out of time passes in a trade window, pays interest only on a mortgage takeover, declares bankruptcy if that is the only legal action, or otherwise resigns with normal debt settlement.

With autopilot enabled, a missed decision puts the seat under passive control. The autopilot rolls, sends purchases to auction, passes in auctions and windows, rejects offers, and uses the bank to raise money for debts. It declares bankruptcy when necessary. Take back control with your seat credential, or play an available move yourself. During your own debt decision, full bank liquidation mortgages eligible titles and sells buildings until the current debt is paid or no more cash can be raised.

## Model players

Model players always choose the best-scoring legal move or candidate according to their policy (greedy selection). This applies to tables, tournaments, combined model players and games controlled through agents. There is no model sampling setting in play. Dice and card draws remain random.

## Final places

Each solvent seat ranks above every bankrupt seat. At a round limit, rank solvent seats by cash plus the purchase prices of their titles, counting mortgaged titles at half price, plus the original cost of their buildings. A hotel counts as five building levels. Jail release cards have no cash value. Equal net worth goes to the lower seat number. Among bankrupt seats, a player eliminated later places higher. The default table has no round limit.

# Dice Circuit — Base Rules

## Objective

Build a score in five coloured areas of your own sheet. Choose dice, complete rows and tracks, and use immediate bonuses to create chains. Your final score is the sum of the five areas plus any earned endgame-symbol points. The highest score wins.

## Components and setup

Use six six-sided dice: yellow, blue, green, orange, purple, and white. Each player has a score sheet. The exact sheet (fields, thresholds, multipliers, and rewards) is available from the board data: use the MCP tool `get_board` with `game_id: "dice_circuit"`, the REST endpoint `GET /api/games/dice_circuit/board`, or the in-game sheet view.

Action descriptions number rows, columns, and track fields starting at 1. Machine `target` indices start at 0: for example, yellow "row 3, column 4" has `target: [2, 3]`, and track "field 1" has `target: 0`. Blue targets are the printed values (2–12), not field indices.

The game supports one to four players. Play six rounds with one or two players, five with three, and four with four. In multiplayer, choose a starting player at random. During a round, each player takes one active turn, proceeding clockwise.

At the beginning of a round, every player receives the round reward before regular dice choices:

| Round | Reward |
|---|---|
| 1 | One reroll action |
| 2 | One extra-die action (+1) |
| 3 | One reroll action |
| 4 | Choose one black cross bonus or one black 6 bonus |
| 5–6 | No starting reward; these rounds exist only at the applicable player counts |

Resolve the round-4 bonus and any resulting bonus chain before regular dice choices.

## Active turn

1. Roll all six dice. Choose an available die and make a legal entry in its colour area. The white die can be used for any colour.
2. Keep the chosen physical die in a chosen-die space on your sheet. Move every other available die showing a strictly lower value to the tray. Equal values remain available.
3. Roll the remaining available dice and repeat. There are at most three regular roll opportunities in an active turn. Dice already kept or on the tray are not rolled again.
4. After the final regular opportunity, move all remaining unchosen dice to the tray. If no dice remain available earlier, regular choices end earlier.

A legal entry cannot be replaced by voluntarily passing. A reroll action, if available, can be used as described below.

### No legal entry

If none of the current roll's results can be used and the active player does not use an available reroll action, no entry is made and that regular opportunity is spent. No die is moved to the tray because of this failed opportunity. On the next regular roll, roll all dice that are still available. After the final regular opportunity, move all remaining unchosen dice to the tray as usual.

For example, on the second regular roll, suppose the remaining dice are green 2 and purple 3, the next green threshold is 5, the last purple entry is 5, and no rerolls remain. Neither die can be entered, so make no entry and keep both available for the third regular roll.

This does not cancel earlier entries or automatically skip a later turn. The applicable extra-die and passive phases still follow.

## Passive choices

After the active player's regular choices, each other player uses one die from the tray for a legal entry on their own sheet. Choosing a die does not remove it: several passive players can use the same physical die.

If no tray die can make any legal entry, the passive player may instead use a die kept on the active player's sheet. This exception costs no +1 and is not available merely because a player prefers a kept die over a usable tray die.

Score sheets are public.

## The five scoring areas

### Yellow

Cross out one unmarked number matching the chosen yellow value. When the number appears twice, choose one occurrence. The four preprinted crosses are already filled. Completed columns score the printed amounts; completed rows and the marked diagonal grant their indicated rewards. Each row or column reward is earned once.

### Blue

Always use the sum of the current blue and white dice. This applies whether you select blue or use white as blue, and regardless of where the other die is located. Cross out that sum if it is unmarked. The score depends on the number of marked blue fields. Completed rows and columns grant their indicated rewards.

### Green

Work from left to right without skipping fields. The die value must meet the printed minimum for the next field. The score printed above the last marked field is your green score. Reaching a reward field grants that reward.

### Orange

Write the value in the next field from left to right. There is no increasing-value requirement. Apply a field's multiplier when entering a value; for example, entering 4 on a ×2 field records 8. Your orange score is the sum of recorded values. Reaching a reward field grants that reward.

### Purple

Write values from left to right without skipping fields. The first value can be any die value; each later value must exceed the previous one. After a 6, any value can follow, including another 6. Your purple score is the sum of recorded values. Reaching a reward field grants that reward.

## Immediate bonuses and chains

A coloured cross bonus marks any available field in yellow or blue, or the next field in green, according to the bonus colour. A coloured number bonus writes that number in the next orange or purple field, applying any multiplier. The black cross offers yellow, blue, or green; the black 6 offers orange or purple.

Green cross bonuses (green or black) bypass the printed green minimum: mark the next field without a die-value check. Purple number bonuses, including the purple or black 6, obey the normal increasing rule. A 6 can follow any value below 6, and another 6 can follow a 6 because the previous 6 resets the rule. Bonus entries never skip fields or enter a full track.

Use entry bonuses immediately; they cannot be saved. A bonus entry may earn another bonus, which is resolved as part of the same chain.

If a bonus has no legal target, it expires without an entry.

If one entry earns several bonuses at the same time, choose which bonus to resolve first. Complete that bonus and its entire resulting chain before returning to the other bonuses from that entry. Then choose the next pending bonus. The same rule applies to branches within a chain. Do not interrupt a chain with another pending branch, a reroll action, or an extra-die action. Newly earned action resources are recorded for use at their permitted time.

The Boardcraft board emblem denotes an endgame bonus. It grants no immediate extra move. At game end, each earned emblem scores the value of your lowest-scoring colour area. If an area scores zero, each emblem scores zero.

### Automatic steps

The engine rolls dice and advances turns, rounds and phases automatically when the preceding choices are complete. It also resolves a forced bonus choice when only one bonus option or one legal target remains. Choices with multiple alternatives stay with the player. These automatic steps are visible in the events for your view, including dice rolls, phase transitions and `bonus_auto_resolved` entries; they do not require an extra action.

## Action resources

Each resource has seven slots: mark the next empty slot when it is earned and cross out the oldest uncrossed mark when it is spent, without erasing any marks.

### Reroll

Only the active player can spend a reroll action. Reroll all dice from the current roll together. You cannot keep some of those dice unchanged, or bring back dice already kept or on the tray. Spending a reroll resource is distinct from taking the next regular roll opportunity.

### Extra die (+1)

The active player may use +1 after finishing regular choices. A passive player may use +1 after their regular passive choice. Spend one resource to make another legal entry using any of the six dice, including a die used for a regular entry.

You may spend several +1 resources in that turn, but may use each physical die as an extra die only once during that turn. Another player's use of the same die does not use up your allowance. A resource earned within a bonus chain cannot interrupt that chain.

## End of the game and ties

Finish the final active turn and the passive players' choices in the final round, including available extra-die actions. Unused reroll resources do not score.

Add the five area scores and the endgame-emblem score. The highest total wins. If totals tie, compare the tied players' highest individual colour-area scores. If this still does not identify a winner, the victory is shared.

## Model players

Model players always choose the legal action with the highest policy score (greedy or argmax), with the lowest action index breaking equal-score ties. This applies to tables, tournaments and games controlled through agents. There is no model sampling setting in play. Dice rolls remain random; a model's preferred move is not a guarantee of optimal strategy.

## Solo

Play six rounds, alternating your active turn and a simulated passive turn. For the passive turn, roll all six dice and put the three lowest on the tray. Break equal-value placement ties using a random proximity-to-tray ordering. Reroll actions cannot be used during the passive turn.

Use one of the three dice on the tray for a legal entry. If none of those three dice can make an entry, you may choose one of the other three dice from that same simulated roll, with no +1 cost. This exception is available only when none of the three tray dice can be used.

The goal is to maximise final points. A solo placement of first is not a measure of playing strength.

## Examples and reading the sheet

- If blue shows 2 and white shows 4, a blue entry crosses out 6, not 2 or 4.
- A purple sequence of 2, 5, 6, 3 is legal because a 6 resets the increasing-value requirement.
- Two simultaneous bonus chains can enter different numbers onto orange multipliers in different orders. Choose a chain, finish it completely, and then return to the other pending chain.

"""Small reproducible baselines using permitted views, with no strength claim."""
import random
from boardcraft.contracts import BotChoice
from .actions import apply_entry, describe_action, entry_from_observation
from .scoring import score_sheet
from .visible import visible_sheet


class RandomBot:
    def __init__(self, seed: int):
        self._random = random.Random(seed)

    def choose(self, observation, legal_actions):
        if not legal_actions:
            raise ValueError('A bot needs at least one legal action')
        selected = self._random.choice(legal_actions)
        return BotChoice(selected, 'Choose uniformly from the visible legal actions.')


class GreedyBot(RandomBot):
    """Immediate score, printed progress and resources; not a search engine."""

    @staticmethod
    def _entry_value(observation, selected):
        return entry_from_observation(observation, selected)

    def _utility(self, observation, selected):
        if selected.kind in ('select_die', 'extra_die', 'place_bonus'):
            before = visible_sheet(observation.data['sheets'][observation.viewer])
            after = before.copy()
            rewards = apply_entry(after, self._entry_value(observation, selected))
            resource_gain = (after.rerolls-before.rerolls) + (after.extras-before.extras) + (after.emblems-before.emblems)
            entry_rewards = sum(r.kind not in ('reroll', 'extra', 'endgame') for r in rewards)
            return score_sheet(after).total-score_sheet(before).total + .25 + 1.5*resource_gain + entry_rewards
        if selected.kind in ('choose_bonus', 'choose_round_bonus'):
            return 0.0
        return {'reroll': -.1, 'finish_extra': -1.0, 'decline_reroll': -2.0}[selected.kind]

    def choose(self, observation, legal_actions):
        if not legal_actions:
            raise ValueError('A bot needs at least one legal action')
        scored = [(self._utility(observation, action), action) for action in legal_actions]
        best = max(value for value, action in scored)
        ties = sorted((action for value, action in scored if value == best), key=lambda action: action.id)
        selected = self._random.choice(ties)
        return BotChoice(selected, f'Visible score, progress and resource heuristic {best:.2f}. {describe_action(selected, observation)}')


class StrategistBot(RandomBot):
    """Exact immediate bonus chains with discounted goals and resource reserves."""

    goal_discount = .55
    chain_weight = .5
    balance_weight = .35
    purple_weight = .6
    opportunity_value = 6.
    area_share = .25

    def choose(self, observation, legal_actions):
        from .strategist import Evaluation
        if not legal_actions:
            raise ValueError('A bot needs at least one legal action')
        evaluation = Evaluation(observation, self)
        scored = [(evaluation.move_value(a), a) for a in legal_actions if a.kind != 'reroll']
        best = max((value for value, _ in scored), default=0.)
        reroll = next((a for a in legal_actions if a.kind == 'reroll'), None)
        if reroll is not None and best < evaluation.reroll_threshold():
            return BotChoice(reroll, 'Reroll: visible choices fall below the reserved-value threshold.')
        ties = sorted((a for value, a in scored if abs(value - best) < 1e-9), key=lambda a: a.id)
        # Finishing extra choices wins a zero-value tie: resources need a gain.
        selected = next((a for a in ties if a.kind == 'finish_extra'), None)
        if selected is None:
            selected = self._random.choice(ties)
        return BotChoice(selected, f'Strategy value after the full bonus chain {best:.2f}. {describe_action(selected, observation)}')

"""Interpret a detached public sheet for the existing pure scoring rules."""
from .state import PlayerSheet


def visible_sheet(data):
    return PlayerSheet(yellow={tuple(pair) for pair in data['yellow']}, blue=set(data['blue']),
                       green=data['green'], orange=list(data['orange']), purple=list(data['purple']),
                       earned_rewards=set(data['earned_rewards']), rerolls=data['rerolls'],
                       extras=data['extras'], emblems=data['emblem_count'],
                       rerolls_used=data['rerolls_used'], extras_used=data['extras_used'])

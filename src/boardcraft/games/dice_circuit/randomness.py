"""Versioned, domain-separated keyed randomness without mutable draw order."""
import hashlib
import json

DICE = ('yellow', 'blue', 'green', 'orange', 'purple', 'white')


class KeyedRandom:
    version = '1'

    def __init__(self, seed: int) -> None:
        if type(seed) is not int or not 0 <= seed < 2**64:
            raise ValueError('Seed must be a nonnegative 64-bit integer')
        self._key = seed.to_bytes(8, 'big')

    def _number(self, domain: str, coordinates: tuple, bound: int) -> int:
        limit = 2**256 - 2**256 % bound
        nonce = 0
        while True:
            message = ','.join(['["dice-circuit-keyed-v1"', json.dumps(domain),
                                *[json.dumps(item) if type(item) is str else str(item) for item in coordinates],
                                f'{nonce}]']).encode('ascii')
            value = int.from_bytes(hashlib.sha256(self._key + message).digest(), 'big')
            if value < limit:
                return value % bound
            nonce += 1

    def die(self, round_no: int, active_seat: int, phase: str, regular_roll: int, reroll_no: int, die_id: str) -> int:
        if die_id not in DICE or phase not in ('active', 'solo_passive'):
            raise ValueError('Unknown die or roll phase')
        return 1 + self._number('die', (round_no, active_seat, phase, regular_roll, reroll_no, die_id), 6)

    def starting_seat(self, players: int) -> int:
        return self._number('starting-seat', (players,), players)

    def proximity(self, round_no: int, active_seat: int, die_id: str) -> int:
        return self._number('solo-proximity', (round_no, active_seat, die_id), 2**256)

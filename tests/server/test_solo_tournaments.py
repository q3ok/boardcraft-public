"""Solo comparisons: several single-seat tournaments on the SAME seeds run in the background and finish in time."""
import time

from boardcraft.server.service import PlayService


def solo_spec(name, seat, seeds):
    return {'name': name, 'config': {'game_id': 'dice_circuit', 'variant': 'base', 'players': 1},
            'rules_version': '1', 'engine_version': '1', 'seats': [seat], 'seeds': seeds,
            'sampler_seeds': [[i] for i in range(len(seeds))], 'rotations': [0], 'match_count': len(seeds),
            'schedule_cap': len(seeds), 'max_inflight': 4, 'max_record_bytes': 50_000_000, 'max_decisions': 200_000}


def test_three_contestants_play_the_same_200_seeds_alone_within_the_time_budget(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 1)
    seeds = [1_000 + 7 * i for i in range(200)]
    made = []
    try:
        started = time.perf_counter()
        for seat in ('random', 'greedy', 'random'):
            item = service.create_tournament(solo_spec(f'Solo: budget · {seat}', seat, seeds))
            # Starting returns at once: the games run in the background executor (200 games alone take about a minute,
            # the margin covers a loaded machine).
            begun = time.perf_counter()
            service.start_tournament(item['tournament_id'], item['creator_credential'], item['spec_sha'], item['revision'])
            assert time.perf_counter() - begun < 10.0
            made.append(item['tournament_id'])
        deadline = started + 240
        while time.perf_counter() < deadline:
            details = [service.get_tournament(t) for t in made]
            if all(d['status'] == 'completed' for d in details):
                break
            time.sleep(0.5)
        assert all(d['status'] == 'completed' for d in details), [d['status'] for d in details]
        scores = [[m['result']['scores'][0] for m in sorted(d['matches'], key=lambda m: m['ordinal'])] for d in details]
        assert all(len(s) == 200 for s in scores)
        # The same seeds and the same deterministic bot give the same scores: the comparison is paired.
        assert scores[0] == scores[2]
        assert sum(scores[1]) > sum(scores[0])
    finally:
        service.close_tournaments()


def test_a_running_solo_contestant_can_be_stopped(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 2)
    try:
        item = service.create_tournament(solo_spec('Solo: stop · greedy', 'greedy', list(range(1, 401))))
        service.start_tournament(item['tournament_id'], item['creator_credential'], item['spec_sha'], item['revision'])
        current = service.get_tournament(item['tournament_id'])
        stopped = service.stop_tournament(item['tournament_id'], item['creator_credential'], current['revision'])
        assert stopped['status'] in ('draining', 'stopping', 'stopped')
        for _ in range(120):
            if service.get_tournament(item['tournament_id'])['status'] == 'stopped':
                break
            time.sleep(0.25)
        final = service.get_tournament(item['tournament_id'])
        assert final['status'] == 'stopped'
        assert sum(m['status'] == 'completed' for m in final['matches']) < 400
    finally:
        service.close_tournaments()

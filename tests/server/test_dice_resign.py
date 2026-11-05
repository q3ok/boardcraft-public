"""Dice tables: a seated person can surrender; the table ends and its replay reconstructs the surrender."""
from boardcraft.contracts import GameConfig
from boardcraft.server.service import PlayService
from boardcraft.server.storage import digest, encode


def dice_table(service, seats):
    made = service.create_game(GameConfig('dice_circuit', 'base', len(seats)), seats)
    table = made['table_id']
    tokens = [service.join_game(table, i)['credential'] if kind in ('human', 'agent') else None
              for i, kind in enumerate(seats)]
    service.start_game(table, made['creator_credential'])
    return table, tokens


def test_person_surrenders_and_the_replay_ends_with_the_surrender(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 9)
    table, tokens = dice_table(service, ['human', 'human'])
    assert service._read_state(table)['record_version'] == '3'
    for _ in range(3):
        moves = [service.get_actions(table, token) for token in tokens]
        actor = next(i for i, m in enumerate(moves) if m['actions'])
        service.apply_action(table, tokens[actor], moves[actor]['decision_id'], moves[actor]['actions'][0]['id'])
    moves = [service.get_actions(table, token) for token in tokens]
    assert all([c['kind'] for c in m['commands']] == ['resign'] and 'expected_revision' in m for m in moves)
    assert service.get_actions(table)['commands'] == []
    waiting = next(i for i, m in enumerate(moves) if not m['actions'])
    service.submit_command(table, tokens[waiting], moves[waiting]['expected_revision'], 'resign', {})
    result = service.get_result(table)['result']
    assert result['reason'] == 'resigned' and result['placements'][waiting] == 2
    assert all(service.get_actions(table, t)['commands'] == [] for t in tokens)
    for token in (None, *tokens):
        last = service.get_replay(table, token)['total_positions'] - 1
        frame = service.get_replay(table, token, position=last)
        assert [e['kind'] for e in frame['events']] == ['resigned', 'game_finished']
        assert frame['observation']['data']['resigned'] == waiting
        timeline = service.get_replay_timeline(table, token)
        assert timeline['total_positions'] == last + 1


def test_agents_and_tables_from_before_surrender_get_no_command(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 9)
    table, tokens = dice_table(service, ['agent', 'human'])
    assert service.get_actions(table, tokens[0])['commands'] == []
    assert [c['kind'] for c in service.get_actions(table, tokens[1])['commands']] == ['resign']
    # A table started before surrender existed keeps its version-2 history, which cannot hold a seat operation.
    with service.store.transaction(True) as db:
        state = service.store.load(db, table)
        del state['record_version']
        service.store.save(db, table, state)
        bootstrap = service.store.checked(db.execute('SELECT payload, checksum FROM bootstrap WHERE table_id=?', (table,)).fetchone())
        for key in ('record_version', 'rules_version', 'engine_version'):
            bootstrap.pop(key, None)
        payload = encode(bootstrap)
        db.execute('UPDATE bootstrap SET payload=?, checksum=? WHERE table_id=?', (payload, digest(payload), table))
    assert service.get_actions(table, tokens[1])['commands'] == []

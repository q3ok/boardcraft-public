"""Creator-only table administration: unlisting (deleting) a table and seat presentation in the lobby.

Deleting removes a table from the public table list. A completed table keeps its game record, so its replay stays
available, as the shared design promises ("the game recording stays"). A table that is being played cannot be deleted.
Seat presentation sets a display name and a token (a game-specific piece, for example a Rentcraft animal) before the
game starts; both are presentation only and never change the rules.
"""
import re

from pydantic import BaseModel, ConfigDict, Field

from .views import Conflict, seat_name, seats_view

TOKEN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class SeatPresentation(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    seat: int = Field(ge=0)
    display_name: str | None = Field(default=None, min_length=1, max_length=40)
    token: str | None = Field(default=None, min_length=1, max_length=32)


class AutostartInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    on: bool


class PresentationInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    seats: list[SeatPresentation] = Field(min_length=1, max_length=16)


def delete_table(service, table_id, credential):
    with service.store.transaction(True) as db:
        state = service.store.load(db, table_id)
        service._creator(state, credential)
        if state['status'] == 'playing':
            raise Conflict('A table in play cannot be deleted; finish or leave the game first')
        state['hidden'] = True
        service.store.save(db, table_id, state)
        if state['status'] == 'completed':
            # The completed summary keeps its replay entry, so the game stays in the replay library.
            from . import directory
            module = service.module(state['config']['game_id'])
            directory.write_summary(db, table_id, directory.prepare_summary(
                table_id, state, engine=service._engine(state), rules_version=module.rules_version,
                engine_version=module.engine_version))
    return {'table_id': table_id, 'deleted': True, 'status': state['status']}


def set_presentation(service, table_id, credential, seats):
    request = PresentationInput(seats=seats)
    with service.store.transaction(True) as db:
        state = service.store.load(db, table_id)
        service._creator(state, credential)
        if state['status'] != 'lobby':
            raise Conflict('Seat names and pieces are chosen before the game starts')
        players = len(state['seats'])
        tokens = {}
        for item in request.seats:
            if item.seat >= players:
                raise ValueError('Unknown seat')
            if item.display_name is not None:
                seat_name(item.display_name)
            if item.token is not None:
                if not TOKEN.fullmatch(item.token):
                    raise ValueError('Invalid seat token')
                tokens[item.token] = tokens.get(item.token, 0) + 1
        taken = {seat.get('token') for index, seat in enumerate(state['seats'])
                 if seat.get('token') and index not in {item.seat for item in request.seats}}
        if any(count > 1 for count in tokens.values()) or taken & set(tokens):
            raise ValueError('Each seat needs its own piece')
        for item in request.seats:
            seat = state['seats'][item.seat]
            if item.display_name is not None:
                # The creator's name is final: a player or agent joining later keeps it.
                seat['display_name'] = seat_name(item.display_name)
                seat['named'] = True
            if item.token is not None:
                seat['token'] = item.token
        service.store.save(db, table_id, state)
    return {'table_id': table_id, 'seats': seats_view(state)}


def set_autostart(service, table_id, credential, on):
    """The creator arms (or disarms) the automatic start; the server starts the table once every seat is ready."""
    if type(on) is not bool:
        raise ValueError('Choose on or off')
    with service.store.transaction(True) as db:
        state = service.store.load(db, table_id)
        service._creator(state, credential)
        if state['status'] != 'lobby':
            raise Conflict('The table has already started')
        if on:
            state['autostart'] = True
        else:
            state.pop('autostart', None)
        service.store.save(db, table_id, state)
    if on:
        service.autostart_if_ready(table_id)
    return {'table_id': table_id, 'autostart': on, 'status': service._read_state(table_id)['status']}


def table_summary(service, table_id):
    """One listed table as the table list shows it (number, round, seats); an unlisted table has only its id."""
    from . import directory
    from .storage import Store
    if not isinstance(table_id, str) or not 1 <= len(table_id) <= 64:
        raise ValueError('Unknown table')
    with service.store.transaction() as db:
        row = db.execute('SELECT payload, checksum, rowid FROM table_directory WHERE table_id=?', (table_id,)).fetchone()
    if row is None:
        return {'table_id': table_id, 'number': None}
    return {**directory.public_summary(Store.checked(row[:2])), 'number': row[2]}


def register_table_admin_routes(app, service, credential):
    from fastapi import Header

    @app.post('/api/tables/{table_id}/autostart')
    def autostart(table_id: str, body: AutostartInput, authorization: str | None = Header(default=None)):
        return set_autostart(service, table_id, credential(authorization), body.on)

    @app.get('/api/tables/{table_id}/summary')
    def summary(table_id: str):
        return table_summary(service, table_id)

    @app.post('/api/tables/{table_id}/delete')
    def delete(table_id: str, authorization: str | None = Header(default=None)):
        return delete_table(service, table_id, credential(authorization))

    @app.post('/api/tables/{table_id}/presentation')
    def presentation(table_id: str, body: PresentationInput, authorization: str | None = Header(default=None)):
        return set_presentation(service, table_id, credential(authorization), [item.model_dump() for item in body.seats])

"""One durable play application used by HTTP, agents and local bot workers."""
from dataclasses import asdict
import json
import secrets
import uuid
import threading
import time
from boardcraft.contracts import GameConfig, config_data
from boardcraft.inference.catalog import (BotIncompatible, BotUnavailable, builtin_reference, choice_id, default_catalog,
    descriptor_data, reference_data, reference_from_data, validate_reference)
from boardcraft.settings import normalize_settings
from . import decision_clock
from boardcraft.registry import get_game, list_games
from .storage import Store, TableSeat, digest, encode
from .views import Conflict, actions_view, player_view, seat_name, seats_view, viewer_for
from .mutations import Mutations
from .idempotency import idempotent, issue_credential, remember


def detached(value):
    return json.loads(encode(value))


class PlayService(Mutations):
    def __init__(self, data_dir=None, *, seed_factory=None, bot_catalog=None, tournament_controller=None, clock=None,
                 open_management=False):
        self.clock = time.time if clock is None else clock
        # Open management (a private server on a trusted network): any client may start, stop, restart, delete and
        # administer any table, tournament and knockout without the creator credential. Off for the public server.
        self.open_management = bool(open_management)
        self.store = Store(data_dir)
        self._wait_hub = self.store.wait_hub
        self._seed_factory = seed_factory or (lambda: secrets.randbits(64))
        self.bot_catalog = default_catalog() if bot_catalog is None else bot_catalog
        self._resource_lock = threading.Lock()
        self._resource_checks = {}
        self._tournament_controller = tournament_controller
        self._bot_lists = {}
        self._bot_lists_lock = threading.Lock()

    def tournaments(self):
        from boardcraft.play.tournaments import TournamentController, TournamentStore
        with self._resource_lock:
            if self._tournament_controller is None:
                from .tournaments import recommended_fit
                self._tournament_controller = TournamentController(TournamentStore(self.store), catalog=self.bot_catalog,
                                                                   auto_fit=recommended_fit(),
                                                                   on_completed=self.rankings_changed)
                self._tournament_controller.store.open_management = self.open_management
            return self._tournament_controller

    def knockouts(self):
        from .knockout import Knockouts
        with self._resource_lock:
            if getattr(self, '_knockouts', None) is None:
                self._knockouts = Knockouts(self, self.store.directory / 'knockouts')
            return self._knockouts

    def recover_tournaments(self):
        """Startup work of the server: fit completed line-up tournaments that have no ranking yet and pick up
        running knockouts. Returns the IDs of the newly fitted tournaments."""
        # Lazy as before: a server that never held a tournament does not create the tournament storage here.
        with self.store.transaction() as db:
            held = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tournaments'").fetchone() is not None
        if held:
            # A draining tournament whose run died with the previous server settles to stopped (never counting partial work).
            self.tournaments().recover_orphans()
            # The stored list summaries first: the tournament list reads them right after a start.
            self.tournaments().recover_summaries()
            # Tournaments saved by an earlier server get a creation time (the list is newest first) and their
            # entrants' names (replay rows name the players).
            games = {}
            known = lambda game_id: games[game_id] if game_id in games else games.setdefault(game_id, self._known_bots(game_id))
            try:
                self.tournaments().recover_details(lambda spec: self.entrant_labels(spec, known(spec['config']['game_id'])))
            except Exception:  # noqa: BLE001 -- the next start repeats it; the other recovery steps still run
                pass
        fitted = self.tournaments().recover_fits() if held else []
        if held:
            # The global rankings follow the completed tournaments; a crash before their refresh is repaired here.
            try:
                self.refresh_rankings()
            except Exception:  # noqa: BLE001 -- the next completion or start refreshes again
                pass
            # A tournament saved to start right away that a crash left pending is started by the server itself.
            self.tournaments().recover_starts()
        if (self.store.directory / 'knockouts').exists():
            try:
                self.knockouts().list()
            except Exception:  # noqa: BLE001 -- a knockout that cannot go on reports itself when it is read
                pass
        return fitted

    def warm_up(self):
        """Startup work: reads the bot list of every game once, so the first page after a start does not load the
        bundled models."""
        for info in list_games():
            try:
                self._bot_descriptors(info.game_id)
            except Exception:  # noqa: BLE001 -- a game whose bots cannot be listed reports it on its own reads
                continue

    def close_tournaments(self):
        if getattr(self, '_knockouts', None) is not None:self._knockouts.close()
        if self._tournament_controller is not None:self._tournament_controller.close()

    def create_tournament(self, body):
        """Saves a tournament; with ``start`` the server also starts it in the same request (and, after a crash between
        the two, at its next startup), so one click is one request and a pending tournament is never left behind."""
        from boardcraft.play.tournaments import TournamentSpec
        from .tournaments import TournamentInput
        data=TournamentInput.model_validate(body).model_dump()
        start=data.pop('start',False)
        module=self.module(data['config']['game_id'])
        descriptors={item.choice_id:item for item in self._bot_descriptors(module.game_id)}
        refs=[]
        labels={}
        for choice in data.pop('seats'):
            _, ref = self._pin_choice(module, choice, descriptors)
            refs.append(reference_data(ref))
            item=descriptors.get(choice if isinstance(choice,str) else choice.get('choice_id'))
            # Every seat is saved with the name of its choice (a model version keeps the model's name).
            labels[ref.identity]=item.display_name if item is not None else self._fallback_entrant(reference_data(ref))
        data['references']=refs
        if data.get('lineups') is None:data.pop('lineups',None)
        controller=self.tournaments()
        made=controller.create(TournamentSpec.from_data(data),autostart=start,labels=labels)
        if not start:
            return made
        try:
            controller.start_saved(made['tournament_id'])
        except Exception as error:  # noqa: BLE001 -- the tournament is saved; its creator sees why it did not start
            current=controller.status(made['tournament_id'])
            if current['status']=='pending':
                return {**made,'start_error':str(error) or type(error).__name__}
        current=controller.status(made['tournament_id'])
        return {**made,'status':current['status'],'revision':current['revision']}

    def tournament_entrants(self, spec, known=None):
        """Choice IDs and names of a tournament's pinned references (in spec order), for the results tables."""
        if known is None:
            known = self._known_bots(spec['config']['game_id'])
        out = []
        for i, ref in enumerate(spec.get('references', [])):
            item = known.get(ref.get('identity'))
            if item is not None:
                out.append({'choice_id': item.choice_id, 'display_name': item.display_name})
            elif ref.get('builtin_kind') == 'hybrid':
                out.append({'choice_id': 'hybrid', 'display_name': 'Combined player'})
            elif ref.get('builtin_kind'):
                out.append({'choice_id': ref['builtin_kind'], 'display_name': ref['builtin_kind'].replace('_', ' ').capitalize()})
            else:
                out.append({'choice_id': f'entrant-{i + 1}', 'display_name': f'Entrant {i + 1}'})
        return out

    @staticmethod
    def _fallback_entrant(ref):
        from .leaderboard import _fallback_name
        return _fallback_name(ref)

    def entrant_labels(self, spec, known=None):
        """Display names of a tournament's pinned references by identity (the catalog name, else a fallback)."""
        if known is None:
            known = self._known_bots(spec['config']['game_id'])
        out = {}
        for ref in spec.get('references', []):
            identity = ref.get('identity')
            if type(identity) is str:
                item = known.get(identity)
                out[identity] = item.display_name if item is not None else self._fallback_entrant(ref)
        return out

    def _known_bots(self, game_id):
        try:
            return {item.reference.identity: item for item in self._bot_descriptors(game_id)}
        except Exception:
            return {}

    def _labelled(self, item, known=None):
        # A tournament's references never change: its names are looked up once (polling stays cheap for the games).
        cache = self.__dict__.setdefault('_entrant_labels', {})
        key = item.get('spec_sha') or encode(item['spec'])
        if key not in cache:
            if len(cache) > 512:
                cache.clear()
            game_id = item['spec']['config']['game_id']
            cache[key] = self.tournament_entrants(item['spec'], None if known is None else known(game_id))
        return {**item, 'entrants': [dict(e) for e in cache[key]], **self.management()}

    def management(self):
        """What any client may do here: with open management every client manages everything; otherwise only the
        holder of a creator credential (which the server cannot know on a read)."""
        return {'management': 'open' if self.open_management else 'credential', 'can_manage': self.open_management}

    def _creator(self, state, credential):
        if self.open_management:
            return
        from .views import creator_for
        creator_for(state, credential)

    def list_tournaments(self):
        """Compact rows from the stored summaries (progress and per-entrant results); matches are read per tournament."""
        games = {}
        # The bot list of each game is read at most once per listing (only for tournaments not named yet).
        known = lambda game_id: games[game_id] if game_id in games else games.setdefault(game_id, self._known_bots(game_id))
        return [self._labelled(t, known) for t in self.tournaments().list_brief()]
    def tournament_matches(self,id,offset=0,limit=100):return self.tournaments().match_page(id,offset,limit)
    def recover_tournament_summaries(self):
        with self.store.transaction() as db:
            held = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tournaments'").fetchone() is not None
        return self.tournaments().recover_summaries() if held else []
    @staticmethod
    def tournament_schema():
        from .tournaments import tournament_schema
        return tournament_schema()
    def get_tournament(self,id,matches=True):
        return self._labelled(self.tournaments().status(id) if matches else self.tournaments().brief(id))
    def start_tournament(self,id,credential,spec_sha,revision):return self.tournaments().start(id,credential,spec_sha,revision)
    def restart_tournament(self,id,credential,spec_sha,revision):return self.tournaments().restart(id,credential,spec_sha,revision)
    def stop_tournament(self,id,credential,revision):return self.tournaments().request_stop(id,credential,revision)
    def delete_tournament(self,id,credential,revision):
        out=self.tournaments().delete(id,credential,revision)
        self.rankings_changed()
        return out
    def fit_tournament_ratings(self,id,credential,revision,fit):return self.tournaments().fit(id,credential,revision,fit)
    def get_tournament_ratings(self,id):return self.tournaments().ratings(id)
    def get_tournament_match_replay(self,id,match_id,position=0):return self.tournaments().match_replay(id,match_id,position)

    def rankings_changed(self):
        """Refreshes the global rankings in the background (coalesced: one refresh runs, a later request repeats it)."""
        with self._resource_lock:
            self._rankings_dirty = True
            if getattr(self, '_rankings_worker', None) is not None and self._rankings_worker.is_alive():
                return
            def work():
                while True:
                    with self._resource_lock:
                        if not self._rankings_dirty:
                            self._rankings_worker = None
                            return
                        self._rankings_dirty = False
                    try:
                        self.refresh_rankings()
                    except Exception:  # noqa: BLE001 -- the startup recovery refreshes again
                        pass
            self._rankings_worker = threading.Thread(target=work, name='rankings-refresh', daemon=True)
            self._rankings_worker.start()

    def refresh_rankings(self):
        """Recomputes the stored global rankings whose completed results changed. Idempotent."""
        from .leaderboard import refresh
        with self.__dict__.setdefault('_rankings_refresh_lock', threading.Lock()):
            return refresh(self)

    def global_ranking(self, game_id, players):
        from .leaderboard import read
        return read(self, game_id, players)

    def list_rankings(self, **filters):
        from .library import list_rankings
        return list_rankings(self, **filters)

    def get_ranking(self, comparison_id, result_sha):
        from .library import get_ranking
        return get_ranking(self, comparison_id, result_sha)

    def fit_rankings(self, sources, fit):
        from .library import fit_rankings
        return fit_rankings(self, sources, fit)

    def list_replays(self, **filters):
        from .library import list_replays
        return list_replays(self, **filters)

    def delete_replays(self, replays):
        """Deletes replays from the library (only on a server with open management). Deleted tournament games
        leave the global rankings, which are computed again."""
        from .library import delete_replays
        out = delete_replays(self, replays)
        if out.pop('rankings', False):
            self.rankings_changed()
        return out

    @staticmethod
    def module(game_id):
        try:
            return get_game(game_id)
        except KeyError as error:
            raise ValueError('Unknown game module') from error

    def list_games(self):
        result = []
        for info in list_games():
            item = asdict(info)
            description = getattr(self.module(info.game_id), 'description', None)
            if isinstance(description, str):
                item['description'] = description
            result.append(item)
        return detached(result)

    def list_tables(self, *, game_id=None, status=None, offset=0, limit=25):
        from .directory import listing, validate_filters
        validate_filters(game_id=game_id, status=status, offset=offset, limit=limit)
        if game_id is not None:
            self.module(game_id)
        out = listing(self.store, game_id=game_id, status=status, offset=offset, limit=limit)
        if self.open_management:
            out = {**out, **self.management(), 'items': [{**item, 'can_manage': True} for item in out['items']]}
        return out

    def _bot_descriptors(self, game_id):
        module = self.module(game_id)
        try:
            descriptors = self.bot_catalog.list_bots(game_id)
        except Exception:
            raise ValueError('Available bot choices could not be read') from None
        ids = set()
        for item in descriptors:
            descriptor_data(item)
            validate_reference(item.reference, module)
            if item.choice_id in ('human', 'agent') or item.choice_id in ids:
                raise ValueError('Duplicate or reserved bot choice ID')
            if item.reference.kind == 'model' and item.choice_id in module.info().bot_kinds:
                raise ValueError('Reserved builtin bot choice ID')
            ids.add(item.choice_id)
        return descriptors

    def _provider_pin(self, game_id, selected):
        try:
            return self.bot_catalog.pin(game_id, selected)
        except Exception:
            raise ValueError('Selected bot choice is unavailable') from None

    def _provider_resolve(self, reference, seed):
        try:
            from boardcraft.inference.catalog import require_greedy_reference
            require_greedy_reference(reference)
            return self.bot_catalog.resolve(reference, seed)
        except BotUnavailable as error:
            public_type = BotIncompatible if isinstance(error, BotIncompatible) else BotUnavailable
            raise public_type(error.reference) from None
        except Exception:
            raise BotUnavailable(reference) from None

    def _catalog_revision(self, game_id):
        revision = getattr(self.bot_catalog, 'catalog_revision', None)
        if not callable(revision):
            return None
        try:
            value = revision(game_id)
            hash(value)
        except Exception:  # noqa: BLE001 -- without a revision the list is simply built again
            return None
        return value

    def list_bots(self, game_id):
        """Bot choices of a game. A catalog reporting ``catalog_revision(game_id)`` (any hashable value that changes
        whenever its listing may change) is listed and validated once per revision; reads return that result."""
        revision = self._catalog_revision(game_id)
        # Module-owned descriptions are part of the list (compared by identity, as the module holds them).
        descriptions = getattr(self.module(game_id), 'bot_descriptions', None)
        if revision is not None:
            with self._bot_lists_lock:
                cached = self._bot_lists.get(game_id)
            if cached is not None and cached[0] == (self.bot_catalog, revision) and cached[2] is descriptions:
                return detached(cached[1])
        result = self._build_bot_list(game_id)
        # Kept only for a revision unchanged across the build. Listing may itself change it (a source found failing
        # on its first load), so one more build settles that case.
        for _ in range(2):
            if revision is None:
                break
            after = self._catalog_revision(game_id)
            if after == revision:
                with self._bot_lists_lock:
                    self._bot_lists[game_id] = ((self.bot_catalog, revision), result, descriptions)
                break
            revision, result = after, self._build_bot_list(game_id)
        return detached(result)

    def _build_bot_list(self, game_id):
        descriptions = getattr(self.module(game_id), 'bot_descriptions', {})
        result = []
        for descriptor in self._bot_descriptors(game_id):
            item = descriptor_data(descriptor)
            description = descriptions.get(descriptor.reference.builtin_kind) if descriptor.kind == 'builtin' else None
            if isinstance(description, str):
                item['description'] = description
            result.append(item)
        # A host catalog may group saved versions of one model (final, checkpoints, latest save) under it.
        from boardcraft.inference.catalog import nest_versions
        versions = getattr(self.bot_catalog, 'bot_versions', None)
        try:
            result = nest_versions(result, versions(game_id) if callable(versions) else None)
        except Exception:  # noqa: BLE001 -- optional grouping never hides the plain list
            pass
        return detached(result)

    def _pin_choice(self, module, choice, descriptors):
        from boardcraft.games.rentcraft.inference.hybrid import is_hybrid
        if is_hybrid(choice):
            from boardcraft.contracts import BotDescriptor
            reference = self._provider_pin(module.game_id, choice)
            validate_reference(reference, module)
            return BotDescriptor(choice.strip(), "Combined player", reference.kind, reference), reference
        if isinstance(choice, dict):
            if set(choice) != {'choice_id', 'options'} or type(choice['options']) is not dict:
                raise ValueError('Invalid bot selection')
            kind = choice_id(choice['choice_id'])
        else:
            kind = choice_id(choice)
        descriptor = descriptors.get(kind)
        if descriptor is None:
            raise ValueError('Unknown or unavailable bot choice')
        if isinstance(choice, dict):
            if descriptor.kind != 'builtin':
                raise ValueError('Only builtin seats accept options')
            expected = builtin_reference(module, kind, descriptor.reference.behavior_version, choice['options'])
            try:
                reference = self.bot_catalog.pin_builtin(module.game_id, kind, choice['options'])
            except Exception:
                raise ValueError('Configured builtin choice is unavailable') from None
        else:
            expected = descriptor.reference
            reference = self._provider_pin(module.game_id, kind)
        from boardcraft.inference.catalog import require_greedy_reference
        require_greedy_reference(reference)
        validate_reference(reference, module)
        if reference != expected:
            raise Conflict('Bot choice changed; read available bots and retry')
        return descriptor, reference

    def _pin_seats(self, module, seats):
        descriptors = {item.choice_id: item for item in self._bot_descriptors(module.game_id)}
        result = []
        for choice in seats:
            agent = choice == 'agent'
            if choice == 'human' or agent:
                selected = TableSeat('human', None, 'Agent' if agent else 'Human')
            else:
                descriptor, reference = self._pin_choice(module, choice, descriptors)
                self._provider_resolve(reference, 0)
                selected = TableSeat(descriptor.choice_id, reference, descriptor.display_name)
            result.append({'kind': selected.kind, 'bot_ref': None if selected.bot_ref is None else reference_data(selected.bot_ref),
                           'display_name': selected.display_name, 'hash': None,
                           'availability': None if selected.bot_ref is None else {'reference': reference_data(selected.bot_ref), 'status': 'available'}})
            if agent:
                result[-1]['control_kind'] = 'agent'
        return result

    def _migrate_seats(self, table, state):
        """Pin only explicitly supported legacy baselines, once under revision CAS."""
        for _ in range(16):
            if state['status'] == 'completed' or all('bot_ref' in item for item in state['seats']):
                return state
            expected = state['revision']
            module = self.module(state['config']['game_id'])
            baseline = {item.kind: item for item in module.bot_info() if item.legacy_behavior_version is not None}
            for item in state['seats']:
                if 'bot_ref' in item:
                    continue
                if item['kind'] == 'human':
                    item.update(bot_ref=None, display_name='Human', availability=None)
                else:
                    info = baseline.get(item['kind'])
                    if info is None:
                        raise ValueError('Unsupported legacy bot seat')
                    ref = builtin_reference(module, info.kind, info.legacy_behavior_version)
                    self._provider_resolve(ref, 0)
                    item.update(bot_ref=reference_data(ref), display_name=info.display_name,
                                availability={'reference': reference_data(ref), 'status': 'available'})
            state['revision'] += 1
            payload = encode(state)
            with self.store.transaction(True) as db:
                changed = db.execute('UPDATE tables SET payload=?,checksum=?,revision=? WHERE table_id=? AND revision=?',
                    (payload, digest(payload), state['revision'], table, expected)).rowcount
                if changed:
                    return state
                state = self.store.load(db, table)
        raise Conflict('Table is busy; retry the operation')

    def _resolve_seats(self, state):
        for item in state['seats']:
            if item['kind'] != 'human':
                reference = reference_from_data(item['bot_ref'])
                validate_reference(reference, self.module(state['config']['game_id']))
                self._provider_resolve(reference, 0)

    def refresh_bot_resources(self, table_id, *, force=False):
        """Bounded resource-only checks, irrespective of eligible decisions."""
        now = time.monotonic()
        with self._resource_lock:
            if not force and now - self._resource_checks.get(table_id, -1e9) < 1:
                return
            self._resource_checks[table_id] = now
        state = self._read_state(table_id)
        if state['status'] == 'completed':
            return
        statuses = {}
        for item in state['seats']:
            if item['kind'] == 'human':
                continue
            ref = reference_from_data(item['bot_ref'])
            try:
                self._provider_resolve(ref, 0)
                status = 'available'
            except BotUnavailable as error:
                # Never attach an error from another version to this seat.
                if error.reference != ref:
                    continue
                status = 'unavailable'
            statuses[ref] = {'reference': reference_data(ref), 'status': status}
        for _ in range(16):
            state = self._read_state(table_id)
            if state['status'] == 'completed':
                return
            changed = False
            for item in state['seats']:
                if item.get('bot_ref') is None:
                    continue
                ref = reference_from_data(item['bot_ref'])
                if ref in statuses and item.get('availability') != statuses[ref]:
                    item['availability'] = statuses[ref]
                    changed = True
            if not changed:
                return
            expected = state['revision']
            state['revision'] += 1
            payload = encode(state)
            with self.store.transaction(True) as db:
                if db.execute('UPDATE tables SET payload=?,checksum=?,revision=? WHERE table_id=? AND revision=?',
                    (payload, digest(payload), state['revision'], table_id, expected)).rowcount:
                    return

    def get_rules(self, game_id):
        return self.module(game_id).rules_text()

    def get_sheet(self, game_id):
        return self.module(game_id).sheet_spec()

    def get_board(self, game_id):
        return self.module(game_id).board_spec()

    def get_settings(self, game_id):
        module = self.module(game_id)
        return {'table': module.settings_schema(), 'simulation_defaults': module.simulation_defaults(),
                'live': decision_clock.live_schema(game_id),
                'bots': {d.choice_id: module.bot_settings_schema(d.reference.builtin_kind)
                         for d in self._bot_descriptors(game_id) if d.kind == 'builtin'}}

    @staticmethod
    def _config(module, config):
        return GameConfig(config.game_id, config.variant, config.players,
                          normalize_settings(module.settings_schema(), config.settings))

    @idempotent
    def create_game(self, config, seats, *, live=None, visibility='invite', seat_names=None):
        if visibility not in ('invite', 'listed'):
            raise ValueError('Invalid table visibility')
        if not isinstance(config, GameConfig):
            raise ValueError('A valid game configuration is required')
        module = self.module(config.game_id)
        info = module.info()
        if config.variant not in info.variants or not info.min_players <= config.players <= info.max_players:
            raise ValueError('Unsupported game configuration')
        if not isinstance(seats, list) or len(seats) != config.players:
            raise ValueError('Each seat requires a supported human or bot kind')
        if seat_names is not None:
            if not isinstance(seat_names, list) or len(seat_names) != len(seats):
                raise ValueError('Give one seat name (or null) for each seat')
            seat_names = [None if name is None else seat_name(name) for name in seat_names]
        config = self._config(module, config)
        pinned_seats = self._pin_seats(module, seats)
        for item, name in zip(pinned_seats, seat_names or ()):
            if name is not None:
                # The creator's name is final: a player or agent joining later keeps it.
                item.update(display_name=name, named=True)
        options = decision_clock.live_settings(module.game_id, pinned_seats, {} if live is None else live)
        seed = self._seed_factory()
        module.new(config, seed)
        table, creator = uuid.uuid4().hex, issue_credential()
        state = {'config': config_data(config), 'seats': pinned_seats,
                 'creator_hash': digest(creator), 'status': 'lobby', 'seed': seed, 'snapshot': None, 'steps': 0,
                 'cursors': {str(viewer): 0 for viewer in range(-1, config.players)}, 'revision': 0}
        if options:
            state['live'] = options
        # A game whose seat operations exist only for people at live tables (Dice surrender) records a new table with
        # a person in the operation format too; bot tables, simulations and tournaments keep the module's own version.
        people = any(item['kind'] == 'human' and item.get('control_kind') != 'agent' for item in pinned_seats)
        if (info.capabilities.seat_operations or info.capabilities.parameterized_actions
                or getattr(module, 'live_seat_operations', False) and people):
            state['record_version'] = '3'
        with self.store.transaction(True) as db:
            self.store.save(db, table, state, create=True, listed=visibility == 'listed',
                            created_at=self.clock() if visibility == 'listed' else 0)
            response = {'table_id': table, 'config': config_data(config), 'seats': seats_view(state), 'creator_credential': creator}
            remember(db, self.store, response)
        return response

    @idempotent
    def join_game(self, table_id, seat, name=None):
        if name is not None:
            name = seat_name(name)
        with self.store.transaction(True) as db:
            state = self.store.load(db, table_id)
            if state['status'] != 'lobby':
                raise Conflict('Seats can only be joined in the lobby')
            if type(seat) is not int or not 0 <= seat < len(state['seats']):
                raise ValueError('Unknown seat')
            item = state['seats'][seat]
            if item['kind'] != 'human' or item['hash'] is not None:
                raise Conflict('Seat is already occupied')
            credential = issue_credential()
            item['hash'] = digest(credential)
            if name is not None and not item.get('named'):
                item['display_name'] = name
            self.store.save(db, table_id, state)
            response = {'table_id': table_id, 'seat': seat, 'credential': credential, 'display_name': item.get('display_name')}
            remember(db, self.store, response)
        if state.get('autostart'):
            try:
                self.autostart_if_ready(table_id)  # the last person to sit down starts an armed table
            except Exception:  # noqa: BLE001 -- the table poller tries again
                pass
        return response

    def _engine(self, state):
        if state['snapshot'] is None:
            return None
        try:
            engine = self.module(state['config']['game_id']).restore(state['snapshot'])
            if config_data(engine.config) != state['config']:
                raise ValueError('Configuration mismatch')
            return engine
        except (ValueError, TypeError, KeyError) as error:
            raise ValueError('Stored table data is corrupt') from error

    def get_view(self, table_id, credential=None):
        state = self._read_state(table_id)
        viewer = viewer_for(state, credential)
        out = self._envelope(table_id, state, self._engine(state), viewer)
        return {**out, 'can_manage': True} if self.open_management else out

    def _envelope(self, table, state, engine, viewer):
        from .envelope import envelope
        return detached(envelope(table, state, engine, viewer, self.module(state['config']['game_id']), self.clock()))

    async def wait_for_decision(self, table_id, credential=None, *, mode='actionable', after_change_token=None, timeout=25.):
        from .waiting import wait_for_decision
        return await wait_for_decision(self, table_id, credential, mode=mode, after_change_token=after_change_token, timeout=timeout)

    def get_actions(self, table_id, credential=None):
        state = self._read_state(table_id)
        viewer = viewer_for(state, credential)
        return detached(actions_view(table_id, state, self._engine(state), viewer, self.module(state['config']['game_id'])))

    def get_events(self, table_id, credential=None, cursor=0, limit=100):
        if type(cursor) is not int or cursor < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('Event cursor and page limit are out of range')
        with self.store.transaction() as db:
            state = self.store.load(db, table_id)
            viewer = viewer_for(state, credential)
            key = -1 if viewer is None else viewer
            end = db.execute('SELECT MAX(sequence) FROM journals WHERE table_id=? AND viewer=?', (table_id, key)).fetchone()[0] or 0
            if end != state['cursors'][str(key)]:
                raise ValueError('Stored table journal is corrupt')
            if cursor > end:
                raise ValueError('Event cursor is beyond this viewer journal')
            rows = db.execute('SELECT payload,checksum FROM journals WHERE table_id=? AND viewer=? AND sequence>? ORDER BY sequence LIMIT ?', (table_id, key, cursor, limit)).fetchall()
        events = [self.store.checked(row) for row in rows]
        if [event['sequence'] for event in events] != list(range(cursor + 1, min(cursor + limit, end) + 1)):
            raise ValueError('Stored table journal is corrupt')
        return {'events': events, 'next_cursor': events[-1]['sequence'] if events else cursor}

    def get_result(self, table_id, credential=None):
        state = self._read_state(table_id)
        viewer_for(state, credential)
        engine = self._engine(state)
        result = None if engine is None else engine.result()
        return {'result': None if result is None else detached(asdict(result))}

    def _replay_source(self, table_id, credential):
        with self.store.transaction() as db:
            state = self.store.load(db, table_id)
            viewer = viewer_for(state, credential)
            if state['status'] != 'completed':
                raise Conflict('Replay is available only for completed games')
            bootstrap = db.execute('SELECT payload,checksum FROM bootstrap WHERE table_id=?', (table_id,)).fetchone()
            steps = db.execute('SELECT step,payload,checksum FROM steps WHERE table_id=? ORDER BY step', (table_id,)).fetchall()
        # A completed table is immutable; the key also covers the stored rows and the seats (names, references), so a
        # changed record or roster is validated again instead of being served from the cache.
        import hashlib
        stored = hashlib.sha256(repr((tuple(bootstrap or ()), [tuple(row) for row in steps], encode(state['seats']))).encode()).hexdigest()
        return state, viewer, bootstrap, steps, (table_id, viewer, state['steps'], stored)

    def get_replay(self, table_id, credential=None, position=0):
        from .replay import replay_view
        state, viewer, bootstrap, steps, key = self._replay_source(table_id, credential)
        return replay_view(self, state, viewer, position, bootstrap, steps, key)

    def get_replay_timeline(self, table_id, credential=None):
        """Every permitted replay position at a glance (event kinds, actors and the game's summary metrics)."""
        from .replay import replay_index
        state, viewer, bootstrap, steps, key = self._replay_source(table_id, credential)
        return replay_index(self, state, viewer, bootstrap, steps, key).timeline()

    def bot_work(self, table_id):
        from .bots import prepare_work
        state = self._read_state(table_id)
        if state.get('automatic_at') is not None and self.clock() < state['automatic_at']:
            return []
        stateful = self.module(state['config']['game_id']).info().capabilities.stateful_bots
        return prepare_work(table_id, state, self._engine(state), stateful=stateful)

    def choose_bot(self, work):
        from .bots import StatefulChoice
        bot = self._provider_resolve(work.reference, work.seed)
        if work.expected_revision is None:
            return bot.choose(work.observation, work.actions)
        if work.state_before is not None:
            bot.load_state(detached(work.state_before))
        choice = bot.choose(work.observation, work.actions)
        return StatefulChoice(choice.action, choice.explanation, detached(bot.save_state()), work, meta=choice.meta)

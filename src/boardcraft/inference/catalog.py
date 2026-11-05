"""Finite local policy sources and immutable observation-only bot references."""

from dataclasses import asdict, dataclass, fields
from importlib.resources import files
import json
from pathlib import Path
import re
import threading
import time
from boardcraft.contracts import (
    BotCompatibility, BotDescriptor, BotReference, RuleBotReference,
    RuleCompatibility, _copy_json,
)
from boardcraft.registry import get_game, list_games
from boardcraft.settings import normalize_settings
from .model import load_artifact, load_game_artifact, validate_description, _unique
from .spec import architecture, digest, identities
from .bot import InferenceBot, SAMPLING_VERSION, GREEDY_SAMPLING_VERSION
from .labels import display_name

MAX_SOURCES = 256
RENT_SELECTION = "rentcraft-greedy-v1"


class BotUnavailable(ValueError):
    """A pinned resource cannot currently be resolved, without provider details."""

    def __init__(self, reference):
        self.reference = reference
        super().__init__("Selected bot version is unavailable")


class BotIncompatible(BotUnavailable):
    def __init__(self, reference):
        super().__init__(reference)
        self.args = ("Selected bot version is incompatible",)


def choice_id(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value
    ):
        raise ValueError("Invalid bot choice ID")
    return value


def compatibility(encoder):
    return BotCompatibility(**identities(encoder))


def _object(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError("Invalid bot reference fields")
    return value


def _compatibility(data):
    _object(data, (item.name for item in fields(BotCompatibility)))
    for key, value in data.items():
        if key in ("input_size", "output_size"):
            if type(value) is not int or value < 1:
                raise ValueError("Invalid bot compatibility size")
        elif (data.get("game_id") == "rentcraft" and key in ("encoder_version", "action_version")):
            if type(value) is not int or value != (1 if key == "encoder_version" else 2):
                raise ValueError("Invalid Rentcraft compatibility version")
        elif key.endswith("_hash"):
            if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value):
                raise ValueError("Invalid bot compatibility digest")
        elif not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value
        ):
            raise ValueError("Invalid bot compatibility version")
    return BotCompatibility(**data)


def _payload(reference):
    data = _reference_data(reference)
    data.pop("identity")
    domain = "boardcraft-rule-reference-v1" if isinstance(reference, RuleBotReference) else "boardcraft-bot-reference-v1"
    return {"domain": domain, **data}


def _reference_data(reference):
    if isinstance(reference, RuleBotReference):
        if any(getattr(reference, key) is not None for key in (
            "policy_sha256", "inference_format", "architecture_json", "sampling_version",
        )):
            raise ValueError("Rule references cannot contain tensor fields")
        return dict(kind=reference.kind, identity=reference.identity,
                    compatibility=asdict(reference.compatibility),
                    builtin_kind=reference.builtin_kind,
                    behavior_version=reference.behavior_version,
                    options_json=reference.options_json)
    return asdict(reference)


def _reference(kind, compat, **kwargs):
    ref = BotReference(kind, "", compat, **kwargs)
    return BotReference(kind, digest(_payload(ref)), compat, **kwargs)


def _rule_compatibility(data):
    _object(data, (item.name for item in fields(RuleCompatibility)))
    for value in data.values():
        choice_id(value)
    return RuleCompatibility(**data)


def rule_compatibility(module):
    return _rule_compatibility(dict(game_id=module.game_id, rules_version=module.rules_version,
                                    observation_version=module.observation_version,
                                    action_version=module.action_version))


def _options_json(options):
    return json.dumps(options, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def builtin_reference(module, kind, version, options=None):
    options = normalize_settings(module.bot_settings_schema(kind), {} if options is None else options)
    if module.game_id == "rentcraft" or not module.info().capabilities.neural_inference:
        kwargs = dict(builtin_kind=choice_id(kind), behavior_version=choice_id(version),
                      options_json=_options_json(options))
        compat = rule_compatibility(module)
        ref = RuleBotReference("builtin", "", compat, **kwargs)
        return RuleBotReference("builtin", digest(_payload(ref)), compat, **kwargs)
    if options:
        raise ValueError("Tensor-compatible builtin references require default settings")
    return _reference(
        "builtin",
        compatibility(module.encoder()),
        builtin_kind=choice_id(kind),
        behavior_version=version,
    )


def model_reference(loaded, *, sampling_version=None):
    # Metadata is the immutable result of the single artifact validation path.
    metadata = loaded.metadata
    network = dict(metadata["architecture"])
    if "hidden" in network:
        network["hidden"] = list(network["hidden"])
    compat = BotCompatibility(
        **{item.name: metadata[item.name] for item in fields(BotCompatibility)}
    )
    mode = _play_selection(compat) if sampling_version is None else sampling_version
    return policy_reference(compat, network, loaded.policy_sha256, metadata["format"], sampling_version=mode)


def _architecture(compat, network):
    if compat.game_id == "rentcraft":
        from boardcraft.games.rentcraft.inference.policy import architecture as rent_architecture
        return rent_architecture(network)
    return architecture(network)


def _play_selection(compat):
    return RENT_SELECTION if compat.game_id == "rentcraft" else GREEDY_SAMPLING_VERSION


def policy_reference(compat, network, policy_sha256, inference_format=1, *, sampling_version=None):
    """Identify a policy by its canonical compatibility, architecture and tensors."""
    compat = _compatibility(asdict(compat))
    network = _architecture(compat, network)
    sampling_version = _play_selection(compat) if sampling_version is None else sampling_version
    allowed = (RENT_SELECTION,) if compat.game_id == "rentcraft" else (SAMPLING_VERSION, GREEDY_SAMPLING_VERSION)
    if sampling_version not in allowed:
        raise ValueError("Unknown policy selection mode")
    if type(inference_format) is not int or inference_format != 1:
        raise ValueError("Unknown inference format")
    if not isinstance(policy_sha256, str) or not re.fullmatch(
        "[0-9a-f]{64}", policy_sha256
    ):
        raise ValueError("Invalid policy digest")
    return _reference(
        "model",
        compat,
        policy_sha256=policy_sha256,
        inference_format=inference_format,
        architecture_json=json.dumps(network, sort_keys=True, separators=(",", ":")),
        sampling_version=sampling_version,
    )


def with_model_selection(reference, selection):
    """Construct an identity, including historical references for read-only replay."""
    if reference.kind != "model":
        raise ValueError("Selection modes require a model reference")
    return policy_reference(reference.compatibility, json.loads(reference.architecture_json),
        reference.policy_sha256, reference.inference_format, sampling_version=selection)


def require_greedy_reference(reference):
    """Historical sampled model identities may be read, but never executed in play."""
    if reference.kind == "model" and reference.sampling_version != _play_selection(reference.compatibility):
        raise BotIncompatible(reference)
    return reference


def reference_data(reference):
    if not isinstance(reference, BotReference):
        raise ValueError("Expected an immutable bot reference")
    data = _reference_data(reference)
    reference_from_data(data)
    return data


def reference_from_data(data):
    if isinstance(data, dict) and "options_json" in data:
        return _rule_reference_from_data(data)
    _object(data, (item.name for item in fields(BotReference)))
    compat = _compatibility(data["compatibility"])
    if data["kind"] == "builtin":
        choice_id(data["builtin_kind"])
        choice_id(data["behavior_version"])
        if any(
            data[k] is not None
            for k in (
                "policy_sha256",
                "inference_format",
                "architecture_json",
                "sampling_version",
            )
        ):
            raise ValueError("Invalid builtin reference")
    elif data["kind"] == "model":
        if (
            data["builtin_kind"] is not None
            or data["behavior_version"] is not None
            or type(data["inference_format"]) is not int
            or data["inference_format"] != 1
            or data["sampling_version"] not in ((RENT_SELECTION,) if compat.game_id == "rentcraft" else (SAMPLING_VERSION, GREEDY_SAMPLING_VERSION))
            or not isinstance(data["policy_sha256"], str)
            or not re.fullmatch("[0-9a-f]{64}", data["policy_sha256"])
            or not isinstance(data["architecture_json"], str)
            or len(data["architecture_json"]) > 65536
        ):
            raise ValueError("Invalid model reference")
        network = _architecture(compat,
            json.loads(data["architecture_json"], object_pairs_hook=_unique)
        )
        if (
            json.dumps(network, sort_keys=True, separators=(",", ":"))
            != data["architecture_json"]
        ):
            raise ValueError("Noncanonical model architecture")
    else:
        raise ValueError("Unknown bot reference kind")
    ref = BotReference(**{**data, "compatibility": compat})
    if not isinstance(ref.identity, str) or digest(_payload(ref)) != ref.identity:
        raise ValueError("Bot reference identity mismatch")
    return ref


def _rule_reference_from_data(data):
    _object(data, ("kind", "identity", "compatibility", "builtin_kind", "behavior_version", "options_json"))
    compat = _rule_compatibility(data["compatibility"])
    if data["kind"] != "builtin":
        raise ValueError("Rule references must identify builtins")
    choice_id(data["builtin_kind"])
    choice_id(data["behavior_version"])
    if not isinstance(data["options_json"], str) or len(data["options_json"]) > 65536:
        raise ValueError("Invalid rule settings")
    options = _copy_json(json.loads(data["options_json"], object_pairs_hook=_unique))
    if not isinstance(options, dict) or _options_json(options) != data["options_json"]:
        raise ValueError("Noncanonical rule settings")
    ref = RuleBotReference(**{**data, "compatibility": compat})
    if not isinstance(ref.identity, str) or digest(_payload(ref)) != ref.identity:
        raise ValueError("Bot reference identity mismatch")
    if ref.builtin_kind == "hybrid":
        from boardcraft.games.rentcraft.inference.hybrid import component_references
        component_references(ref)
    return ref


def validate_reference(reference, module):
    try:
        validated = reference_from_data(reference_data(reference))
        neural = module.info().capabilities.neural_inference
        if isinstance(validated, RuleBotReference):
            if (neural and module.game_id != "rentcraft"
                    or validated.compatibility != rule_compatibility(module)):
                raise ValueError("Incompatible rule reference")
            if validated.builtin_kind == "hybrid":
                from boardcraft.games.rentcraft.inference.hybrid import component_references
                for child in component_references(validated).values():
                    validate_reference(child, module)
                return reference
            options = json.loads(validated.options_json)
            normalized = normalize_settings(module.bot_settings_schema(validated.builtin_kind), options)
            if _options_json(normalized) != validated.options_json:
                raise ValueError("Noncanonical bot settings")
        else:
            if not neural:
                raise ValueError("Incompatible tensor reference")
            resolver = getattr(module, "inference_profile", None)
            expected = (BotCompatibility(**resolver(validated.compatibility.encoder_id,
                        validated.compatibility.encoder_version).identities()) if callable(resolver)
                        else compatibility(module.encoder()))
            if validated.compatibility != expected:
                raise ValueError("Incompatible tensor reference")
    except (AttributeError, TypeError, ValueError, KeyError):
        raise BotIncompatible(reference) from None
    return reference


def descriptor_data(descriptor):
    choice_id(descriptor.choice_id)
    display_name(descriptor.display_name)
    if descriptor.kind != descriptor.reference.kind:
        raise ValueError("Bot descriptor kind mismatch")
    result = dict(
        choice_id=descriptor.choice_id,
        display_name=descriptor.display_name,
        kind=descriptor.kind,
        reference=reference_data(descriptor.reference),
    )
    if descriptor.description is not None:
        result["description"] = validate_description(descriptor.description)
    return result


def nest_versions(items, versions):
    """Nest optional saved versions of a model under it in a bot list.

    ``versions`` (from a catalog's optional ``bot_versions(game_id)``) maps a listed model choice to
    ``{"versions": [{"choice_id", "label"}...], "training": bool}``. Every version stays its own pinnable
    choice of the catalog; a version that is not itself a key is only hidden from the top level. Anything
    malformed leaves the plain list unchanged.
    """
    if not isinstance(versions, dict) or not versions:
        return items
    models = {item["choice_id"] for item in items if item.get("kind") == "model"}
    nested, extra = set(), {}
    for owner, entry in versions.items():
        if owner not in models or not isinstance(entry, dict):
            return items
        listed = entry.get("versions", [])
        training = entry.get("training", False)
        if type(training) is not bool or not isinstance(listed, list) or len(listed) > 64:
            return items
        clean = []
        for version in listed:
            if not isinstance(version, dict) or version.get("choice_id") not in models:
                return items
            label = version.get("label")
            if not isinstance(label, str) or not 1 <= len(label) <= 80 or not label.isprintable():
                return items
            clean.append({"choice_id": version["choice_id"], "label": label})
        extra[owner] = {"versions": clean, "training": training}
        nested.update(v["choice_id"] for v in clean if v["choice_id"] not in versions)
    return [{**item, **extra.get(item["choice_id"], {})} for item in items if item["choice_id"] not in nested]


@dataclass(frozen=True)
class NumpySource:
    metadata_path: Path
    weights_path: Path
    sampling_version: str | None = None


class RuntimeBotCatalog:
    """Policies load once; each resolution owns a separately seeded sampler."""

    def __init__(self, sources=None):
        self._sources = {}
        self._aliases = {}
        self._policies = {}
        self._loaded_sources = {}
        self._failed_sources = {}
        # Files of a source whose content failed to load (None: a transient read error, retried every second).
        self._failed_files = {}
        self._failed_stamps = {}
        self._lock = threading.RLock()
        self._revision = 0
        for alias, source in (sources or {}).items():
            self.register(alias, source)

    def register(self, alias, source):
        choice_id(alias)
        reserved = {
            "human",
            *(
                bot.kind
                for info in list_games()
                for bot in get_game(info.game_id).bot_info()
            ),
        }
        if alias in reserved or not isinstance(source, NumpySource):
            raise ValueError("Invalid or reserved model choice ID")
        if source.sampling_version not in (None, GREEDY_SAMPLING_VERSION, RENT_SELECTION):
            raise ValueError("Play model sources must use greedy selection")
        with self._lock:
            if len(self._sources) >= MAX_SOURCES and alias not in self._sources:
                raise ValueError("Too many configured model sources")
            self._sources[alias] = source
            self._revision += 1
            active_sources = {self._source_key(item) for item in self._sources.values()}
            for key in tuple(self._loaded_sources):
                if key[:2] == self._source_key(source) or key[:2] not in active_sources:
                    del self._loaded_sources[key]
            for key in tuple(self._failed_sources):
                if key[:2] == self._source_key(source) or key[:2] not in active_sources:
                    del self._failed_sources[key]
                    self._failed_files.pop(key, None)
            self._aliases = {
                key: value for key, value in self._aliases.items() if key[0] != alias
            }

    @staticmethod
    def _source_key(source):
        return Path(source.metadata_path).resolve(), Path(source.weights_path).resolve()

    @staticmethod
    def _file_stamp(paths):
        stamp = []
        for path in paths:
            try:
                stat = Path(path).stat()
                stamp.append((stat.st_mtime_ns, stat.st_size))
            except OSError:
                stamp.append(None)
        return tuple(stamp)

    def _load(self, alias, module):
        if not module.info().capabilities.neural_inference:
            raise BotIncompatible(None)
        key = alias, module.game_id
        with self._lock:
            if key not in self._aliases:
                source = self._sources[alias]
                source_key = (*self._source_key(source), module.game_id)
                files = self._failed_files.get(source_key)
                if (time.monotonic() - self._failed_sources.get(source_key, -1e9) < 1
                        and (files is None or files == self._file_stamp(source_key[:2]))):
                    raise BotUnavailable(None)
                loaded = self._loaded_sources.get(source_key)
                if loaded is None:
                    try:
                        loaded = (load_game_artifact(source.metadata_path, source.weights_path, module)
                                  if callable(getattr(module, "inference_profile", None)) else
                                  load_artifact(source.metadata_path, source.weights_path, module.encoder()))
                    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as error:
                        self._failed_sources[source_key] = time.monotonic()
                        self._failed_files[source_key] = (None if isinstance(error, OSError)
                                                          else self._file_stamp(source_key[:2]))
                        raise BotUnavailable(None) from None
                reference = model_reference(loaded, sampling_version=source.sampling_version)
                if (
                    len(self._policies) >= MAX_SOURCES
                    and reference not in self._policies
                ):
                    raise ValueError("Too many retained model versions")
                self._loaded_sources[source_key] = loaded
                self._failed_sources.pop(source_key, None)
                self._failed_files.pop(source_key, None)
                self._policies.setdefault(reference, loaded.policy)
                self._aliases[key] = BotDescriptor(
                    alias, loaded.metadata["display_name"], "model", reference,
                    loaded.metadata.get("description"),
                )
            return self._aliases[key]

    def catalog_revision(self, game_id):
        """Changes whenever ``list_bots`` may list differently: a registered source, a changed file of a source whose
        content failed to load, and every second while a source has a transient read error (it is retried)."""
        with self._lock:
            failed = [(key, self._failed_files.get(key)) for key in self._failed_sources if key[2] == game_id]
            now = time.monotonic()
            transient = any(files is None for _, files in failed)
            # Files of failed sources are looked at again at most once per second; otherwise only memory is read.
            keys = frozenset(key for key, files in failed if files is not None)
            stamped = self._failed_stamps.get(game_id)
            if stamped is None or stamped[:2] != (self._revision, keys) or now - stamped[2] >= 1:
                stamps = tuple(sorted((str(key), self._file_stamp(key[:2])) for key in keys))
                self._failed_stamps[game_id] = stamped = self._revision, keys, now, stamps
            return self._revision, int(now) if transient else 0, stamped[3]

    def list_bots(self, game_id):
        module = get_game(game_id)
        result = [
            BotDescriptor(
                info.kind,
                info.display_name,
                "builtin",
                builtin_reference(module, info.kind, info.behavior_version),
            )
            for info in module.bot_info()
        ]
        if not module.info().capabilities.neural_inference:
            return tuple(result)
        with self._lock:
            for alias in self._sources:
                try:
                    result.append(self._load(alias, module))
                except BotUnavailable:
                    continue
        return tuple(result)

    def pin(self, game_id, selected):
        from boardcraft.games.rentcraft.inference.hybrid import is_hybrid, parse_hybrid, hybrid_reference
        if is_hybrid(selected):
            if game_id != "rentcraft":
                raise ValueError("Combined players require Rentcraft")
            module = get_game(game_id)
            owners = parse_hybrid(selected)
            pins = {value: self._pin_component(module, value) for value in dict.fromkeys(owners.values())}
            return hybrid_reference(module, {area: pins[value] for area, value in owners.items()})
        choice_id(selected)
        for descriptor in self.list_bots(game_id):
            if descriptor.choice_id == selected:
                return descriptor.reference
        raise ValueError("Unknown or unavailable bot choice")

    def _pin_component(self, module, selected):
        # Configured rule IDs use kind:option=<JSON>,option=<JSON>.
        if "=" not in selected:
            return self.pin(module.game_id, selected)
        kind, separator, text = selected.partition(":")
        if not separator or kind not in module.info().bot_kinds:
            raise ValueError("Unknown configured rule player")
        options = {}
        decoder = json.JSONDecoder(object_pairs_hook=_unique)
        while text:
            key, separator, value = text.partition("=")
            key = key.strip()
            if not separator or not key or key in options:
                raise ValueError("Invalid configured rule parameter")
            parsed, end = decoder.raw_decode(value.lstrip())
            options[key] = parsed
            text = value.lstrip()[end:].strip()
            if text:
                if not text.startswith(",") or not text[1:].strip():
                    raise ValueError("Invalid configured rule parameters")
                text = text[1:].strip()
        return self.pin_builtin(module.game_id, kind, options)

    def pin_builtin(self, game_id: str, choice_id: str, options: dict) -> BotReference:
        if not isinstance(options, dict):
            raise ValueError("Builtin options must be an object")
        module = get_game(game_id)
        for item in module.bot_info():
            if item.kind == choice_id:
                return builtin_reference(module, item.kind, item.behavior_version, options)
        raise ValueError("Unknown builtin bot choice")

    def resolve(self, reference, seed):
        try:
            require_greedy_reference(reference)
            module = get_game(reference.compatibility.game_id)
            validate_reference(reference, module)
        except (KeyError, TypeError, AttributeError, ValueError):
            raise BotIncompatible(reference) from None
        if isinstance(reference, RuleBotReference) and reference.builtin_kind == "hybrid":
            from boardcraft.games.rentcraft.inference.hybrid import HybridPlayer, component_references
            refs = component_references(reference)
            players = {child.identity: self.resolve(child, seed) for child in dict.fromkeys(refs.values())}
            return HybridPlayer({area: players[child.identity] for area, child in refs.items()},
                                binding=reference.identity,
                                component_ids={area: child.identity for area, child in refs.items()})
        if reference.kind == "builtin":
            options = json.loads(reference.options_json) if isinstance(reference, RuleBotReference) else {}
            if not any(
                reference == builtin_reference(module, item.kind, item.behavior_version, options)
                for item in module.bot_info()
                if item.kind == reference.builtin_kind
            ):
                raise BotUnavailable(reference)
            if options:
                return module.bot(reference.builtin_kind, seed, options=options)
            return module.bot(reference.builtin_kind, seed)
        with self._lock:
            policy_keys = [reference]
            if reference.compatibility.game_id != "rentcraft":
                policy_keys.append(with_model_selection(reference, GREEDY_SAMPLING_VERSION))
            if not any(key in self._policies for key in policy_keys):
                self.list_bots(module.game_id)
            policy = next((self._policies[key] for key in policy_keys if key in self._policies), None)
        if policy is None:
            raise BotUnavailable(reference)
        factory = getattr(module, "inference_bot", None)
        if callable(factory):
            profile = module.inference_profile(reference.compatibility.encoder_id,
                                               reference.compatibility.encoder_version)
            return factory(profile, policy, seed)
        return InferenceBot(module.encoder(), policy, seed, describe=getattr(module, "describe_action", None))


def _manifest_sources(manifest):
    try:
        with (
            manifest.open("rb")
            if hasattr(manifest, "open")
            else Path(manifest).open("rb") as stream
        ):
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("Source manifest is too large")
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
        _object(data, ("version", "models"))
        if (
            type(data["version"]) is not int
            or data["version"] != 1
            or not isinstance(data["models"], list)
            or len(data["models"]) > MAX_SOURCES
        ):
            raise ValueError("Invalid source manifest")
        sources = {}
        root = Path(str(manifest)).resolve().parent
        for item in data["models"]:
            if not isinstance(item, dict):
                raise ValueError("Invalid model source")
            _object({k:v for k,v in item.items() if k != "sampling_version"}, ("choice_id", "metadata", "weights"))
            if "sampling_version" in item and item["sampling_version"] not in (GREEDY_SAMPLING_VERSION, RENT_SELECTION):
                raise ValueError("Unknown policy selection mode")
            alias = choice_id(item["choice_id"])
            if alias in sources or any(
                not isinstance(item[k], str) or not item[k]
                for k in ("metadata", "weights")
            ):
                raise ValueError("Invalid or duplicate model source")
            sources[alias] = NumpySource(
                root / item["metadata"], root / item["weights"], item.get("sampling_version")
            )
        return sources
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        raise ValueError("Cannot read a valid model source manifest") from None


def default_catalog(manifest=None):
    """Discover bundled policies automatically and add explicit user sources."""
    sources = {}
    packaged = files("boardcraft.models").joinpath("models.json")
    manifests = [packaged] if packaged.is_file() else []
    # Retain the original optional package manifest for existing distributions.
    legacy = files("boardcraft.inference").joinpath("models.json")
    if legacy.is_file():
        manifests.append(legacy)
    if manifest is not None:
        manifests.append(manifest)
    for path in manifests:
        supplied = _manifest_sources(path)
        if sources.keys() & supplied.keys():
            raise ValueError("Duplicate bundled or user model choice ID")
        sources.update(supplied)
    return RuntimeBotCatalog(sources)

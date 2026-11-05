"""A host catalog that reports a revision is listed once per revision, not on every read."""

from boardcraft.inference.catalog import RuntimeBotCatalog
from boardcraft.server.service import PlayService


class CountingCatalog:
    def __init__(self, revision=0):
        self.base, self.revision, self.calls = RuntimeBotCatalog(), revision, 0

    def list_bots(self, game_id):
        self.calls += 1
        return self.base.list_bots(game_id)

    def catalog_revision(self, game_id):
        return self.revision

    def __getattr__(self, name):
        return getattr(self.base, name)


def test_unchanged_revision_reuses_the_validated_list(tmp_path):
    catalog = CountingCatalog()
    service = PlayService(tmp_path, bot_catalog=catalog)
    first = service.list_bots("dice_circuit")
    assert catalog.calls == 1
    first[0]["display_name"] = "changed by a caller"
    second = service.list_bots("dice_circuit")
    assert catalog.calls == 1
    assert second[0]["display_name"] != "changed by a caller"
    assert second == service.list_bots("dice_circuit")
    catalog.revision = 1
    service.list_bots("dice_circuit")
    assert catalog.calls == 2
    service.list_bots("rentcraft")
    assert catalog.calls == 3


def test_catalog_without_revision_is_listed_on_every_read(tmp_path):
    catalog = CountingCatalog(revision=None)
    service = PlayService(tmp_path, bot_catalog=catalog)
    service.list_bots("dice_circuit")
    service.list_bots("dice_circuit")
    assert catalog.calls == 2


def test_failing_revision_never_serves_a_cached_list(tmp_path):
    catalog = CountingCatalog()
    service = PlayService(tmp_path, bot_catalog=catalog)
    service.list_bots("dice_circuit")

    def broken(game_id):
        raise RuntimeError("unavailable")

    catalog.catalog_revision = broken
    service.list_bots("dice_circuit")
    assert catalog.calls == 2


def test_runtime_catalog_revision_follows_sources_and_retries(tmp_path, monkeypatch):
    import boardcraft.inference.catalog as catalog_module
    from boardcraft.inference.catalog import NumpySource

    catalog = RuntimeBotCatalog()
    before = catalog.catalog_revision("dice_circuit")
    assert before == catalog.catalog_revision("dice_circuit")
    catalog.register("fixture", NumpySource(tmp_path / "missing.json", tmp_path / "missing.npz"))
    registered = catalog.catalog_revision("dice_circuit")
    assert registered != before
    now = [100.0]
    monkeypatch.setattr(catalog_module.time, "monotonic", lambda: now[0])
    catalog.list_bots("dice_circuit")  # the missing source fails and is retried after a second
    failing = catalog.catalog_revision("dice_circuit")
    assert failing == catalog.catalog_revision("dice_circuit")
    now[0] += 1.5
    assert catalog.catalog_revision("dice_circuit") != failing

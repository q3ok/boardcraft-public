from fastapi import FastAPI
from fastapi.testclient import TestClient

from boardcraft.server.static_files import PageStaticFiles


def test_html_pages_are_revalidated_and_assets_are_not_marked(tmp_path):
    (tmp_path / "index.html").write_text("<p>new build</p>", encoding="utf-8")
    (tmp_path / "app-abc123.js").write_text("1", encoding="utf-8")
    app = FastAPI()
    app.mount("/", PageStaticFiles(directory=tmp_path, html=True))
    client = TestClient(app)
    assert client.get("/").headers["cache-control"] == "no-cache"
    assert client.get("/index.html").headers["cache-control"] == "no-cache"
    assert "cache-control" not in client.get("/app-abc123.js").headers

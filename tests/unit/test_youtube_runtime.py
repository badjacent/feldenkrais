from kb.ingest import youtube


def test_youtube_dl_runtime_options_uses_installed_node(monkeypatch):
    monkeypatch.setattr(youtube.shutil, "which", lambda name: "/opt/node" if name == "node" else None)

    assert youtube._youtube_dl_runtime_options() == {
        "js_runtimes": {"node": {"path": "/opt/node"}}
    }


def test_youtube_dl_runtime_options_is_empty_without_node(monkeypatch):
    monkeypatch.setattr(youtube.shutil, "which", lambda _name: None)

    assert youtube._youtube_dl_runtime_options() == {}

from kb.ingest import youtube


def test_youtube_dl_runtime_options_uses_installed_node(monkeypatch):
    monkeypatch.setattr(youtube.shutil, "which", lambda name: "/opt/node" if name == "node" else None)

    assert youtube._youtube_dl_runtime_options() == {
        "js_runtimes": {"node": {"path": "/opt/node"}}
    }


def test_youtube_dl_runtime_options_is_empty_without_node(monkeypatch):
    monkeypatch.setattr(youtube.shutil, "which", lambda _name: None)

    assert youtube._youtube_dl_runtime_options() == {}


def test_youtube_dl_runtime_options_prefers_deno(monkeypatch):
    paths = {"deno": "/opt/deno", "node": "/opt/node"}
    monkeypatch.setattr(youtube.shutil, "which", paths.get)

    assert youtube._youtube_dl_runtime_options() == {
        "js_runtimes": {"deno": {"path": "/opt/deno"}}
    }


def test_youtube_dl_session_options(monkeypatch):
    monkeypatch.setenv("YOUTUBE_COOKIES_PATH", "/tmp/example/cookies.txt")
    monkeypatch.setenv("YOUTUBE_PLAYER_CLIENT", "web_safari")

    assert youtube._youtube_dl_session_options() == {
        "cookiefile": "/tmp/example/cookies.txt",
        "extractor_args": {"youtube": {"player_client": ["web_safari"]}},
    }

from micropub_server import cli


def test_serve_does_not_advertise_the_web_server(monkeypatch, state):
    """uvicorn adds `Server: uvicorn` unless told not to; there is nothing to gain from saying it."""
    calls = []
    monkeypatch.setattr(cli, "build_state", lambda settings: state)
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: calls.append(kwargs))

    assert cli._serve(None) == 0

    assert calls[0]["server_header"] is False

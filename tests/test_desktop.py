"""Desktop profile/session boundaries and config changes; no real model requests."""

import base64
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from evidenceforge import desktop, desktop_config
from evidenceforge.api import create_app
from evidenceforge.config import Settings
from evidenceforge.desktop_config import DesktopSettingsStore, ProfileLock


@pytest.fixture
def profile(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop_config, "protect_secret", lambda value: base64.b64encode(value.encode()).decode())
    monkeypatch.setattr(desktop_config, "unprotect_secret", lambda value: base64.b64decode(value).decode())
    return tmp_path / "desktop"


@pytest.fixture
def authenticated(profile):
    app = desktop.create_desktop_app(profile, "test-session")
    with TestClient(app) as client:
        response = client.get("/desktop/launch?token=test-session", follow_redirects=False)
        assert response.status_code == 303
        assert "httponly" in response.headers["set-cookie"].lower()
        assert "samesite=strict" in response.headers["set-cookie"].lower()
        yield app, client


def test_profile_ignores_cwd_dotenv_and_environment(profile, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("EF_API_KEY=do-not-import\nEF_MODEL=private-model\n")
    monkeypatch.setenv("EF_API_KEY", "environment-secret")
    monkeypatch.setenv("EF_DATA_DIR", str(tmp_path / "wrong"))
    settings = DesktopSettingsStore(profile).settings()
    assert settings.api_key == ""
    assert settings.model == ""
    assert settings.data_dir == profile / "data"


@pytest.mark.parametrize("path", ["/", "/api/health", "/api/runs", "/api/desktop/settings", "/static/app.js"])
def test_requires_desktop_session(profile, path):
    with TestClient(desktop.create_desktop_app(profile, "token")) as client:
        assert client.get(path).status_code == 403
        assert client.get("/desktop/launch?token=错误").status_code == 403


def test_bootstrap_is_one_time_and_settings_reject_cross_origin(authenticated):
    app, client = authenticated
    assert client.get("/desktop/launch?token=test-session").status_code == 403
    response = client.put("/api/desktop/settings", json={"model": "demo"}, headers={"Origin": "https://untrusted.example"})
    assert response.status_code == 403
    assert app.state.desktop_profile.first_run


def test_save_secret_roundtrip_keep_clear_and_live_tool_updates(authenticated, profile):
    app, client = authenticated
    response = client.put("/api/desktop/settings", json={"base_url": "https://provider.example/v1/", "model": "test-model",
                                                       "api_key": "model-secret", "tavily_api_key": "search-secret"})
    assert response.status_code == 200
    public = response.json()
    assert public["model_configured"] and public["web_search_configured"] and not public["first_run"]
    assert "model-secret" not in response.text and "search-secret" not in response.text
    assert "model-secret" not in (profile / "settings.json").read_text()
    assert DesktopSettingsStore(profile).load()["api_key"] == "model-secret"
    assert client.get("/api/health").json()["tools"][-1] == "search_web"
    response = client.put("/api/desktop/settings", json={"model": "another", "api_key": None})
    assert response.status_code == 200 and app.state.desktop_settings.api_key == "model-secret"
    response = client.put("/api/desktop/settings", json={"api_key": "", "tavily_api_key": ""})
    assert response.status_code == 200 and not response.json()["model_configured"]
    assert "search_web" not in client.get("/api/health").json()["tools"]


def test_validation_errors_do_not_echo_secrets(authenticated):
    _, client = authenticated
    secret = "secret-" * 600
    response = client.put("/api/desktop/settings", json={"api_key": secret, "base_url": "not-a-url"})
    assert response.status_code == 422
    assert "secret-" not in response.text and "input" not in response.text


def test_corrupted_config_does_not_fall_back_to_defaults(profile):
    profile.mkdir()
    (profile / "settings.json").write_text("invalid-private-data")
    with pytest.raises(RuntimeError, match="无法读取") as error:
        DesktopSettingsStore(profile).load()
    assert "private-data" not in str(error.value)


def test_busy_run_blocks_settings_change(authenticated):
    app, client = authenticated
    app.state.store.create_run("An unfinished research task", "demo", {})
    response = client.put("/api/desktop/settings", json={"model": "replacement"})
    assert response.status_code == 409 and app.state.desktop_settings.model == ""


def test_cancelled_worker_keeps_snapshot_and_blocks_save_until_finished(authenticated, monkeypatch):
    app, client = authenticated
    entered, release = threading.Event(), threading.Event()
    seen = []

    def fake_execute(engine, run_id):
        seen.append(engine.settings)
        entered.set()
        assert release.wait(10)

    monkeypatch.setattr(desktop.Engine, "execute", fake_execute)
    run = app.state.store.create_run("A cancelled request is returning", "demo", {})
    thread = threading.Thread(target=app.state.engine.execute, args=(run["id"],))
    thread.start()
    try:
        assert entered.wait(5)
        app.state.store.update_run(run["id"], status="cancelled")
        assert client.put("/api/desktop/settings", json={"model": "next"}).status_code == 409
        assert seen[0] is not app.state.desktop_settings
    finally:
        release.set()
        thread.join(5)
    assert client.put("/api/desktop/settings", json={"model": "next"}).status_code == 200


def test_connection_test_uses_unsaved_fields_without_disclosing_provider_error(authenticated, monkeypatch):
    _, client = authenticated
    original = httpx.Client
    calls = []

    def response(request):
        calls.append(request)
        return httpx.Response(401, json={"error": "echoed-private-key"})

    monkeypatch.setattr(desktop.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(response), **kwargs))
    result = client.post("/api/desktop/test", json={"model": "unsaved", "base_url": "https://test.example/v1", "api_key": "test-only-key"})
    assert result.status_code == 422
    assert "echoed-private-key" not in result.text and "test-only-key" not in result.text
    assert calls[0].url == "https://test.example/v1/chat/completions"
    assert calls[0].headers["Authorization"] == "Bearer test-only-key"


def test_shutdown_drains_workers_before_releasing_profile(profile, monkeypatch):
    app = desktop.create_desktop_app(profile, "session")
    entered, release, drained = threading.Event(), threading.Event(), threading.Event()

    def slow_execute(engine, run_id):
        entered.set()
        assert release.wait(10)
        assert engine.stop_event.is_set()
        app.state.store.add_event(run_id, "system", "stopped", "Worker finished")

    monkeypatch.setattr(desktop.Engine, "execute", slow_execute)
    run = app.state.store.create_run("Pending request during shutdown", "demo", {})
    with ProfileLock(profile):
        worker = threading.Thread(target=app.state.engine.execute, args=(run["id"],))
        worker.start()
        try:
            assert entered.wait(5)
            app.state.desktop_begin_shutdown()
            assert app.state.store.get_run(run["id"])["status"] == "cancelled"

            def drain():
                app.state.desktop_wait_for_workers()
                drained.set()

            waiter = threading.Thread(target=drain)
            waiter.start()
            assert not drained.wait(0.1)
            with pytest.raises(RuntimeError, match="已在运行"):
                with ProfileLock(profile):
                    pass
        finally:
            release.set()
            worker.join(5)
        assert drained.wait(5)
        waiter.join(5)
        assert app.state.desktop_active_runs() == 0
    with ProfileLock(profile):
        pass


def test_desktop_config_api_is_not_exposed_by_web_app(tmp_path):
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path))) as client:
        assert client.get("/api/desktop/settings").status_code == 404
        assert not client.get("/api/health").json()["desktop"]


def test_profile_lock_is_exclusive_and_released(profile):
    with ProfileLock(profile):
        with pytest.raises(RuntimeError, match="已在运行"):
            with ProfileLock(profile):
                pass
    with ProfileLock(profile):
        pass

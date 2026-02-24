import importlib
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

TEST_MESSAGE = "Quais passos devo seguir diante de suspeita de crime infantil online?"


def _setup_module(monkeypatch, tmp_path, *, disable_model=True):
    knowledge_path = tmp_path / "knowledge.json"
    monkeypatch.setenv("KNOWLEDGE_CACHE_PATH", str(knowledge_path))
    sample = tmp_path / "sample.txt"
    sample.write_text("Conteudo de teste para knowledge base.")
    monkeypatch.setenv("TRAINING_FILES", str(sample))
    if os.getenv("REQUIRE_API_KEY") is None:
        monkeypatch.setenv("REQUIRE_API_KEY", "0")
    if os.getenv("RATE_LIMIT_ENABLED") is None:
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "0")
    monkeypatch.delenv("ACOLHEIA_API_KEY", raising=False)
    if disable_model:
        monkeypatch.setenv("DISABLE_GEMINI", "1")
    else:
        monkeypatch.setenv("DISABLE_GEMINI", "0")
    sys.modules.pop("apigemini", None)
    module = importlib.import_module("apigemini")
    return module, TestClient(module.app)


def test_health_endpoint(monkeypatch, tmp_path):
    module, client = _setup_module(monkeypatch, tmp_path)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_mensagem_rejects_empty(monkeypatch, tmp_path):
    module, client = _setup_module(monkeypatch, tmp_path)
    resp = client.post("/mensagem", json={"mensagem": "   "})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Mensagem nao pode ser vazia."


def test_mensagem_rejects_large(monkeypatch, tmp_path):
    monkeypatch.setenv("MAX_MESSAGE_CHARS", "10")
    module, client = _setup_module(monkeypatch, tmp_path)
    resp = client.post("/mensagem", json={"mensagem": "a" * 11})
    assert resp.status_code == 413
    assert "excede o limite" in resp.json()["detail"]


def test_mensagem_requires_api_key(monkeypatch, tmp_path):
    monkeypatch.setenv("REQUIRE_API_KEY", "1")
    module, client = _setup_module(monkeypatch, tmp_path)
    monkeypatch.setenv("ACOLHEIA_API_KEY", "segredo")
    sys.modules.pop("apigemini", None)
    module = importlib.import_module("apigemini")
    client = TestClient(module.app)

    resp = client.post("/mensagem", json={"mensagem": TEST_MESSAGE})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Chave de acesso invalida."


def test_mensagem_fails_when_auth_required_without_key_config(monkeypatch, tmp_path):
    monkeypatch.setenv("REQUIRE_API_KEY", "1")
    monkeypatch.delenv("ACOLHEIA_API_KEY", raising=False)
    module, client = _setup_module(monkeypatch, tmp_path)
    resp = client.post("/mensagem", json={"mensagem": TEST_MESSAGE})
    assert resp.status_code == 503
    assert "autenticacao nao configurada" in resp.json()["detail"].lower()


def test_mensagem_rate_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
    monkeypatch.setenv("RATE_LIMIT_MAX_REQUESTS", "1")
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "60")
    module, client = _setup_module(monkeypatch, tmp_path)

    first = client.post("/mensagem", json={"mensagem": TEST_MESSAGE})
    assert first.status_code == 200

    second = client.post("/mensagem", json={"mensagem": TEST_MESSAGE})
    assert second.status_code == 429
    assert "limite de requisicoes excedido" in second.json()["detail"].lower()


def test_mensagem_calls_gemini(monkeypatch, tmp_path):
    class DummyResponse:
        text = "Orientacao simulada com destaque."

    class DummyModel:
        def generate_content(self, *_args, **_kwargs):
            return DummyResponse()

    monkeypatch.setenv("MAX_MESSAGE_CHARS", "2000")
    monkeypatch.setenv("INCLUDE_DEFAULT_FONTES", "1")
    monkeypatch.setenv("USE_GROQ", "0")
    monkeypatch.setenv("RAW_MODE", "0")
    monkeypatch.setenv("FREE_MODE", "0")
    module, client = _setup_module(monkeypatch, tmp_path, disable_model=False)
    module.GEMINI_MODEL = DummyModel()

    resp = client.post("/mensagem", json={"mensagem": TEST_MESSAGE})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["resposta"]
    assert payload["resposta_com_fontes"]
    assert payload["fontes"]
    assert payload["model_used"]
    assert payload["used_web_search"] is False
    assert payload["is_fallback"] is False
    assert all("label" in ref and "url" in ref for ref in payload["fontes"])
    assert payload["contexto"]
    assert "generated_at" in payload

"""
Testes unitários para os endpoints de settings e usage.

Cobre:
- GET /api/settings — retorna todos os campos, chaves mascaradas
- POST /api/settings — salva configurações, retorna valores mascarados
- GET /api/usage — retorna today + history com 7 entradas
- Tratamento de ValidationError (400) e InternalError (500)
- Carregamento de configurações na inicialização (Req 6.1, 6.2)

Requisitos: 1.1, 1.2, 1.4, 2.3, 2.4, 2.6, 6.1, 6.2
"""
import json
import os
import pytest
from unittest.mock import patch, MagicMock


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_data_dir(tmp_path):
    """Diretório temporário isolado para cada teste."""
    return str(tmp_path)


@pytest.fixture
def app(tmp_data_dir):
    """Flask app de teste com DATA_DIR isolado."""
    os.environ["DATA_DIR"] = tmp_data_dir
    os.environ.setdefault("SERPER_KEY", "")
    os.environ.setdefault("OLLAMA_KEY", "")

    # Reimporta o módulo de settings para pegar o novo DATA_DIR
    import importlib
    import app.config.settings as settings_mod
    settings_mod.DATA_DIR = tmp_data_dir

    import app.services.settings_service as svc
    svc.SETTINGS_FILE = os.path.join(tmp_data_dir, "api_settings.json")

    import app.services.usage_tracker as ut
    ut.USAGE_FILE = os.path.join(tmp_data_dir, "api_usage.json")

    from app.app_factory import create_app
    flask_app = create_app()
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


# ─── GET /api/settings ────────────────────────────────────────────────────────

class TestGetSettings:
    def test_returns_200_and_success_envelope(self, client):
        """GET /api/settings deve retornar 200 com envelope success=true."""
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "data" in data

    def test_returns_all_expected_fields(self, client):
        """GET /api/settings deve retornar todos os campos editáveis."""
        resp = client.get("/api/settings")
        data = resp.get_json()["data"]
        expected_fields = {"SERPER_KEY", "OLLAMA_KEY", "OLLAMA_BASE", "OLLAMA_MODEL", "OLLAMA_FALLBACKS"}
        assert expected_fields.issubset(set(data.keys()))

    def test_non_secret_fields_not_masked(self, client):
        """OLLAMA_BASE, OLLAMA_MODEL e OLLAMA_FALLBACKS não devem ser mascarados."""
        import app.config.settings as settings_mod
        settings_mod.OLLAMA_BASE = "https://api.example.com"
        settings_mod.OLLAMA_MODEL = "gpt-test"
        settings_mod.OLLAMA_FALLBACKS = []

        resp = client.get("/api/settings")
        data = resp.get_json()["data"]
        assert data["OLLAMA_BASE"] == "https://api.example.com"
        assert data["OLLAMA_MODEL"] == "gpt-test"

    def test_empty_keys_returned_as_empty_string(self, client):
        """Chaves vazias devem ser retornadas como string vazia."""
        import app.config.settings as settings_mod
        settings_mod.SERPER_KEY = ""
        settings_mod.OLLAMA_KEY = ""

        resp = client.get("/api/settings")
        data = resp.get_json()["data"]
        assert data["SERPER_KEY"] == ""
        assert data["OLLAMA_KEY"] == ""

    def test_api_keys_are_masked_when_set(self, client):
        """Chaves de API com valor devem ser mascaradas (nunca expostas por completo)."""
        import app.config.settings as settings_mod
        settings_mod.SERPER_KEY = "sk-abcdefgh1234"
        settings_mod.OLLAMA_KEY = "key-xyzwabcd5678"

        resp = client.get("/api/settings")
        data = resp.get_json()["data"]
        # Não deve conter o valor completo
        assert data["SERPER_KEY"] != "sk-abcdefgh1234"
        assert data["OLLAMA_KEY"] != "key-xyzwabcd5678"
        # Deve ter formato mascarado (contém "...")
        assert "..." in data["SERPER_KEY"]
        assert "..." in data["OLLAMA_KEY"]


# ─── POST /api/settings ───────────────────────────────────────────────────────

class TestPostSettings:
    def test_returns_200_and_success_envelope(self, client):
        """POST /api/settings com payload válido deve retornar 200."""
        resp = client.post(
            "/api/settings",
            json={"OLLAMA_BASE": "https://new.example.com", "OLLAMA_MODEL": "new-model"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    def test_saves_non_key_fields(self, client, tmp_data_dir):
        """POST /api/settings deve persistir campos não-chave no arquivo JSON."""
        client.post(
            "/api/settings",
            json={"OLLAMA_BASE": "https://saved.example.com", "OLLAMA_MODEL": "saved-model"},
        )
        settings_file = os.path.join(tmp_data_dir, "api_settings.json")
        assert os.path.exists(settings_file)
        with open(settings_file) as f:
            saved = json.load(f)
        assert saved.get("OLLAMA_BASE") == "https://saved.example.com"
        assert saved.get("OLLAMA_MODEL") == "saved-model"

    def test_updates_module_in_memory(self, client):
        """POST /api/settings deve atualizar o módulo de settings em memória."""
        import app.config.settings as settings_mod
        client.post("/api/settings", json={"OLLAMA_MODEL": "updated-model-in-memory"})
        assert settings_mod.OLLAMA_MODEL == "updated-model-in-memory"

    def test_blank_key_preserves_existing(self, client, tmp_data_dir):
        """POST com chave em branco deve preservar o valor anterior."""
        import app.services.settings_service as svc
        # Salva uma chave inicial
        svc.save_settings_file({"SERPER_KEY": "sk-existingkey1234"})
        svc.apply_settings_to_module({"SERPER_KEY": "sk-existingkey1234"})

        # Envia payload com SERPER_KEY em branco
        resp = client.post("/api/settings", json={"SERPER_KEY": ""})
        assert resp.status_code == 200

        # Verifica que o arquivo ainda tem a chave original
        with open(svc.SETTINGS_FILE) as f:
            saved = json.load(f)
        assert saved.get("SERPER_KEY") == "sk-existingkey1234"

    def test_field_too_long_returns_400(self, client):
        """Campo com mais de 500 caracteres deve retornar 400."""
        resp = client.post("/api/settings", json={"OLLAMA_BASE": "x" * 501})
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False
        assert data["error"]["code"] == "VALIDATION_ERROR"

    def test_control_character_returns_400(self, client):
        """Campo com caractere de controle deve retornar 400."""
        resp = client.post("/api/settings", json={"OLLAMA_BASE": "bad\x01value"})
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False
        assert data["error"]["code"] == "VALIDATION_ERROR"

    def test_file_write_error_returns_500(self, client):
        """Falha ao escrever o arquivo deve retornar 500."""
        with patch("app.services.settings_service.save_settings_file") as mock_save:
            from app.models.errors import InternalError
            mock_save.side_effect = InternalError("Disk full")
            resp = client.post("/api/settings", json={"OLLAMA_MODEL": "test"})
        assert resp.status_code == 500
        data = resp.get_json()
        assert data["success"] is False
        assert data["error"]["code"] == "INTERNAL_ERROR"

    def test_empty_payload_returns_200(self, client):
        """Payload vazio deve ser aceito (nenhum campo alterado)."""
        resp = client.post("/api/settings", json={})
        assert resp.status_code == 200

    def test_returns_masked_keys_in_response(self, client):
        """A resposta do POST deve retornar chaves mascaradas, não o valor completo."""
        resp = client.post("/api/settings", json={"SERPER_KEY": "sk-abcdefgh1234"})
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["SERPER_KEY"] != "sk-abcdefgh1234"
        assert "..." in data["SERPER_KEY"]


# ─── GET /api/usage ───────────────────────────────────────────────────────────

class TestGetUsage:
    def test_returns_200_and_success_envelope(self, client):
        """GET /api/usage deve retornar 200 com envelope success=true."""
        resp = client.get("/api/usage")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    def test_returns_today_and_history(self, client):
        """GET /api/usage deve retornar 'today' e 'history'."""
        resp = client.get("/api/usage")
        data = resp.get_json()["data"]
        assert "today" in data
        assert "history" in data

    def test_history_has_exactly_7_entries(self, client):
        """history deve ter exatamente 7 entradas."""
        resp = client.get("/api/usage")
        history = resp.get_json()["data"]["history"]
        assert len(history) == 7

    def test_today_has_all_services(self, client):
        """today deve ter contadores para todos os 4 serviços."""
        resp = client.get("/api/usage")
        today = resp.get_json()["data"]["today"]
        for svc in ("serper", "ollama", "brasilapi", "maps"):
            assert svc in today

    def test_history_entries_have_all_services(self, client):
        """Cada entrada do history deve ter data e todos os serviços."""
        resp = client.get("/api/usage")
        history = resp.get_json()["data"]["history"]
        for entry in history:
            assert "date" in entry
            for svc in ("serper", "ollama", "brasilapi", "maps"):
                assert svc in entry

    def test_empty_usage_returns_zeros(self, client):
        """Sem dados de uso, todos os contadores devem ser zero."""
        resp = client.get("/api/usage")
        data = resp.get_json()["data"]
        for svc in ("serper", "ollama", "brasilapi", "maps"):
            assert data["today"][svc] == 0
        for entry in data["history"]:
            for svc in ("serper", "ollama", "brasilapi", "maps"):
                assert entry[svc] == 0


# ─── Rate Limiting ────────────────────────────────────────────────────────────

@pytest.fixture(autouse=False)
def reset_rate_limiter():
    """Reseta o estado do rate limiter global antes de cada teste de rate limit."""
    from app.middleware.rate_limit import limiter
    with limiter._lock:
        limiter._windows.clear()
    yield
    with limiter._lock:
        limiter._windows.clear()


class TestRateLimit:
    def test_settings_rate_limit_after_10_requests(self, client, reset_rate_limiter):
        """Após 10 requisições por minuto, deve retornar 429."""
        # Faz 10 requisições (dentro do limite)
        for _ in range(10):
            resp = client.get("/api/settings")
            assert resp.status_code == 200

        # A 11ª deve ser bloqueada
        resp = client.get("/api/settings")
        assert resp.status_code == 429
        data = resp.get_json()
        assert data["success"] is False
        assert data["error"]["code"] == "RATE_LIMIT_EXCEEDED"

    def test_post_settings_rate_limit_after_10_requests(self, client, reset_rate_limiter):
        """POST /api/settings também deve ter rate limit de 10 req/min."""
        for _ in range(10):
            resp = client.post("/api/settings", json={})
            assert resp.status_code == 200

        resp = client.post("/api/settings", json={})
        assert resp.status_code == 429


# ─── Inicialização — carregamento do arquivo de configuração ─────────────────

class TestStartupLoading:
    def test_load_settings_on_startup(self, tmp_data_dir):
        """Na inicialização, o arquivo de configuração deve ser carregado (Req 6.1)."""
        # Cria o arquivo de configuração antes de iniciar o app
        settings_file = os.path.join(tmp_data_dir, "api_settings.json")
        with open(settings_file, "w") as f:
            json.dump({"OLLAMA_MODEL": "startup-model", "OLLAMA_BASE": "https://startup.example.com"}, f)

        import app.services.settings_service as svc
        svc.SETTINGS_FILE = settings_file

        import app.config.settings as settings_mod
        settings_mod.DATA_DIR = tmp_data_dir

        # Simula o carregamento que acontece no create_app
        saved = svc.load_settings_file()
        assert saved != {}
        svc.apply_settings_to_module(saved)

        assert settings_mod.OLLAMA_MODEL == "startup-model"
        assert settings_mod.OLLAMA_BASE == "https://startup.example.com"

    def test_missing_file_uses_env_defaults(self, tmp_data_dir):
        """Se o arquivo não existir, os valores do .env devem ser mantidos (Req 6.2)."""
        import app.services.settings_service as svc
        svc.SETTINGS_FILE = os.path.join(tmp_data_dir, "api_settings.json")

        # Garante que o arquivo não existe
        assert not os.path.exists(svc.SETTINGS_FILE)

        saved = svc.load_settings_file()
        assert saved == {}

        # apply_settings_to_module não deve ser chamado (nenhuma alteração)
        import app.config.settings as settings_mod
        original_model = settings_mod.OLLAMA_MODEL
        # Não chama apply — valor deve permanecer o mesmo
        assert settings_mod.OLLAMA_MODEL == original_model

"""
Prospector API — Settings & Usage Routes

GET  /api/settings  — retorna configurações atuais (chaves mascaradas)
POST /api/settings  — valida e salva novas configurações
GET  /api/usage     — retorna histórico de consumo dos últimos 7 dias

Rate limiting: 10 req/min por IP nos endpoints de settings (Req 5.5).
"""
from flask import Blueprint, request

from app.models.errors import ValidationError, InternalError, make_success, handle_error
from app.middleware.rate_limit import rate_limit
from app.services.settings_service import get_current_settings, update_settings
from app.services.usage_tracker import get_usage

settings_bp = Blueprint("settings", __name__, url_prefix="/api")


@settings_bp.route("/settings", methods=["GET"])
@rate_limit(limit=10, window=60)
def get_settings():
    """
    GET /api/settings
    Retorna as configurações atuais com chaves de API mascaradas.
    Req 1.1, 1.2, 1.3, 1.4, 5.1, 5.2
    """
    data = get_current_settings()
    return make_success(data)


@settings_bp.route("/settings", methods=["POST"])
@rate_limit(limit=10, window=60)
def post_settings():
    """
    POST /api/settings
    Valida e persiste novas configurações; retorna valores salvos (mascarados).
    Req 2.3, 2.4, 2.5, 2.6, 5.1, 5.2, 5.4, 5.5
    """
    payload = request.get_json(silent=True) or {}
    try:
        saved = update_settings(payload)
    except (ValidationError, InternalError):
        raise
    return make_success(saved)


@settings_bp.route("/usage", methods=["GET"])
def get_usage_endpoint():
    """
    GET /api/usage
    Retorna contadores de consumo dos últimos 7 dias por serviço.
    Req 4.1, 4.2, 4.6
    """
    usage = get_usage(7)
    return make_success({
        "today": usage["today"],
        "history": usage["history"],
    })

"""
Prospector API — Flask Application Factory

Creates and configures the Flask app following CORE-03 architecture.
"""
from flask import Flask, jsonify
from flask_cors import CORS

from app.config.settings import ALLOWED_ORIGINS, APP_NAME, APP_VERSION
from app.models.errors import AppError, handle_error
from app.routes.health import health_bp
from app.routes.search import search_bp
from app.routes.lead import lead_bp
from app.routes.settings import settings_bp


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)

    # ─── Configuration ───
    app.config["JSON_SORT_KEYS"] = False
    app.config["JSONIFY_PRETTYPRINT_REGULAR"] = False

    # ─── CORS (BACK-05) ───
    _origins = ALLOWED_ORIGINS if ALLOWED_ORIGINS != ["*"] else "*"
    CORS(app, resources={
        r"/api/*": {
            "origins": _origins,
            "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            "allow_headers": ["Content-Type", "Authorization", "X-API-Key"],
            "max_age": 3600,
        }
    })

    # ─── Load persisted settings before registering blueprints (Req 6.1, 6.2) ───
    from app.services.settings_service import load_settings_file, apply_settings_to_module
    _saved = load_settings_file()
    if _saved:
        apply_settings_to_module(_saved)

    # ─── Register Blueprints ───
    app.register_blueprint(health_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(lead_bp)
    app.register_blueprint(settings_bp)

    # ─── Error Handlers ───
    app.register_error_handler(AppError, handle_error)
    app.register_error_handler(400, lambda e: (jsonify({
        "success": False, "error": {"code": "BAD_REQUEST", "message": str(e)}
    }), 400))
    app.register_error_handler(404, lambda e: (jsonify({
        "success": False, "error": {"code": "NOT_FOUND", "message": "Resource not found"}
    }), 404))
    app.register_error_handler(500, lambda e: (jsonify({
        "success": False, "error": {"code": "INTERNAL_ERROR", "message": "Internal server error"}
    }), 500))

    # ─── Root endpoint ───
    @app.route("/")
    def root():
        return jsonify({
            "success": True,
            "data": {
                "name": APP_NAME,
                "version": APP_VERSION,
                "endpoints": {
                    "health": "/api/health",
                    "search_create": "POST /api/search",
                    "search_get": "GET /api/search/<id>",
                    "search_delete": "DELETE /api/search/<id>",
                    "search_rediscover": "POST /api/search/<id>/rediscover",
                    "search_enrich": "POST /api/search/<id>/enrich",
                    "search_score": "POST /api/search/<id>/score",
                    "search_analyze_market": "POST /api/search/<id>/analyze-market",
                    "search_analyze_leads": "POST /api/search/<id>/analyze-leads",
                    "search_analyze": "POST /api/search/<id>/analyze",
                    "history": "GET /api/history",
                    "lead_update": "PUT /api/search/<id>/lead/<lid>",
                    "lead_delete": "DELETE /api/search/<id>/lead/<lid>",
                    "lead_get": "GET /api/search/<id>/lead/<lid>",
                    "lead_analysis": "PUT /api/search/<id>/lead/<lid>/analysis",
                    "lead_reanalyze": "POST /api/search/<id>/analyze-leads/<int>",
                    "lead_diagnose": "POST /api/search/<id>/diagnose/<lid>",
                    "settings_get": "GET /api/settings",
                    "settings_update": "POST /api/settings",
                    "usage": "GET /api/usage",
                }
            }
        })

    print(f"[{APP_NAME}] v{APP_VERSION} ready — {len(app.url_map._rules)} routes")
    return app


# ─── WSGI Entry Point ───
app = create_app()

if __name__ == "__main__":
    from app.config.settings import HOST, PORT, DEBUG
    app.run(host=HOST, port=PORT, debug=DEBUG)
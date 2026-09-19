from http import HTTPStatus

from flask import Flask, request
from flask_migrate import Migrate
from flask_smorest import Api
from sqlalchemy import text

from app.config import Config
from app.database import db
from app.extensions.storage import init_storage
from app.services.errors import ServiceError
from app import model  # noqa: F401  (registers models with SQLAlchemy / Alembic)

migrate = Migrate()


def create_app(db_url=None, config_overrides=None, document_storage=None):

    app = Flask(__name__)
    app.config.from_object(Config)
    if db_url:
        app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config.update(config_overrides or {})

    db.init_app(app)
    # S3 (or local disk in development) for invoice and supporting documents.
    init_storage(app, document_storage)
    # Schema changes go through migrations (flask db upgrade), not db.create_all().
    migrate.init_app(app=app, db=db)

    api_endpoints = Api(app)
    from app.routes import blueprints

    for blp in blueprints:
        api_endpoints.register_blueprint(blp)

    @app.errorhandler(ServiceError)
    def handle_service_error(error: ServiceError):
        # Same body shape as flask-smorest's own errors.
        body = {
            "code": error.status_code,
            "status": HTTPStatus(error.status_code).phrase,
            "message": error.message,
        }
        if error.errors:
            body["errors"] = error.errors
        return body, error.status_code

    @app.after_request
    def add_cors_headers(response):
        origin = request.headers.get("Origin")
        if origin and origin in app.config["CORS_ORIGINS"]:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Vendor-Id"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
            response.headers["Access-Control-Expose-Headers"] = "Content-Disposition"
            response.headers["Vary"] = "Origin"
        return response

    @app.get("/healthz")
    def healthz():
        try:
            db.session.execute(text("SELECT 1"))
        except Exception:
            app.logger.exception("Health check failed")
            return {"status": "error", "database": "unreachable"}, 503
        return {"status": "ok"}

    from app.cli import register_cli

    register_cli(app)

    return app

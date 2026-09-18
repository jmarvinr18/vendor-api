import os

from flask import Flask, request
from flask_migrate import Migrate
from flask_smorest import Api

from app.config import Config
from app.database import db
from app import model  # noqa: F401  (registers models with SQLAlchemy / Alembic)

migrate = Migrate()


def create_app(db_url=None):

    app = Flask(__name__)
    app.config.from_object(Config)
    if db_url:
        app.config["SQLALCHEMY_DATABASE_URI"] = db_url

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)
    # Schema changes go through migrations (flask db upgrade), not db.create_all().
    migrate.init_app(app=app, db=db)

    api_endpoints = Api(app)
    from app.routes import blueprints

    for blp in blueprints:
        api_endpoints.register_blueprint(blp)

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

    from app.cli import register_cli

    register_cli(app)

    return app

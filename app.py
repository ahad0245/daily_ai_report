from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

try:
    from .config import get_default_report_date, get_report_config
    from .report_service import ApplicationsReportClient, build_report_payload
except ImportError:
    from config import get_default_report_date, get_report_config
    from report_service import ApplicationsReportClient, build_report_payload


PACKAGE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = PACKAGE_DIR / "frontend"
DIST_DIR = PACKAGE_DIR / "dist"


def create_app() -> Flask:
    load_dotenv(PACKAGE_DIR / ".env")
    validate_report_environment()

    app = Flask(__name__, static_folder=None)
    app.config["JSON_SORT_KEYS"] = False

    @app.get("/api/report/config")
    def get_config() -> Any:
        config = get_report_config()
        default_date = get_default_report_date(config)
        return jsonify(
            {
                "timezone": config.timezone_name,
                "defaultReportDate": default_date.isoformat(),
                "statusConfig": {
                    "selectedByAi": config.selected_status,
                    "rejectedByAi": config.rejected_status,
                },
            }
        )

    @app.get("/api/report/daily-ai-applicant-processing")
    def get_daily_ai_report() -> Any:
        config = get_report_config()
        target_date = parse_requested_date(request.args.get("date"), config)
        client = ApplicationsReportClient()
        try:
            applications = client.fetch_ai_processed_applications_for_date(
                target_date=target_date,
                config=config,
            )
            payload = build_report_payload(
                applications=applications,
                target_date=target_date,
                config=config,
            )
            return jsonify(payload)
        except Exception as exc:  # pragma: no cover - returned to UI intentionally
            status_code = 401 if "401" in str(exc) or "unauthorized" in str(exc).lower() else 500
            return (
                jsonify(
                    {
                        "error": {
                            "message": str(exc),
                            "statusCode": status_code,
                            "reportDate": target_date.isoformat(),
                            "generatedAt": datetime.now(config.timezone).isoformat(),
                        }
                    }
                ),
                status_code,
            )

    @app.get("/")
    def serve_index() -> Any:
        return send_from_directory(get_assets_dir(), "index.html")

    @app.get("/<path:asset_path>")
    def serve_assets(asset_path: str) -> Any:
        assets_dir = get_assets_dir()
        full_path = assets_dir / asset_path
        if full_path.exists() and full_path.is_file():
            return send_from_directory(assets_dir, asset_path)
        return send_from_directory(assets_dir, "index.html")

    return app


def get_assets_dir() -> Path:
    if DIST_DIR.exists():
        return DIST_DIR
    return FRONTEND_DIR


def parse_requested_date(value: str | None, config) -> date:
    if not value:
        return get_default_report_date(config)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Invalid date format. Use YYYY-MM-DD.") from exc


def validate_report_environment() -> None:
    required = [
        "ZOHO_RECRUIT_BASE_URL",
        "ZOHO_RECRUIT_ACCOUNTS_DOMAIN",
        "ZOHO_RECRUIT_ACCESS_TOKEN",
        "ZOHO_RECRUIT_REFRESH_TOKEN",
        "ZOHO_RECRUIT_CLIENT_ID",
        "ZOHO_RECRUIT_CLIENT_SECRET",
    ]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")


if __name__ == "__main__":
    application = create_app()
    application.run(host=os.getenv("REPORT_APP_HOST", "127.0.0.1"), port=int(os.getenv("REPORT_APP_PORT", "8000")), debug=False)

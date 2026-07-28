from __future__ import annotations

import json
import os
from datetime import date, datetime
from html import escape
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request, send_from_directory

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

    @app.after_request
    def add_no_cache_headers(response: Response) -> Response:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

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

    @app.get("/api/report/job-detail")
    def get_job_detail() -> Any:
        config = get_report_config()
        target_date = parse_requested_date(request.args.get("date"), config)
        job_key = (request.args.get("jobKey") or "").strip()
        if not job_key:
            return jsonify({"error": {"message": "jobKey is required.", "statusCode": 400}}), 400

        client = ApplicationsReportClient()
        try:
            payload = client.fetch_job_detail_for_date(
                target_date=target_date,
                config=config,
                job_key=job_key,
            )
            return jsonify(payload)
        except Exception as exc:  # pragma: no cover - returned to UI intentionally
            status_code = 401 if "401" in str(exc) or "unauthorized" in str(exc).lower() else 500
            return jsonify({"error": {"message": str(exc), "statusCode": status_code}}), status_code

    @app.get("/api/report/candidates/<candidate_id>/attachments/<attachment_id>")
    def get_candidate_attachment(candidate_id: str, attachment_id: str) -> Response:
        client = ApplicationsReportClient()
        response = client.get_binary(f"Candidates/{candidate_id}/Attachments/{attachment_id}")
        headers = {
            "Content-Type": response.headers.get("content-type", "application/octet-stream"),
            "Content-Disposition": response.headers.get("content-disposition", "inline"),
        }
        return Response(response.content, status=response.status_code, headers=headers)

    @app.get("/api/report/candidates/<candidate_id>/resume")
    def get_candidate_resume(candidate_id: str) -> Response:
        client = ApplicationsReportClient()
        attachments = client.fetch_candidate_attachments(candidate_id)
        resume_attachment = next(
            (
                attachment
                for attachment in attachments
                if "resume" in str((attachment.get("Category") or {}).get("name") or "").lower()
                or "resume" in str(attachment.get("File_Name") or "").lower()
            ),
            attachments[0] if attachments else None,
        )
        if not resume_attachment or not resume_attachment.get("id"):
            return jsonify({"error": {"message": "Resume not found.", "statusCode": 404}}), 404
        response = client.get_binary(f"Candidates/{candidate_id}/Attachments/{resume_attachment['id']}")
        headers = {
            "Content-Type": response.headers.get("content-type", "application/octet-stream"),
            "Content-Disposition": response.headers.get("content-disposition", "inline"),
        }
        return Response(response.content, status=response.status_code, headers=headers)

    @app.get("/api/report/candidates/<candidate_id>/profile")
    def get_candidate_profile(candidate_id: str) -> Any:
        client = ApplicationsReportClient()
        try:
            return jsonify(client.fetch_candidate_profile(candidate_id))
        except Exception as exc:  # pragma: no cover - returned to UI intentionally
            status_code = 401 if "401" in str(exc) or "unauthorized" in str(exc).lower() else 500
            return jsonify({"error": {"message": str(exc), "statusCode": status_code}}), status_code

    @app.get("/job-detail-page")
    def get_job_detail_page() -> Response:
        config = get_report_config()
        target_date = parse_requested_date(request.args.get("date"), config)
        job_key = (request.args.get("jobKey") or "").strip()
        if not job_key:
            return Response("Missing jobKey", status=400, mimetype="text/plain")

        client = ApplicationsReportClient()
        payload = client.fetch_job_detail_for_date(
            target_date=target_date,
            config=config,
            job_key=job_key,
        )
        html = build_job_detail_html(payload)
        return Response(html, mimetype="text/html")

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


def build_job_detail_html(payload: dict[str, Any]) -> str:
    job = payload["job"]
    candidates = payload["candidates"]
    summary = job["summary"]

    cards = [
        ("Applications", summary["totalApplications"]),
        ("Selected by AI", summary["selectedByAi"]),
        ("Rejected by AI", summary["rejectedByAi"]),
        ("Other Statuses", summary["otherStatuses"]),
        ("Positions", job.get("numberOfPositions") or "-"),
        ("Remote", job.get("remoteJob") or "-"),
    ]
    cards_html = "".join(
        f'<article class="mini-card"><h3>{escape(str(label))}</h3><strong>{escape(str(value))}</strong></article>'
        for label, value in cards
    )

    candidate_sections = []
    for candidate in candidates:
        parsed = candidate.get("parsedResume") or {}
        parsed_meta = []
        for label, value in [
            ("Current Title", parsed.get("headline")),
            ("Employer", parsed.get("currentEmployer")),
            ("Experience", parsed.get("experienceInYears")),
            ("Qualification", parsed.get("highestQualification")),
            ("Additional Info", parsed.get("additionalInfo")),
        ]:
            if value:
                parsed_meta.append(f"<p><strong>{escape(label)}:</strong> {escape(str(value))}</p>")

        experience_items = []
        for item in parsed.get("experience", []):
            title = " at ".join(part for part in [item.get("title") or "", item.get("company") or ""] if part) or "Experience Entry"
            period = " - ".join(part for part in [item.get("from") or "", item.get("to") or ""] if part)
            summary_text = str(item.get("summary") or "")
            period_html = f"<span>{escape(period)}</span>" if period else ""
            summary_html = f"<p>{escape(summary_text)}</p>" if summary_text else ""
            experience_items.append(
                f"<li><strong>{escape(title)}</strong>{period_html}{summary_html}</li>"
            )
        experience_html = "".join(experience_items)

        education_items = []
        for item in parsed.get("education", []):
            title = " - ".join(part for part in [item.get("degree") or "", item.get("specialization") or ""] if part) or "Education Entry"
            school = str(item.get("school") or "")
            period = " - ".join(part for part in [item.get("from") or "", item.get("to") or ""] if part)
            school_html = f"<span>{escape(school)}</span>" if school else ""
            period_html = f"<p>{escape(period)}</p>" if period else ""
            education_items.append(
                f"<li><strong>{escape(title)}</strong>{school_html}{period_html}</li>"
            )
        education_html = "".join(education_items)
        skills = candidate.get("candidateSkillSet") or []
        skills_html = "".join(f'<span class="chip">{escape(str(skill))}</span>' for skill in skills)

        resume_html = (
            f'<a class="button" href="{escape(candidate["resume"]["downloadUrl"])}" target="_blank" rel="noreferrer">Open Resume</a>'
            if candidate.get("resume", {}).get("available") and candidate.get("resume", {}).get("downloadUrl")
            else "<p>No resume attachment found.</p>"
        )

        candidate_sections.append(
            f"""
            <article class="candidate-card">
              <div class="candidate-head">
                <div>
                  <h3>{escape(candidate.get('candidateName') or 'Unknown Candidate')}</h3>
                  <p>{escape(candidate.get('applicationStatus') or 'Unknown Status')}</p>
                  <p>{escape(candidate.get('candidateEmail') or '')}</p>
                  <p>{escape(candidate.get('candidatePhone') or '')}</p>
                </div>
                <div class="score-box">
                  <span>AI Score</span>
                  <strong>{escape(str(candidate.get('aiResumeScore') if candidate.get('aiResumeScore') is not None else '-'))}</strong>
                </div>
              </div>
              <section class="panel">
                <h4>AI Description</h4>
                <pre>{escape(candidate.get('aiDescription') or 'No AI description available.')}</pre>
              </section>
              <section class="panel">
                <h4>Resume</h4>
                <p>{escape(candidate.get('resume', {}).get('fileName') or 'Resume attachment available.')}</p>
                {resume_html}
              </section>
              <section class="panel">
                <h4>Skills</h4>
                <div class="chips">{skills_html or '<p>No skills available.</p>'}</div>
              </section>
              <section class="panel">
                <h4>Parsed Resume</h4>
                <div id="parsed-slot-{escape(candidate.get('candidateId') or '')}">
                  {''.join(parsed_meta) or '<p class="muted">Parsed resume not loaded yet.</p>'}
                  {f'<h5>Experience</h5><ul>{experience_html}</ul>' if experience_html else ''}
                  {f'<h5>Education</h5><ul>{education_html}</ul>' if education_html else ''}
                </div>
                <button class="button button-secondary load-profile-button" type="button" data-candidate-id="{escape(candidate.get('candidateId') or '')}">Load Parsed Resume</button>
              </section>
            </article>
            """
        )

    return f"""
    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="UTF-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <title>{escape(job.get('jobName') or 'Job Detail')}</title>
        <style>
          body {{ margin: 0; font-family: Segoe UI, Arial, sans-serif; background: #f7f2ea; color: #1e2430; }}
          .page {{ max-width: 1200px; margin: 24px auto; padding: 0 16px 40px; }}
          .topbar, .panel, .mini-card, .candidate-card {{ background: #fffdf9; border: 1px solid #e6dacb; border-radius: 16px; box-shadow: 0 12px 30px rgba(0,0,0,.06); }}
          .topbar {{ padding: 20px; }}
          .topbar a {{ color: #0d5c63; text-decoration: none; font-weight: 700; }}
          .meta {{ color: #5d6677; margin-top: 8px; }}
          .grid {{ display: grid; grid-template-columns: repeat(6, minmax(0,1fr)); gap: 12px; margin-top: 18px; }}
          .mini-card {{ padding: 16px; }}
          .mini-card h3 {{ margin: 0; font-size: 14px; color: #5d6677; }}
          .mini-card strong {{ display: block; margin-top: 10px; font-size: 28px; }}
          .panel {{ padding: 18px; margin-top: 18px; }}
          .rich-copy {{ line-height: 1.6; }}
          .candidate-list {{ display: grid; gap: 16px; margin-top: 18px; }}
          .candidate-card {{ padding: 18px; }}
          .candidate-head {{ display: flex; justify-content: space-between; gap: 16px; }}
          .candidate-head h3 {{ margin: 0; }}
          .candidate-head p {{ margin: 6px 0 0; color: #5d6677; }}
          .score-box {{ min-width: 120px; text-align: center; background: #eef6f6; border-radius: 12px; padding: 12px; }}
          .score-box span {{ display: block; color: #5d6677; font-size: 12px; text-transform: uppercase; }}
          .score-box strong {{ display: block; margin-top: 6px; font-size: 28px; }}
          .chips {{ display: flex; flex-wrap: wrap; gap: 8px; }}
          .chip {{ display: inline-block; padding: 8px 12px; border: 1px solid #e6dacb; border-radius: 999px; background: #fff7ef; }}
          .button {{ display: inline-block; margin-top: 8px; padding: 10px 14px; border-radius: 10px; background: #0d5c63; color: white; text-decoration: none; }}
          .button-secondary {{ background: #efe3d2; color: #1e2430; border: 0; cursor: pointer; }}
          .muted {{ color: #5d6677; }}
          pre {{ white-space: pre-wrap; font-family: inherit; color: #5d6677; }}
          ul {{ padding-left: 20px; }}
          li + li {{ margin-top: 8px; }}
          @media (max-width: 960px) {{ .grid {{ grid-template-columns: repeat(2, minmax(0,1fr)); }} .candidate-head {{ flex-direction: column; }} }}
          @media (max-width: 560px) {{ .grid {{ grid-template-columns: 1fr; }} }}
        </style>
      </head>
      <body>
        <div class="page">
          <div class="topbar">
            <a href="/">&larr; Back to report</a>
            <h1>{escape(job.get('jobName') or 'Unknown Job')}</h1>
            <p class="meta">{escape(job.get('jobPublicId') or job.get('jobId') or 'No Job ID')} | {escape(payload['meta']['reportDate'])}</p>
            <p class="meta">{escape(job.get('clientName') or '')}</p>
          </div>
          <section class="grid">{cards_html}</section>
          <section class="panel">
            <h2>Job Description</h2>
            <div class="rich-copy">{job.get('jobDescriptionHtml') or '<p>No job description available.</p>'}</div>
          </section>
          <section class="panel">
            <h2>Candidates ({len(candidates)})</h2>
            <div class="candidate-list">{''.join(candidate_sections)}</div>
          </section>
        </div>
        <script>
          function escapeHtml(value) {{
            return String(value)
              .replaceAll("&", "&amp;")
              .replaceAll("<", "&lt;")
              .replaceAll(">", "&gt;")
              .replaceAll('"', "&quot;")
              .replaceAll("'", "&#39;");
          }}

          function renderParsedResume(parsed) {{
            const meta = [];
            if (parsed.headline) meta.push(`<p><strong>Current Title:</strong> ${{escapeHtml(parsed.headline)}}</p>`);
            if (parsed.currentEmployer) meta.push(`<p><strong>Employer:</strong> ${{escapeHtml(parsed.currentEmployer)}}</p>`);
            if (parsed.experienceInYears) meta.push(`<p><strong>Experience:</strong> ${{escapeHtml(parsed.experienceInYears)}}</p>`);
            if (parsed.highestQualification) meta.push(`<p><strong>Qualification:</strong> ${{escapeHtml(parsed.highestQualification)}}</p>`);
            if (parsed.additionalInfo) meta.push(`<p><strong>Additional Info:</strong> ${{escapeHtml(parsed.additionalInfo)}}</p>`);

            const experience = (parsed.experience || []).map((item) => {{
              const title = [item.title, item.company].filter(Boolean).join(" at ");
              const period = [item.from, item.to].filter(Boolean).join(" - ");
              return `<li><strong>${{escapeHtml(title || "Experience Entry")}}</strong>${{period ? `<span>${{escapeHtml(period)}}</span>` : ""}}${{item.summary ? `<p>${{escapeHtml(item.summary)}}</p>` : ""}}</li>`;
            }}).join("");

            const education = (parsed.education || []).map((item) => {{
              const title = [item.degree, item.specialization].filter(Boolean).join(" - ");
              const period = [item.from, item.to].filter(Boolean).join(" - ");
              return `<li><strong>${{escapeHtml(title || "Education Entry")}}</strong>${{item.school ? `<span>${{escapeHtml(item.school)}}</span>` : ""}}${{period ? `<p>${{escapeHtml(period)}}</p>` : ""}}</li>`;
            }}).join("");

            return `
              ${{meta.join("") || "<p class='muted'>No parsed resume fields available.</p>"}}
              ${{experience ? `<h5>Experience</h5><ul>${{experience}}</ul>` : ""}}
              ${{education ? `<h5>Education</h5><ul>${{education}}</ul>` : ""}}
            `;
          }}

          document.querySelectorAll(".load-profile-button").forEach((button) => {{
            button.addEventListener("click", async () => {{
              const candidateId = button.dataset.candidateId;
              if (!candidateId) return;
              button.disabled = true;
              button.textContent = "Loading...";
              try {{
                const response = await fetch(`/api/report/candidates/${{encodeURIComponent(candidateId)}}/profile`, {{
                  headers: {{ Accept: "application/json" }}
                }});
                const payload = await response.json();
                if (!response.ok) throw new Error(payload?.error?.message || "Failed to load parsed resume.");
                const slot = document.getElementById(`parsed-slot-${{candidateId}}`);
                if (slot) {{
                  slot.innerHTML = renderParsedResume(payload.parsedResume || {{}});
                }}
                button.textContent = "Parsed Resume Loaded";
              }} catch (error) {{
                button.disabled = false;
                button.textContent = "Retry Parsed Resume";
              }}
            }});
          }});
        </script>
      </body>
    </html>
    """


if __name__ == "__main__":
    application = create_app()
    application.run(host=os.getenv("REPORT_APP_HOST", "127.0.0.1"), port=int(os.getenv("REPORT_APP_PORT", "8000")), debug=False)

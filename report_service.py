from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Iterable, List
from urllib.parse import quote

import requests

try:
    from .config import ReportConfig, get_day_bounds
except ImportError:
    from config import ReportConfig, get_day_bounds


UNKNOWN_JOB_LABEL = "Unknown Job"
ZOHO_REQUEST_TIMEOUT_SECONDS = float(os.getenv("ZOHO_REQUEST_TIMEOUT_SECONDS", "90"))
ZOHO_REQUEST_MAX_ATTEMPTS = max(1, int(os.getenv("ZOHO_REQUEST_MAX_ATTEMPTS", "3")))
ZOHO_REQUEST_RETRY_DELAY_SECONDS = float(os.getenv("ZOHO_REQUEST_RETRY_DELAY_SECONDS", "2"))


@dataclass(frozen=True)
class ApplicationRecord:
    application_id: str
    created_time: str
    ai_processed: bool
    application_status: str
    job_key: str
    job_id: str
    job_public_id: str
    job_name: str


class ZohoRecruitClient:
    def __init__(self) -> None:
        self.base_url = os.environ["ZOHO_RECRUIT_BASE_URL"].rstrip("/")
        self.accounts_domain = os.environ["ZOHO_RECRUIT_ACCOUNTS_DOMAIN"].rstrip("/")
        self.access_token = os.environ["ZOHO_RECRUIT_ACCESS_TOKEN"]
        self.refresh_token = os.environ["ZOHO_RECRUIT_REFRESH_TOKEN"]
        self.client_id = os.environ["ZOHO_RECRUIT_CLIENT_ID"]
        self.client_secret = os.environ["ZOHO_RECRUIT_CLIENT_SECRET"]
        self.session = requests.Session()

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Zoho-oauthtoken {self.access_token}",
            "Content-Type": "application/json",
        }

    def _send_request_with_retries(
        self,
        method: str,
        url: str,
        *,
        headers: Dict[str, str] | None = None,
        params: Dict[str, Any] | None = None,
        json_body: Dict[str, Any] | None = None,
        data: Dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(1, ZOHO_REQUEST_MAX_ATTEMPTS + 1):
            try:
                return self.session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    json=json_body,
                    data=data,
                    timeout=timeout or ZOHO_REQUEST_TIMEOUT_SECONDS,
                )
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last_error = exc
                if attempt >= ZOHO_REQUEST_MAX_ATTEMPTS:
                    break
                time.sleep(ZOHO_REQUEST_RETRY_DELAY_SECONDS * attempt)

        raise RuntimeError(
            f"Zoho request failed after {ZOHO_REQUEST_MAX_ATTEMPTS} attempts: {method} {url} :: {last_error}"
        ) from last_error

    def refresh_access_token(self) -> None:
        response = self._send_request_with_retries(
            "POST",
            f"{self.accounts_domain}/oauth/v2/token",
            data={
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
            },
        )
        if not response.ok:
            raise RuntimeError(f"Unable to refresh Zoho access token: {response.status_code} {response.text}")

        payload = response.json()
        access_token = payload.get("access_token")
        if not access_token:
            raise RuntimeError("Zoho token refresh response did not return access_token.")
        self.access_token = access_token

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Dict[str, Any] | None = None,
        json_body: Dict[str, Any] | None = None,
        retry_on_auth: bool = True,
    ) -> Dict[str, Any]:
        response = self._send_request_with_retries(
            method=method,
            url=f"{self.base_url}/recruit/v2/{path.lstrip('/')}",
            headers=self._headers(),
            params=params,
            json_body=json_body,
        )

        if response.status_code == 401 and retry_on_auth:
            self.refresh_access_token()
            return self._request(
                method,
                path,
                params=params,
                json_body=json_body,
                retry_on_auth=False,
            )

        if not response.ok:
            raise RuntimeError(f"Zoho API error {response.status_code}: {response.text}")
        if not response.text.strip():
            return {}
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Zoho returned a non-JSON response: "
                f"status={response.status_code} content_type={response.headers.get('content-type')} "
                f"body={response.text[:500]!r}"
            ) from exc


class ApplicationsReportClient(ZohoRecruitClient):
    def fetch_ai_processed_applications_for_date(
        self,
        *,
        target_date: date,
        config: ReportConfig,
    ) -> List[ApplicationRecord]:
        start, end = get_day_bounds(target_date, config)
        criteria = (
            f"((AI_Processed:equals:true)"
            f"and(Created_Time:between:{start.isoformat()},{end.isoformat()}))"
        )

        page = 1
        seen_ids: set[str] = set()
        records: List[ApplicationRecord] = []

        while True:
            payload = self._request(
                "GET",
                f"{quote('Applications', safe='')}/search",
                params={
                    "criteria": criteria,
                    "per_page": str(config.page_size),
                    "page": str(page),
                    "approved": "both",
                },
            )
            for item in payload.get("data", []):
                normalized = normalize_application_record(item)
                if not normalized.application_id or normalized.application_id in seen_ids:
                    continue
                seen_ids.add(normalized.application_id)
                records.append(normalized)

            info = payload.get("info", {})
            if not info.get("more_records"):
                break
            page += 1

        return records


def normalize_application_record(item: Dict[str, Any]) -> ApplicationRecord:
    application_id = str(item.get("id") or item.get("Application_ID") or "").strip()
    created_time = str(item.get("Created_Time") or "").strip()
    application_status = str(item.get("Application_Status") or "").strip()
    job_numeric_id = str(item.get("$Job_Opening_Id") or "").strip()
    job_public_id = str(item.get("Job_Opening_ID") or "").strip()
    job_name = (
        str(item.get("Potential_Name") or item.get("Posting_Title") or item.get("Application_Name__s") or "").strip()
    )
    if not job_name and application_status:
        job_name = UNKNOWN_JOB_LABEL

    job_key = job_numeric_id or job_public_id or job_name or UNKNOWN_JOB_LABEL
    return ApplicationRecord(
        application_id=application_id,
        created_time=created_time,
        ai_processed=bool(item.get("AI_Processed")),
        application_status=application_status or "Unknown",
        job_key=job_key,
        job_id=job_numeric_id or "",
        job_public_id=job_public_id or "",
        job_name=job_name or UNKNOWN_JOB_LABEL,
    )


def build_report_payload(
    *,
    applications: Iterable[ApplicationRecord],
    target_date: date,
    config: ReportConfig,
) -> Dict[str, Any]:
    selected_status = config.selected_status
    rejected_status = config.rejected_status

    jobs: Dict[str, Dict[str, Any]] = {}
    global_other_breakdown: Dict[str, int] = {}
    total_applications = 0
    total_selected = 0
    total_rejected = 0
    total_other = 0

    for application in applications:
        total_applications += 1
        job = jobs.setdefault(
            application.job_key,
            {
                "jobKey": application.job_key,
                "jobId": application.job_id,
                "jobPublicId": application.job_public_id,
                "jobName": application.job_name or UNKNOWN_JOB_LABEL,
                "totalApplications": 0,
                "selectedByAi": 0,
                "rejectedByAi": 0,
                "otherStatuses": 0,
                "otherStatusBreakdown": {},
            },
        )
        job["totalApplications"] += 1

        status = application.application_status or "Unknown"
        if status == selected_status:
            job["selectedByAi"] += 1
            total_selected += 1
        elif status == rejected_status:
            job["rejectedByAi"] += 1
            total_rejected += 1
        else:
            job["otherStatuses"] += 1
            total_other += 1
            job["otherStatusBreakdown"][status] = job["otherStatusBreakdown"].get(status, 0) + 1
            global_other_breakdown[status] = global_other_breakdown.get(status, 0) + 1

    rows = sorted(
        jobs.values(),
        key=lambda item: (-item["totalApplications"], item["jobName"].lower(), item["jobPublicId"], item["jobId"]),
    )
    for row in rows:
        total = row["totalApplications"] or 0
        row["aiSelectionRate"] = round((row["selectedByAi"] / total) * 100, 2) if total else 0.0
        row["otherStatusBreakdown"] = dict(sorted(row["otherStatusBreakdown"].items(), key=lambda item: item[0].lower()))

    generated_at = datetime.now(config.timezone).isoformat()
    report_date_label = target_date.strftime("%Y-%m-%d")

    return {
        "meta": {
            "reportDate": report_date_label,
            "timezone": config.timezone_name,
            "generatedAt": generated_at,
            "filters": {
                "aiProcessed": True,
                "selectedStatus": selected_status,
                "rejectedStatus": rejected_status,
            },
        },
        "summary": {
            "reportDate": report_date_label,
            "totalJobs": len(rows),
            "totalApplications": total_applications,
            "selectedByAi": total_selected,
            "rejectedByAi": total_rejected,
            "otherStatuses": total_other,
        },
        "otherStatusBreakdown": dict(sorted(global_other_breakdown.items(), key=lambda item: item[0].lower())),
        "jobs": rows,
        "grandTotal": {
            "jobName": "Grand Total",
            "jobId": "",
            "jobPublicId": "",
            "totalApplications": total_applications,
            "selectedByAi": total_selected,
            "rejectedByAi": total_rejected,
            "otherStatuses": total_other,
            "aiSelectionRate": round((total_selected / total_applications) * 100, 2) if total_applications else 0.0,
        },
    }

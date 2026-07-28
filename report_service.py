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
    application_name: str
    created_time: str
    ai_processed: bool
    application_status: str
    ai_resume_score: float | None
    ai_screening_status: str
    ai_screening_reason: str
    candidate_id: str
    candidate_name: str
    candidate_email: str
    candidate_phone: str
    candidate_skill_set: str
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

    def get_binary(
        self,
        path: str,
        *,
        retry_on_auth: bool = True,
    ) -> requests.Response:
        response = self._send_request_with_retries(
            "GET",
            f"{self.base_url}/recruit/v2/{path.lstrip('/')}",
            headers={"Authorization": f"Zoho-oauthtoken {self.access_token}"},
        )
        if response.status_code == 401 and retry_on_auth:
            self.refresh_access_token()
            return self.get_binary(path, retry_on_auth=False)
        if not response.ok:
            raise RuntimeError(f"Zoho API error {response.status_code}: {response.text}")
        return response


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

    def fetch_job_detail_for_date(
        self,
        *,
        target_date: date,
        config: ReportConfig,
        job_key: str,
    ) -> Dict[str, Any]:
        applications = self.fetch_ai_processed_applications_for_date(target_date=target_date, config=config)
        job_applications = [application for application in applications if application.job_key == job_key]
        if not job_applications:
            raise RuntimeError("No job details found for the selected date.")

        primary = job_applications[0]
        job_record = self.fetch_job_opening(primary.job_id) if primary.job_id else {}
        candidates = [self.build_candidate_preview(application) for application in job_applications]
        candidates.sort(
            key=lambda item: (
                -(item["aiResumeScore"] if isinstance(item["aiResumeScore"], (int, float)) else -1),
                item["candidateName"].lower(),
            )
        )

        summary = build_report_payload(
            applications=job_applications,
            target_date=target_date,
            config=config,
        )["summary"]

        return {
            "meta": {
                "reportDate": target_date.isoformat(),
                "generatedAt": datetime.now(config.timezone).isoformat(),
                "timezone": config.timezone_name,
            },
            "job": {
                "jobKey": primary.job_key,
                "jobId": primary.job_id,
                "jobPublicId": primary.job_public_id,
                "jobName": primary.job_name,
                "jobDescription": clean_html_text(job_record.get("Job_Description")),
                "jobDescriptionHtml": job_record.get("Job_Description") or "",
                "jobType": stringify(job_record.get("Job_Type")),
                "workExperience": stringify(job_record.get("Work_Experience")),
                "numberOfPositions": stringify(job_record.get("Number_of_Positions")),
                "remoteJob": boolean_label(job_record.get("Remote_Job")),
                "dateOpened": stringify(job_record.get("Date_Opened")),
                "targetDate": stringify(job_record.get("Target_Date")),
                "clientName": nested_name(job_record.get("Client_Name")),
                "summary": {
                    "totalApplications": summary["totalApplications"],
                    "selectedByAi": summary["selectedByAi"],
                    "rejectedByAi": summary["rejectedByAi"],
                    "otherStatuses": summary["otherStatuses"],
                },
            },
            "candidates": candidates,
        }

    def fetch_job_opening(self, job_id: str) -> Dict[str, Any]:
        payload = self._request("GET", f"JobOpenings/{job_id}")
        return payload.get("data", [{}])[0]

    def fetch_candidate(self, candidate_id: str) -> Dict[str, Any]:
        payload = self._request("GET", f"Candidates/{candidate_id}")
        return payload.get("data", [{}])[0]

    def fetch_candidate_attachments(self, candidate_id: str) -> List[Dict[str, Any]]:
        payload = self._request("GET", f"Candidates/{candidate_id}/Attachments")
        return payload.get("data", [])

    def build_candidate_preview(self, application: ApplicationRecord) -> Dict[str, Any]:
        return {
            "applicationId": application.application_id,
            "applicationName": application.application_name,
            "applicationStatus": application.application_status,
            "createdTime": application.created_time,
            "candidateId": application.candidate_id,
            "candidateName": application.candidate_name,
            "candidateEmail": application.candidate_email,
            "candidatePhone": application.candidate_phone,
            "aiResumeScore": application.ai_resume_score,
            "aiScreeningStatus": application.ai_screening_status,
            "aiDescription": application.ai_screening_reason,
            "candidateSkillSet": split_skill_set(application.candidate_skill_set),
            "parsedResume": None,
            "resume": {
                "fileName": "",
                "downloadUrl": (
                    f"/api/report/candidates/{application.candidate_id}/resume"
                    if application.candidate_id
                    else ""
                ),
                "available": bool(application.candidate_id),
            },
        }

    def fetch_candidate_profile(self, candidate_id: str) -> Dict[str, Any]:
        candidate_record = self.fetch_candidate(candidate_id)
        attachments = self.fetch_candidate_attachments(candidate_id)
        resume_attachment = next(
            (
                attachment
                for attachment in attachments
                if "resume" in stringify((attachment.get("Category") or {}).get("name")).lower()
                or "resume" in stringify(attachment.get("File_Name")).lower()
            ),
            attachments[0] if attachments else None,
        )
        return {
            "candidateId": candidate_id,
            "resume": {
                "fileName": stringify(resume_attachment.get("File_Name")) if resume_attachment else "",
                "downloadUrl": f"/api/report/candidates/{candidate_id}/resume" if resume_attachment else "",
                "available": bool(resume_attachment),
            },
            "parsedResume": build_parsed_resume_sections(candidate_record),
        }


def normalize_application_record(item: Dict[str, Any]) -> ApplicationRecord:
    application_id = stringify(item.get("id") or item.get("Application_ID"))
    created_time = stringify(item.get("Created_Time"))
    application_status = stringify(item.get("Application_Status"))
    job_numeric_id = stringify(item.get("$Job_Opening_Id"))
    job_public_id = stringify(item.get("Job_Opening_ID"))
    job_name = stringify(item.get("Potential_Name") or item.get("Posting_Title") or item.get("Application_Name__s"))
    if not job_name and application_status:
        job_name = UNKNOWN_JOB_LABEL

    job_key = job_numeric_id or job_public_id or job_name or UNKNOWN_JOB_LABEL
    return ApplicationRecord(
        application_id=application_id,
        application_name=stringify(item.get("Application_Name__s")),
        created_time=created_time,
        ai_processed=bool(item.get("AI_Processed")),
        application_status=application_status or "Unknown",
        ai_resume_score=parse_float(item.get("AI_Resume_Score")),
        ai_screening_status=stringify(item.get("AI_Screening_Status")),
        ai_screening_reason=stringify(item.get("AI_Screening_Reason_2")),
        candidate_id=stringify(item.get("$Candidate_Id")),
        candidate_name=stringify(item.get("Full_Name") or build_full_name(item)),
        candidate_email=stringify(item.get("Email")),
        candidate_phone=stringify(item.get("Phone") or item.get("Mobile")),
        candidate_skill_set=stringify(item.get("Skill_Set")),
        job_key=job_key,
        job_id=job_numeric_id,
        job_public_id=job_public_id,
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


def build_full_name(item: Dict[str, Any]) -> str:
    parts = [stringify(item.get("First_Name")), stringify(item.get("Last_Name"))]
    return " ".join(part for part in parts if part).strip()


def nested_name(value: Any) -> str:
    if isinstance(value, dict):
        return stringify(value.get("name"))
    return stringify(value)


def stringify(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def parse_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def boolean_label(value: Any) -> str:
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    return ""


def split_skill_set(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def clean_html_text(value: str) -> str:
    return stringify(value)


def build_parsed_resume_sections(candidate_record: Dict[str, Any]) -> Dict[str, Any]:
    experience = candidate_record.get("Experience_Details") or []
    education = candidate_record.get("Educational_Details") or []
    return {
        "headline": stringify(candidate_record.get("Current_Job_Title")),
        "currentEmployer": stringify(candidate_record.get("Current_Employer")),
        "experienceInYears": stringify(candidate_record.get("Experience_in_Years")),
        "highestQualification": stringify(candidate_record.get("Highest_Qualification_Held")),
        "additionalInfo": stringify(candidate_record.get("Additional_Info")),
        "experience": [
            {
                "company": stringify(item.get("Company")),
                "title": stringify(item.get("Occupation")),
                "from": stringify(item.get("From")),
                "to": stringify(item.get("To")),
                "summary": stringify(item.get("Description")),
            }
            for item in experience
        ],
        "education": [
            {
                "school": stringify(item.get("Institute_School")),
                "degree": stringify(item.get("Degree")),
                "specialization": stringify(item.get("Major")),
                "from": stringify(item.get("From")),
                "to": stringify(item.get("To")),
            }
            for item in education
        ],
    }

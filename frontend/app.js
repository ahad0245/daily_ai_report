const state = {
  config: null,
  report: null,
  filteredJobs: [],
  searchQuery: "",
  sortKey: "totalApplications",
  sortDirection: "desc",
  abortController: null,
};

const elements = {
  reportDateInput: document.getElementById("report-date"),
  jobSearchInput: document.getElementById("job-search"),
  refreshButton: document.getElementById("refresh-button"),
  exportButton: document.getElementById("export-button"),
  retryButton: document.getElementById("retry-button"),
  timezonePill: document.getElementById("timezone-pill"),
  lastUpdated: document.getElementById("last-updated"),
  summaryCards: document.getElementById("summary-cards"),
  loadingState: document.getElementById("loading-state"),
  errorState: document.getElementById("error-state"),
  errorMessage: document.getElementById("error-message"),
  emptyState: document.getElementById("empty-state"),
  reportContent: document.getElementById("report-content"),
  tableBody: document.getElementById("report-table-body"),
  tableFoot: document.getElementById("report-table-foot"),
  tableCaption: document.getElementById("table-caption"),
  statusBreakdownPanel: document.getElementById("status-breakdown-panel"),
  statusBreakdownList: document.getElementById("status-breakdown-list"),
  sortButtons: Array.from(document.querySelectorAll("th button[data-sort]")),
};

bootstrap().catch((error) => {
  showError(error.message || "Failed to initialize the report.");
});

async function bootstrap() {
  attachListeners();
  state.config = await fetchJson("/api/report/config");
  elements.timezonePill.textContent = state.config.timezone;
  elements.reportDateInput.value = state.config.defaultReportDate;
  await loadReport(state.config.defaultReportDate);
}

function attachListeners() {
  elements.refreshButton.addEventListener("click", () => loadReport(elements.reportDateInput.value));
  elements.retryButton.addEventListener("click", () => loadReport(elements.reportDateInput.value));
  elements.reportDateInput.addEventListener("change", () => loadReport(elements.reportDateInput.value));
  elements.jobSearchInput.addEventListener("input", (event) => {
    state.searchQuery = event.target.value.trim().toLowerCase();
    applyFiltersAndRender();
  });
  elements.exportButton.addEventListener("click", exportCsv);
  elements.sortButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.sort;
      if (!key) return;
      if (state.sortKey === key) {
        state.sortDirection = state.sortDirection === "asc" ? "desc" : "asc";
      } else {
        state.sortKey = key;
        state.sortDirection = key === "jobName" || key === "jobPublicId" ? "asc" : "desc";
      }
      applyFiltersAndRender();
    });
  });
}

async function loadReport(reportDate) {
  if (!reportDate) return;

  if (state.abortController) {
    state.abortController.abort();
  }
  state.abortController = new AbortController();

  setLoading(true);
  hideError();

  try {
    const report = await fetchJson(`/api/report/daily-ai-applicant-processing?date=${encodeURIComponent(reportDate)}`, {
      signal: state.abortController.signal,
    });
    state.report = report;
    state.searchQuery = elements.jobSearchInput.value.trim().toLowerCase();
    renderSummary(report.summary);
    renderOtherStatusBreakdown(report.otherStatusBreakdown);
    applyFiltersAndRender();
    elements.lastUpdated.textContent = `Last updated: ${formatDateTime(report.meta.generatedAt, state.config.timezone)}`;
  } catch (error) {
    if (error.name === "AbortError") return;
    showError(error.message || "The report request failed.");
  } finally {
    setLoading(false);
  }
}

function applyFiltersAndRender() {
  if (!state.report) return;

  const jobs = [...state.report.jobs];
  const filtered = jobs.filter((job) => {
    if (!state.searchQuery) return true;
    const haystack = `${job.jobName} ${job.jobPublicId || ""} ${job.jobId || ""}`.toLowerCase();
    return haystack.includes(state.searchQuery);
  });

  filtered.sort((left, right) => compareRows(left, right, state.sortKey, state.sortDirection));
  state.filteredJobs = filtered;

  const filteredGrandTotal = buildFilteredGrandTotal(filtered);
  elements.tableCaption.textContent = `${filtered.length} job${filtered.length === 1 ? "" : "s"} shown for ${state.report.summary.reportDate}.`;

  if (!filtered.length && state.report.jobs.length) {
    showEmpty("No jobs matched the current search.");
    return;
  }
  if (!state.report.jobs.length) {
    showEmpty("No AI processed applications matched the selected date.");
    return;
  }

  hideEmpty();
  renderTable(filtered, filteredGrandTotal);
}

function compareRows(left, right, sortKey, sortDirection) {
  const direction = sortDirection === "asc" ? 1 : -1;
  const leftValue = left?.[sortKey] ?? "";
  const rightValue = right?.[sortKey] ?? "";

  if (typeof leftValue === "number" && typeof rightValue === "number") {
    if (leftValue !== rightValue) return (leftValue - rightValue) * direction;
  } else {
    const comparison = String(leftValue).localeCompare(String(rightValue), undefined, { sensitivity: "base" });
    if (comparison !== 0) return comparison * direction;
  }

  if (left.totalApplications !== right.totalApplications) {
    return right.totalApplications - left.totalApplications;
  }
  return String(left.jobName).localeCompare(String(right.jobName), undefined, { sensitivity: "base" });
}

function buildFilteredGrandTotal(rows) {
  const totals = rows.reduce(
    (accumulator, row) => {
      accumulator.totalApplications += row.totalApplications;
      accumulator.selectedByAi += row.selectedByAi;
      accumulator.rejectedByAi += row.rejectedByAi;
      accumulator.otherStatuses += row.otherStatuses;
      return accumulator;
    },
    {
      totalApplications: 0,
      selectedByAi: 0,
      rejectedByAi: 0,
      otherStatuses: 0,
    },
  );

  return {
    ...totals,
    aiSelectionRate: totals.totalApplications
      ? roundToTwo((totals.selectedByAi / totals.totalApplications) * 100)
      : 0,
  };
}

function renderSummary(summary) {
  const cards = [
    { title: "Report Date", value: summary.reportDate, note: "Default is yesterday." },
    { title: "Total Jobs", value: summary.totalJobs, note: "Jobs that received AI processed applications." },
    { title: "Total Applications", value: summary.totalApplications, note: "Deduplicated by application ID." },
    { title: "Selected by AI", value: summary.selectedByAi, note: state.config.statusConfig.selectedByAi },
    { title: "Rejected by AI", value: summary.rejectedByAi, note: state.config.statusConfig.rejectedByAi },
    { title: "Other Statuses", value: summary.otherStatuses, note: "All remaining application statuses." },
  ];

  elements.summaryCards.innerHTML = cards
    .map(
      (card) => `
        <article>
          <h2>${escapeHtml(card.title)}</h2>
          <strong>${escapeHtml(String(card.value))}</strong>
          <p>${escapeHtml(card.note)}</p>
        </article>
      `,
    )
    .join("");
}

function renderOtherStatusBreakdown(breakdown) {
  const entries = Object.entries(breakdown || {});
  if (!entries.length) {
    elements.statusBreakdownPanel.hidden = true;
    elements.statusBreakdownList.innerHTML = "";
    return;
  }

  elements.statusBreakdownPanel.hidden = false;
  elements.statusBreakdownList.innerHTML = entries
    .map(
      ([status, count]) => `<span class="status-chip"><span>${escapeHtml(status)}</span><strong>${escapeHtml(String(count))}</strong></span>`,
    )
    .join("");
}

function renderTable(rows, totals) {
  elements.tableBody.innerHTML = rows
    .map((row) => {
      const otherDetails = Object.entries(row.otherStatusBreakdown || {})
        .map(([status, count]) => `${status}: ${count}`)
        .join(" • ");

      return `
        <tr>
          <td>
            <div class="job-title">${escapeHtml(row.jobName || "Unknown Job")}</div>
            <div class="job-meta">${escapeHtml(row.jobId || "No numeric job ID")}</div>
            ${otherDetails ? `<div class="row-note">${escapeHtml(otherDetails)}</div>` : ""}
          </td>
          <td>${escapeHtml(row.jobPublicId || row.jobId || "—")}</td>
          <td><span class="metric">${escapeHtml(String(row.totalApplications))}</span></td>
          <td><span class="metric metric-good">${escapeHtml(String(row.selectedByAi))}</span></td>
          <td><span class="metric metric-bad">${escapeHtml(String(row.rejectedByAi))}</span></td>
          <td><span class="metric metric-other">${escapeHtml(String(row.otherStatuses))}</span></td>
          <td><span class="rate-pill">${escapeHtml(formatPercent(row.aiSelectionRate))}</span></td>
        </tr>
      `;
    })
    .join("");

  elements.tableFoot.innerHTML = `
    <tr class="tfoot-row">
      <td>Grand Total</td>
      <td>—</td>
      <td>${escapeHtml(String(totals.totalApplications))}</td>
      <td>${escapeHtml(String(totals.selectedByAi))}</td>
      <td>${escapeHtml(String(totals.rejectedByAi))}</td>
      <td>${escapeHtml(String(totals.otherStatuses))}</td>
      <td>${escapeHtml(formatPercent(totals.aiSelectionRate))}</td>
    </tr>
  `;

  elements.reportContent.hidden = false;
}

function exportCsv() {
  if (!state.report || !state.filteredJobs.length) return;
  const totals = buildFilteredGrandTotal(state.filteredJobs);
  const reportDate = state.report.summary.reportDate;
  const header = [
    "Report Date",
    "Job Name",
    "Job ID",
    "Total Applications",
    "Selected by AI",
    "Rejected by AI",
    "Other Statuses",
    "AI Selection Rate",
  ];

  const rows = state.filteredJobs.map((job) => [
    reportDate,
    job.jobName || "Unknown Job",
    job.jobPublicId || job.jobId || "",
    job.totalApplications,
    job.selectedByAi,
    job.rejectedByAi,
    job.otherStatuses,
    formatPercent(job.aiSelectionRate),
  ]);

  rows.push([
    reportDate,
    "Grand Total",
    "",
    totals.totalApplications,
    totals.selectedByAi,
    totals.rejectedByAi,
    totals.otherStatuses,
    formatPercent(totals.aiSelectionRate),
  ]);

  const csvContent = [header, ...rows]
    .map((row) => row.map((value) => `"${String(value ?? "").replaceAll('"', '""')}"`).join(","))
    .join("\n");

  const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `daily-ai-applicant-processing-${reportDate}.csv`;
  anchor.click();
  URL.revokeObjectURL(url);
}

function setLoading(isLoading) {
  elements.loadingState.hidden = !isLoading;
  elements.refreshButton.disabled = isLoading;
  elements.exportButton.disabled = isLoading;
  if (isLoading) {
    elements.reportContent.hidden = true;
    elements.emptyState.hidden = true;
  }
}

function showError(message) {
  elements.errorMessage.textContent = message;
  elements.errorState.hidden = false;
  elements.emptyState.hidden = true;
  elements.reportContent.hidden = true;
}

function hideError() {
  elements.errorState.hidden = true;
}

function showEmpty(message) {
  elements.emptyState.querySelector("p").textContent = message;
  elements.emptyState.hidden = false;
  elements.reportContent.hidden = true;
}

function hideEmpty() {
  elements.emptyState.hidden = true;
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: {
      Accept: "application/json",
    },
    ...options,
  });

  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload?.error?.message || `Request failed with status ${response.status}`);
  }
  return payload;
}

function formatDateTime(value, timezone) {
  const date = new Date(value);
  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: timezone,
  }).format(date);
}

function formatPercent(value) {
  return `${roundToTwo(Number(value || 0)).toFixed(2)}%`;
}

function roundToTwo(value) {
  return Math.round(value * 100) / 100;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}


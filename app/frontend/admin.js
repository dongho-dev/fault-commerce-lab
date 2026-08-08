"use strict";

const elements = {
  clock: document.querySelector("[data-clock]"),
  environmentDot: document.querySelector("[data-environment-dot]"),
  environmentLabel: document.querySelector("[data-environment-label]"),
  systemPill: document.querySelector("[data-system-pill]"),
  systemStatus: document.querySelector("[data-system-status]"),
  live: document.querySelector("[data-live-value]"),
  ready: document.querySelector("[data-ready-value]"),
  attempts: document.querySelector("[data-attempt-value]"),
  rejections: document.querySelector("[data-rejection-value]"),
  httpTotal: document.querySelector("[data-http-total]"),
  attemptDetail: document.querySelector("[data-attempt-detail]"),
  confirmedDetail: document.querySelector("[data-confirmed-detail]"),
  rejectionDetail: document.querySelector("[data-rejection-detail]"),
  metricTime: document.querySelector("[data-metric-time]"),
  signalNumber: document.querySelector("[data-signal-number]"),
  form: document.querySelector("#probe-form"),
  runLabel: document.querySelector("[data-run-label]"),
  panelState: document.querySelector("[data-panel-state]"),
  verdict: document.querySelector("[data-verdict-panel]"),
  verdictCode: document.querySelector("[data-verdict-code]"),
  verdictKicker: document.querySelector("[data-verdict-kicker]"),
  verdictTitle: document.querySelector("[data-verdict-title]"),
  verdictMessage: document.querySelector("[data-verdict-message]"),
  equation: document.querySelector("[data-equation]"),
  results: document.querySelector("[data-results]"),
  resultSuccess: document.querySelector("[data-result-success]"),
  resultRejected: document.querySelector("[data-result-rejected]"),
  resultError: document.querySelector("[data-result-error]"),
  resultP95: document.querySelector("[data-result-p95]"),
  resultDuration: document.querySelector("[data-result-duration]"),
  successBar: document.querySelector("[data-success-bar]"),
  rejectedBar: document.querySelector("[data-rejected-bar]"),
  errorBar: document.querySelector("[data-error-bar]"),
  requestCount: document.querySelector("[data-request-count]"),
  requestRows: document.querySelector("[data-request-rows]"),
  exportButton: document.querySelector("[data-export]"),
  refreshButton: document.querySelector("[data-refresh]"),
  toast: document.querySelector("[data-toast]")
};

let latestReport = null;
let toastTimer = null;
let isRunning = false;

function integer(value) {
  return new Intl.NumberFormat("ko-KR").format(Number(value) || 0);
}

function nowLabel() {
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false
  }).format(new Date()) + " KST";
}

function updateClock() {
  elements.clock.textContent = nowLabel();
}

function showToast(message, isError) {
  window.clearTimeout(toastTimer);
  elements.toast.textContent = message;
  elements.toast.classList.toggle("is-error", Boolean(isError));
  elements.toast.classList.add("is-visible");
  toastTimer = window.setTimeout(function () {
    elements.toast.classList.remove("is-visible");
  }, 3600);
}

function metricValue(payload, name) {
  const escaped = name.replace(/[-/\\^$*+?.()|[\]{}]/g, "\\$&");
  const pattern = new RegExp("^" + escaped + "(?:\\{[^}]*\\})?\\s+([0-9.eE+-]+)$");
  return payload.split(/\r?\n/).reduce(function (sum, line) {
    const match = line.match(pattern);
    return sum + (match ? Number(match[1]) : 0);
  }, 0);
}

async function checkedFetch(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    throw new Error(path + " returned HTTP " + response.status);
  }
  return response;
}

async function refreshSystem() {
  const results = await Promise.allSettled([
    checkedFetch("/health/live"),
    checkedFetch("/health/ready"),
    checkedFetch("/metrics").then(function (response) { return response.text(); })
  ]);

  const live = results[0].status === "fulfilled";
  const ready = results[1].status === "fulfilled";
  const healthy = live && ready;

  elements.live.textContent = live ? "LIVE" : "DOWN";
  elements.ready.textContent = ready ? "READY" : "WAIT";
  elements.environmentLabel.textContent = healthy ? "System healthy" : "System unavailable";
  elements.systemStatus.textContent = healthy ? "All systems nominal" : "Attention required";
  elements.environmentDot.classList.toggle("is-online", healthy);
  elements.environmentDot.classList.toggle("is-offline", !healthy);
  elements.systemPill.classList.toggle("is-online", healthy);
  elements.systemPill.classList.toggle("is-offline", !healthy);

  if (results[2].status === "fulfilled") {
    const metrics = results[2].value;
    const attempts = metricValue(metrics, "commerce_order_attempts_total");
    const confirmed = metricValue(metrics, "commerce_orders_confirmed_total");
    const rejections = metricValue(metrics, "commerce_insufficient_stock_rejections_total");
    const httpTotal = metricValue(metrics, "commerce_http_requests_total");

    elements.attempts.textContent = integer(attempts);
    elements.rejections.textContent = integer(rejections);
    elements.httpTotal.textContent = integer(httpTotal);
    elements.attemptDetail.textContent = integer(attempts);
    elements.confirmedDetail.textContent = integer(confirmed);
    elements.rejectionDetail.textContent = integer(rejections);
    elements.metricTime.textContent = nowLabel();
  } else {
    elements.attempts.textContent = "—";
    elements.rejections.textContent = "—";
    elements.metricTime.textContent = "Metrics unavailable";
  }
}

function formConfiguration() {
  const fields = new FormData(elements.form);
  return {
    stock: Number(fields.get("stock")),
    requests: Number(fields.get("requests")),
    quantity: Number(fields.get("quantity")),
    postalCode: String(fields.get("postal_code") || "").trim()
  };
}

function expectedFor(config) {
  const success = Math.min(config.requests, Math.floor(config.stock / config.quantity));
  return {
    success: success,
    rejected: config.requests - success,
    currentStock: config.stock - success * config.quantity
  };
}

function updateExpectation() {
  const config = formConfiguration();
  const expected = expectedFor(config);
  document.querySelector("[data-expected-success]").textContent = "201 × " + integer(expected.success);
  document.querySelector("[data-expected-rejection]").textContent = "409 × " + integer(expected.rejected);
  document.querySelector("[data-expected-stock]").textContent = "재고 " + integer(expected.currentStock);
  elements.runLabel.textContent = integer(config.requests) + "개 요청 실행";
}

async function apiRequest(method, path, body, requestId) {
  const started = performance.now();
  const options = {
    method: method,
    headers: {
      "Accept": "application/json",
      "X-Request-ID": requestId
    }
  };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }

  try {
    const response = await fetch(path, options);
    const payload = await response.json().catch(function () { return null; });
    return {
      status: response.status,
      elapsedMs: performance.now() - started,
      requestId: response.headers.get("X-Request-ID") || requestId,
      data: payload,
      error: null
    };
  } catch (error) {
    return {
      status: 0,
      elapsedMs: performance.now() - started,
      requestId: requestId,
      data: null,
      error: error instanceof Error ? error.message : String(error)
    };
  }
}

function percentile(values, percentileValue) {
  if (!values.length) return 0;
  const ordered = values.slice().sort(function (a, b) { return a - b; });
  if (ordered.length === 1) return ordered[0];
  const position = (ordered.length - 1) * percentileValue / 100;
  const lower = Math.floor(position);
  const upper = Math.min(lower + 1, ordered.length - 1);
  return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower);
}

function countStatuses(results) {
  return results.reduce(function (counts, result) {
    const key = String(result.status || "error");
    counts[key] = (counts[key] || 0) + 1;
    return counts;
  }, {});
}

function setBusy(busy) {
  isRunning = busy;
  Array.from(elements.form.elements).forEach(function (control) {
    control.disabled = busy;
  });
  elements.panelState.textContent = busy ? "Running" : "Ready";
  elements.panelState.classList.toggle("is-running", busy);
}

function setVerdict(state, report) {
  const mark = elements.verdict.querySelector(".verdict-mark span");
  const equationValues = elements.equation.querySelectorAll("dd");
  elements.verdict.dataset.state = state;

  if (state === "running") {
    mark.textContent = "…";
    elements.verdictCode.textContent = "PROBE IN FLIGHT";
    elements.verdictKicker.textContent = "Concurrent requests dispatched";
    elements.verdictTitle.textContent = "동시에 주문하고 있습니다.";
    elements.verdictMessage.textContent = "상품 생성이 끝났습니다. 모든 응답과 최종 재고를 수집한 뒤 판정합니다.";
    equationValues.forEach(function (node) { node.textContent = "—"; });
    return;
  }

  if (state === "error") {
    mark.textContent = "!";
    elements.verdictCode.textContent = "PROBE ERROR";
    elements.verdictKicker.textContent = "Experiment did not complete";
    elements.verdictTitle.textContent = "검증을 완료하지 못했습니다.";
    elements.verdictMessage.textContent = report.message;
    return;
  }

  mark.textContent = report.passed ? "✓" : "!";
  elements.verdictCode.textContent = report.passed ? "INVARIANT HOLDS" : "INCIDENT DETECTED";
  elements.verdictKicker.textContent = report.passed ? "Observed values match the contract" : "Observed values violate the contract";
  elements.verdictTitle.textContent = report.passed ? "재고 정합성이 유지됐습니다." : "재고 정합성 장애를 발견했습니다.";
  elements.verdictMessage.textContent = report.message;
  equationValues[0].textContent = integer(report.configuration.stock);
  equationValues[1].textContent = integer(report.observed.confirmedQuantity);
  equationValues[2].textContent = integer(report.observed.equationCurrent);
  equationValues[3].textContent = integer(report.observed.currentStock);
}

function appendCell(row, value, className) {
  const cell = document.createElement("td");
  if (className) {
    const chip = document.createElement("span");
    chip.className = className;
    chip.textContent = value;
    cell.append(chip);
  } else {
    cell.textContent = value;
  }
  row.append(cell);
}

function renderRequestRows(results) {
  elements.requestRows.replaceChildren();
  results.forEach(function (result, index) {
    const row = document.createElement("tr");
    let statusClass = "status-chip is-error";
    if (result.status === 201) statusClass = "status-chip is-success";
    if (result.status === 409) statusClass = "status-chip is-rejected";
    appendCell(row, String(index + 1).padStart(2, "0"));
    appendCell(row, result.status ? String(result.status) : "ERR", statusClass);
    appendCell(row, result.elapsedMs.toFixed(1) + " ms");
    appendCell(row, result.requestId);
    elements.requestRows.append(row);
  });
}

function renderReport(report) {
  const total = report.configuration.requests;
  const observed = report.observed;
  const scale = function (value) { return String(total ? value / total : 0); };

  elements.results.hidden = false;
  elements.resultSuccess.textContent = integer(observed.success);
  elements.resultRejected.textContent = integer(observed.rejected);
  elements.resultError.textContent = integer(observed.other);
  elements.resultP95.textContent = observed.p95Ms.toFixed(1) + " ms";
  elements.resultDuration.textContent = "Total " + observed.durationMs.toFixed(1) + " ms";
  elements.successBar.style.transform = "scaleX(" + scale(observed.success) + ")";
  elements.rejectedBar.style.transform = "scaleX(" + scale(observed.rejected) + ")";
  elements.errorBar.style.transform = "scaleX(" + scale(observed.other) + ")";
  elements.requestCount.textContent = integer(total) + " requests";
  elements.signalNumber.textContent = String(Math.max(0, observed.currentStock)).padStart(2, "0");
  elements.exportButton.disabled = false;
  renderRequestRows(report.requests);
}

async function runProbe(event) {
  event.preventDefault();
  if (isRunning || !elements.form.reportValidity()) return;

  const config = formConfiguration();
  const expected = expectedFor(config);
  const runToken = String(Date.now());
  setBusy(true);
  setVerdict("running");
  elements.results.hidden = true;
  elements.exportButton.disabled = true;

  try {
    const productResult = await apiRequest(
      "POST",
      "/products",
      {
        name: "Control Room Probe " + runToken,
        unit_price: 10000,
        initial_stock: config.stock
      },
      "admin-product-" + runToken
    );
    if (productResult.status !== 201 || !productResult.data) {
      throw new Error("테스트 상품 생성 실패 · HTTP " + productResult.status);
    }

    const product = productResult.data;
    const waiting = [];
    let release;
    const barrier = new Promise(function (resolve) { release = resolve; });

    for (let index = 0; index < config.requests; index += 1) {
      waiting.push((async function (requestIndex) {
        await barrier;
        return apiRequest(
          "POST",
          "/orders",
          {
            product_id: product.id,
            quantity: config.quantity,
            postal_code: config.postalCode
          },
          "admin-order-" + runToken + "-" + String(requestIndex + 1)
        );
      })(index));
    }

    const batchStarted = performance.now();
    release();
    const requestResults = await Promise.all(waiting);
    const durationMs = performance.now() - batchStarted;
    const finalProductResult = await apiRequest(
      "GET",
      "/products/" + product.id,
      undefined,
      "admin-observe-" + runToken
    );
    if (finalProductResult.status !== 200 || !finalProductResult.data) {
      throw new Error("최종 재고 조회 실패 · HTTP " + finalProductResult.status);
    }

    const statuses = countStatuses(requestResults);
    const success = statuses["201"] || 0;
    const rejected = statuses["409"] || 0;
    const other = requestResults.length - success - rejected;
    const confirmedQuantity = success * config.quantity;
    const equationCurrent = config.stock - confirmedQuantity;
    const currentStock = Number(finalProductResult.data.current_stock);
    const passed =
      success === expected.success &&
      rejected === expected.rejected &&
      other === 0 &&
      currentStock === expected.currentStock &&
      currentStock === equationCurrent;

    const reasons = [];
    if (success !== expected.success) {
      reasons.push("확정 주문은 기대 " + expected.success + "건이지만 실제 " + success + "건입니다.");
    }
    if (rejected !== expected.rejected) {
      reasons.push("재고 부족 거절은 기대 " + expected.rejected + "건이지만 실제 " + rejected + "건입니다.");
    }
    if (other > 0) {
      reasons.push("예상하지 않은 응답 또는 연결 오류가 " + other + "건 있습니다.");
    }
    if (currentStock !== equationCurrent) {
      reasons.push(
        "초기 " + config.stock + " − 확정 수량 " + confirmedQuantity +
        "의 계산값 " + equationCurrent + "과 실제 재고 " + currentStock + "이 다릅니다."
      );
    }

    const report = {
      generatedAt: new Date().toISOString(),
      passed: passed,
      message: passed
        ? "응답 분포와 최종 재고가 모두 기대값과 일치합니다. " +
          "201 " + success + "건, 409 " + rejected + "건, 최종 재고 " + currentStock + "입니다."
        : reasons.join(" "),
      configuration: config,
      expected: expected,
      observed: {
        success: success,
        rejected: rejected,
        other: other,
        confirmedQuantity: confirmedQuantity,
        equationCurrent: equationCurrent,
        currentStock: currentStock,
        durationMs: durationMs,
        p50Ms: percentile(requestResults.map(function (item) { return item.elapsedMs; }), 50),
        p95Ms: percentile(requestResults.map(function (item) { return item.elapsedMs; }), 95),
        maxMs: Math.max.apply(null, requestResults.map(function (item) { return item.elapsedMs; }))
      },
      product: product,
      requests: requestResults
    };

    latestReport = report;
    setVerdict(passed ? "pass" : "fail", report);
    renderReport(report);
    showToast(
      passed ? "정상 기준 검증 통과 · 재고 불변조건 유지" : "Incident detected · 재고 불변조건 위반",
      !passed
    );
    await refreshSystem();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    setVerdict("error", { message: message });
    showToast(message, true);
  } finally {
    setBusy(false);
  }
}

function exportReport() {
  if (!latestReport) return;
  const blob = new Blob([JSON.stringify(latestReport, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "fault-commerce-probe-" + Date.now() + ".json";
  link.click();
  URL.revokeObjectURL(url);
}

function bindNavigation() {
  const links = Array.from(document.querySelectorAll(".operator-nav a"));
  const targets = links.map(function (link) {
    return document.querySelector(link.getAttribute("href"));
  }).filter(Boolean);

  const observer = new IntersectionObserver(function (entries) {
    const visible = entries.filter(function (entry) { return entry.isIntersecting; }).pop();
    if (!visible) return;
    links.forEach(function (link) {
      link.classList.toggle("is-active", link.getAttribute("href") === "#" + visible.target.id);
    });
  }, { rootMargin: "-20% 0px -65% 0px" });

  targets.forEach(function (target) { observer.observe(target); });
}

function initialize() {
  updateClock();
  updateExpectation();
  bindNavigation();
  elements.form.addEventListener("input", updateExpectation);
  elements.form.addEventListener("submit", runProbe);
  elements.exportButton.addEventListener("click", exportReport);
  elements.refreshButton.addEventListener("click", function () {
    refreshSystem().then(function () {
      showToast("운영 지표를 새로고침했습니다.", false);
    });
  });
  document.querySelector("[data-run-shortcut]").addEventListener("click", function () {
    document.querySelector("#load-test").scrollIntoView({ behavior: "smooth", block: "start" });
    window.setTimeout(function () {
      elements.form.querySelector("input").focus();
    }, 500);
  });

  refreshSystem();
  window.setInterval(updateClock, 1000);
  window.setInterval(function () {
    if (!isRunning) refreshSystem();
  }, 15000);
}

initialize();

"use strict";

const STORAGE_KEY = "fault-commerce-demo-products-v1";
const templates = {};
let toastTimer = null;

const currency = new Intl.NumberFormat("ko-KR", {
  style: "currency",
  currency: "KRW",
  maximumFractionDigits: 0
});

function makeRequestId() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return window.crypto.randomUUID();
  }
  return "web-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
}

function loadSavedTemplates() {
  try {
    const saved = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "{}");
    if (saved && typeof saved === "object") {
      Object.assign(templates, saved);
    }
  } catch (_error) {
    window.localStorage.removeItem(STORAGE_KEY);
  }
}

function saveTemplates() {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(templates));
  } catch (_error) {
    // The interactive demo still works when storage is unavailable.
  }
}

function showToast(message, isError) {
  const toast = document.querySelector("[data-toast]");
  window.clearTimeout(toastTimer);
  toast.textContent = message;
  toast.classList.toggle("is-error", Boolean(isError));
  toast.classList.add("is-visible");
  toastTimer = window.setTimeout(function () {
    toast.classList.remove("is-visible");
  }, 3600);
}

function setBusy(formOrButton, busy) {
  const controls = formOrButton.matches("form")
    ? formOrButton.querySelectorAll("button, input")
    : [formOrButton];
  controls.forEach(function (control) {
    control.disabled = busy;
  });
  formOrButton.setAttribute("aria-busy", String(busy));
}

function activityRow(method, path, status, requestId) {
  const item = document.createElement("li");

  const methodNode = document.createElement("span");
  methodNode.className = "activity-method";
  methodNode.textContent = method;

  const pathNode = document.createElement("span");
  pathNode.className = "activity-path";
  pathNode.textContent = path + " ? " + status;

  const idNode = document.createElement("span");
  idNode.className = "activity-id";
  idNode.title = requestId || "request id unavailable";
  idNode.textContent = requestId || "?";

  item.append(methodNode, pathNode, idNode);
  return item;
}

function addActivity(method, path, status, requestId) {
  const list = document.querySelector("[data-activity-list]");
  const placeholder = list.querySelector(".activity-placeholder");
  if (placeholder) placeholder.remove();

  list.prepend(activityRow(method, path, status, requestId));
  while (list.children.length > 7) {
    list.lastElementChild.remove();
  }
}

async function apiRequest(method, path, body, options) {
  const requestId = makeRequestId();
  const config = {
    method: method,
    headers: {
      "Accept": "application/json",
      "X-Request-ID": requestId
    }
  };

  if (body !== undefined) {
    config.headers["Content-Type"] = "application/json";
    config.body = JSON.stringify(body);
  }

  try {
    const response = await window.fetch(path, config);
    const text = await response.text();
    let data = null;

    if (text) {
      try {
        data = JSON.parse(text);
      } catch (_error) {
        data = { message: text };
      }
    }

    const responseRequestId = response.headers.get("X-Request-ID") || requestId;
    if (!options || !options.silent) {
      addActivity(method, path, response.status, responseRequestId);
    }

    if (!response.ok) {
      const error = new Error((data && data.message) || "요청을 처리하지 못했습니다.");
      error.status = response.status;
      error.data = data || {};
      error.requestId = responseRequestId;
      throw error;
    }

    return {
      data: data,
      status: response.status,
      requestId: responseRequestId
    };
  } catch (error) {
    if (typeof error.status !== "number") {
      error.status = 0;
      error.data = {
        code: "NETWORK_ERROR",
        message: "API 서버에 연결할 수 없습니다."
      };
      error.requestId = requestId;
      if (!options || !options.silent) {
        addActivity(method, path, "ERR", requestId);
      }
    }
    throw error;
  }
}

function summaryEntries(data) {
  if (!data || typeof data !== "object") return [];

  const labels = {
    id: "ID",
    product_id: "Product",
    name: "Name",
    unit_price: "Unit price",
    initial_stock: "Initial stock",
    current_stock: "Current stock",
    quantity: "Quantity",
    shipping_fee: "Shipping",
    total_amount: "Total",
    status: "Order status",
    postal_code: "Postal code",
    code: "Error code",
    request_id: "Request ID"
  };

  return Object.keys(labels)
    .filter(function (key) {
      return Object.prototype.hasOwnProperty.call(data, key);
    })
    .slice(0, 8)
    .map(function (key) {
      let value = data[key];
      if (["unit_price", "shipping_fee", "total_amount"].includes(key) && typeof value === "number") {
        value = currency.format(value);
      }
      return [labels[key], String(value)];
    });
}

function renderResult(config) {
  const empty = document.querySelector("[data-console-empty]");
  const panel = document.querySelector("[data-console-result]");
  const summary = document.querySelector("[data-result-summary]");

  empty.hidden = true;
  panel.hidden = false;
  panel.classList.toggle("is-error", config.status >= 400 || config.status === 0);

  document.querySelector("[data-result-method]").textContent = config.method;
  document.querySelector("[data-result-code]").textContent = config.status === 0 ? "ERR" : String(config.status);
  document.querySelector("[data-result-title]").textContent = config.title;
  document.querySelector("[data-result-message]").textContent = config.message;
  document.querySelector("[data-result-json]").textContent = JSON.stringify(config.data, null, 2);

  summary.replaceChildren();
  summaryEntries(config.data).forEach(function (entry) {
    const group = document.createElement("div");
    const term = document.createElement("dt");
    const value = document.createElement("dd");
    term.textContent = entry[0];
    value.textContent = entry[1];
    group.append(term, value);
    summary.append(group);
  });
}

function renderError(method, title, error) {
  const data = error.data || {
    code: "UNEXPECTED_ERROR",
    message: error.message || "알 수 없는 오류가 발생했습니다."
  };
  renderResult({
    method: method,
    status: error.status || 0,
    title: title,
    message: data.message || error.message,
    data: data
  });
  showToast(data.message || error.message, true);
}

function cardForKey(key) {
  return document.querySelector("[data-product-card='" + key + "']");
}

function updateTemplateCard(key, product) {
  const card = cardForKey(key);
  if (!card) return;

  const stateNode = card.querySelector("[data-product-state]");
  const createButton = card.querySelector(".create-template");
  const orderButton = card.querySelector(".order-template");

  if (!product) {
    stateNode.textContent = "아직 생성되지 않음";
    stateNode.classList.remove("is-created");
    createButton.textContent = "테스트 상품 생성";
    orderButton.disabled = true;
    return;
  }

  stateNode.textContent = "#" + product.id + " · 재고 " + product.current_stock + "/" + product.initial_stock;
  stateNode.classList.add("is-created");
  createButton.textContent = "새 상품 다시 생성";
  orderButton.disabled = product.current_stock < 1;
  orderButton.dataset.productId = String(product.id);
  orderButton.dataset.defaultQuantity = key === "headphones" ? "2" : "1";
}

function findTemplateKeyByProductId(productId) {
  return Object.keys(templates).find(function (key) {
    return Number(templates[key]) === Number(productId);
  });
}

function prepareOrder(key) {
  const productId = templates[key];
  if (!productId) return;

  const form = document.querySelector("#create-order-form");
  form.elements.product_id.value = productId;
  form.elements.quantity.value = key === "headphones" ? 2 : 1;
  document.querySelector("#lab").scrollIntoView({ behavior: "smooth", block: "start" });
  window.setTimeout(function () {
    form.elements.postal_code.focus();
  }, 520);
  showToast("Product #" + productId + " 주문 정보가 준비되었습니다.", false);
}

async function createTemplate(button) {
  const key = button.dataset.templateKey;
  const payload = {
    name: button.dataset.name,
    unit_price: Number(button.dataset.price),
    initial_stock: Number(button.dataset.stock)
  };

  setBusy(button, true);
  try {
    const result = await apiRequest("POST", "/products", payload);
    templates[key] = result.data.id;
    saveTemplates();
    updateTemplateCard(key, result.data);

    const lookupForm = document.querySelector("#lookup-product-form");
    lookupForm.elements.product_id.value = result.data.id;

    renderResult({
      method: "POST",
      status: result.status,
      title: "Product created",
      message: "상품과 초기 재고가 하나의 트랜잭션으로 저장되었습니다.",
      data: result.data
    });
    showToast(result.data.name + " · Product #" + result.data.id + " 생성 완료", false);
  } catch (error) {
    renderError("POST", "Product creation failed", error);
  } finally {
    setBusy(button, false);
  }
}

async function refreshTemplateProduct(key, productId, silent) {
  try {
    const result = await apiRequest("GET", "/products/" + productId, undefined, { silent: silent });
    templates[key] = result.data.id;
    updateTemplateCard(key, result.data);
    return result.data;
  } catch (error) {
    if (error.status === 404) {
      delete templates[key];
      saveTemplates();
      updateTemplateCard(key, null);
      return null;
    }
    if (!silent) renderError("GET", "Product lookup failed", error);
    return null;
  }
}

async function validateSavedTemplates() {
  const keys = Object.keys(templates);
  await Promise.all(keys.map(function (key) {
    return refreshTemplateProduct(key, templates[key], true);
  }));
  saveTemplates();
}

async function checkHealth() {
  const liveState = document.querySelector("[data-live-state]");
  const readyState = document.querySelector("[data-ready-state]");
  const headerStatus = document.querySelector("[data-header-status]");
  const headerDot = document.querySelector("[data-header-dot]");
  const pulse = document.querySelector("[data-health-pulse]");
  const title = document.querySelector("[data-health-title]");

  const results = await Promise.allSettled([
    apiRequest("GET", "/health/live", undefined, { silent: true }),
    apiRequest("GET", "/health/ready", undefined, { silent: true })
  ]);

  const live = results[0].status === "fulfilled";
  const ready = results[1].status === "fulfilled";

  liveState.textContent = live ? "Live" : "Unavailable";
  readyState.textContent = ready ? "Ready" : "Unavailable";
  liveState.className = live ? "is-good" : "is-bad";
  readyState.className = ready ? "is-good" : "is-bad";

  const healthy = live && ready;
  headerStatus.textContent = healthy ? "System healthy" : "System unavailable";
  title.textContent = healthy ? "모든 시스템 정상" : "연결 상태 확인 필요";
  headerDot.classList.toggle("is-online", healthy);
  headerDot.classList.toggle("is-offline", !healthy);
  pulse.classList.toggle("is-online", healthy);
  pulse.classList.toggle("is-offline", !healthy);
}

function bindTemplateCards() {
  document.querySelectorAll(".create-template").forEach(function (button) {
    button.addEventListener("click", function () {
      createTemplate(button);
    });
  });

  document.querySelectorAll(".order-template").forEach(function (button) {
    button.addEventListener("click", function () {
      prepareOrder(button.dataset.templateKey);
    });
  });
}

function bindCreateProductForm() {
  const form = document.querySelector("#create-product-form");
  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    const fields = new FormData(form);
    const payload = {
      name: String(fields.get("name") || "").trim(),
      unit_price: Number(fields.get("unit_price")),
      initial_stock: Number(fields.get("initial_stock"))
    };

    setBusy(form, true);
    try {
      const result = await apiRequest("POST", "/products", payload);
      document.querySelector("#lookup-product-form").elements.product_id.value = result.data.id;
      document.querySelector("#create-order-form").elements.product_id.value = result.data.id;
      renderResult({
        method: "POST",
        status: result.status,
        title: "Product created",
        message: "커스텀 상품과 재고가 준비되었습니다.",
        data: result.data
      });
      showToast("Product #" + result.data.id + " 생성 완료", false);
    } catch (error) {
      renderError("POST", "Product creation failed", error);
    } finally {
      setBusy(form, false);
    }
  });
}

function bindLookupForm() {
  const form = document.querySelector("#lookup-product-form");
  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    const productId = Number(new FormData(form).get("product_id"));
    setBusy(form, true);

    try {
      const result = await apiRequest("GET", "/products/" + productId);
      renderResult({
        method: "GET",
        status: result.status,
        title: "Inventory observed",
        message: "PostgreSQL에 저장된 최신 상품·재고 상태입니다.",
        data: result.data
      });
      const key = findTemplateKeyByProductId(productId);
      if (key) updateTemplateCard(key, result.data);
    } catch (error) {
      renderError("GET", "Product lookup failed", error);
    } finally {
      setBusy(form, false);
    }
  });
}

function bindOrderForm() {
  const form = document.querySelector("#create-order-form");
  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    const fields = new FormData(form);
    const payload = {
      product_id: Number(fields.get("product_id")),
      quantity: Number(fields.get("quantity")),
      postal_code: String(fields.get("postal_code") || "").trim()
    };

    setBusy(form, true);
    try {
      const result = await apiRequest("POST", "/orders", payload);
      renderResult({
        method: "POST",
        status: result.status,
        title: "Order confirmed",
        message: "가격 스냅샷, 재고 차감, 주문 저장이 함께 커밋되었습니다.",
        data: result.data
      });
      showToast("Order #" + result.data.id + " ? " + currency.format(result.data.total_amount), false);

      const key = findTemplateKeyByProductId(payload.product_id);
      if (key) await refreshTemplateProduct(key, payload.product_id, true);
    } catch (error) {
      renderError("POST", "Order was not confirmed", error);
    } finally {
      setBusy(form, false);
    }
  });
}

function bindConsoleClear() {
  document.querySelector("[data-clear-console]").addEventListener("click", function () {
    document.querySelector("[data-console-result]").hidden = true;
    document.querySelector("[data-console-empty]").hidden = false;
    document.querySelector("[data-activity-list]").replaceChildren();

    const placeholder = document.createElement("li");
    placeholder.className = "activity-placeholder";
    placeholder.textContent = "요청 기록이 여기에 쌓입니다.";
    document.querySelector("[data-activity-list]").append(placeholder);
  });
}

function bindNavigation() {
  const header = document.querySelector("[data-header]");
  const toggle = document.querySelector(".nav-toggle");
  const nav = document.querySelector("#site-nav");

  function updateHeader() {
    header.classList.toggle("is-scrolled", window.scrollY > 18);
  }

  function closeNav() {
    toggle.setAttribute("aria-expanded", "false");
    nav.classList.remove("is-open");
    document.body.style.overflow = "";
  }

  toggle.addEventListener("click", function () {
    const opening = toggle.getAttribute("aria-expanded") !== "true";
    toggle.setAttribute("aria-expanded", String(opening));
    nav.classList.toggle("is-open", opening);
    document.body.style.overflow = opening ? "hidden" : "";
  });

  nav.querySelectorAll("a").forEach(function (link) {
    link.addEventListener("click", closeNav);
  });

  window.addEventListener("scroll", updateHeader, { passive: true });
  updateHeader();
}

function setupReveals() {
  const nodes = document.querySelectorAll("[data-reveal]");
  if (!("IntersectionObserver" in window)) {
    nodes.forEach(function (node) {
      node.classList.add("is-visible");
    });
    return;
  }

  const observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (entry.isIntersecting) {
        entry.target.classList.add("is-visible");
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.12 });

  nodes.forEach(function (node) {
    observer.observe(node);
  });
}

async function initialize() {
  loadSavedTemplates();
  bindNavigation();
  bindTemplateCards();
  bindCreateProductForm();
  bindLookupForm();
  bindOrderForm();
  bindConsoleClear();
  setupReveals();

  await Promise.all([checkHealth(), validateSavedTemplates()]);
  window.setInterval(checkHealth, 30000);
}

document.addEventListener("DOMContentLoaded", initialize);

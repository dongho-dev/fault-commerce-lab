"use strict";

(() => {
  const CATEGORIES = [
    { slug: "digital", label: "디지털·가전", icon: "digital", image: "keyboard" },
    { slug: "home", label: "홈·리빙", icon: "home", image: "blanket" },
    { slug: "kitchen", label: "주방용품", icon: "kitchen", image: "dripset" },
    { slug: "food", label: "식품", icon: "food", image: "tangerine" },
    { slug: "beauty", label: "뷰티", icon: "beauty", image: "toner" },
    { slug: "sports", label: "스포츠·레저", icon: "sports", image: "yogamat" },
  ];
  const ETC = { slug: "etc", label: "기타", icon: "etc", image: null };
  const SORTS = [
    ["recommended", "추천순"],
    ["price_asc", "낮은가격순"],
    ["price_desc", "높은가격순"],
    ["discount", "할인율순"],
    ["newest", "최신순"],
  ];
  const HERO_SLIDES = [
    {
      bg: "#e8eefc",
      eyebrow: "디지털 위크",
      title: "작업 책상을 바꾸는<br>키보드·헤드폰 특가",
      text: "기계식 키보드, 무선 마우스, QHD 모니터를 정가보다 낮은 가격에.",
      href: "#/search?category=digital&sort=discount",
      image: "/static/assets/hero-workspace.jpg",
    },
    {
      bg: "#fff1de",
      eyebrow: "산지에서 바로",
      title: "제주 노지 감귤과<br>든든한 아침 식탁",
      text: "감귤, 신선란, 그래놀라까지 매일 먹는 식품을 한 번에.",
      href: "#/search?category=food",
      image: "/static/assets/products/tangerine.jpg",
    },
    {
      bg: "#e9f4ee",
      eyebrow: "홈트레이닝",
      title: "요가매트부터 러닝화까지<br>움직이기 좋은 계절",
      text: "집에서도 밖에서도 쓰기 좋은 스포츠·레저 용품을 모았습니다.",
      href: "#/search?category=sports",
      image: "/static/assets/products/yogamat.jpg",
    },
    {
      bg: "#f3ede6",
      eyebrow: "커피 입문",
      title: "핸드드립 세트로<br>집에서 내리는 한 잔",
      text: "드리퍼, 서버, 텀블러까지 주방용품을 새로 들여 보세요.",
      href: "#/search?category=kitchen",
      image: "/static/assets/products/dripset.jpg",
    },
  ];
  const PAGE_SIZE = 20;
  const FREE_SHIPPING = 200_000;
  const KEYS = { cart: "faultmart.cart.v1", orders: "faultmart.orders.v1", postal: "faultmart.postal" };

  const app = document.getElementById("app");
  const won = new Intl.NumberFormat("ko-KR");
  let heroTimer = null;
  let renderToken = 0;

  // ---------- utilities ----------
  const esc = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
    );
  const icon = (name, cls = "") => `<svg class="${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const money = (n) => `${won.format(n)}<em>원</em>`;
  const categoryOf = (slug) => CATEGORIES.find((c) => c.slug === slug) || { ...ETC, slug: slug || "etc" };
  const discountPct = (p) =>
    p.list_price && p.list_price > p.unit_price
      ? Math.floor(((p.list_price - p.unit_price) / p.list_price) * 100)
      : 0;

  function quoteShipping(postal, quantity, merchandise) {
    let fee = merchandise >= FREE_SHIPPING ? 0 : 3_000;
    const digits = String(postal || "").replace(/\D/g, "");
    if (digits && Number(digits.slice(0, 2)) >= 60) fee += 2_500;
    if (quantity > 2) fee += (quantity - 2) * 700;
    return Math.max(fee, 0);
  }

  function load(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch {
      return fallback;
    }
  }

  function save(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* storage unavailable: keep in-memory behaviour */
    }
  }

  class ApiError extends Error {
    constructor(status, body) {
      super((body && body.message) || `HTTP ${status}`);
      this.status = status;
      this.code = (body && body.code) || "HTTP_ERROR";
      this.requestId = body && body.request_id;
    }
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      ...options,
    });
    let body = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    if (!response.ok) throw new ApiError(response.status, body);
    return body;
  }

  const listProducts = (params) => {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") query.set(k, v);
    });
    return api(`/products?${query}`);
  };

  function toast(message, { link, error } = {}) {
    const stack = document.querySelector("[data-toasts]");
    const node = document.createElement("div");
    node.className = `toast${error ? " error" : ""}`;
    node.innerHTML = `<span>${esc(message)}</span>${link ? `<a href="${esc(link.href)}">${esc(link.label)}</a>` : ""}`;
    stack.appendChild(node);
    setTimeout(() => node.remove(), 3200);
  }

  // ---------- cart ----------
  const cart = {
    items: load(KEYS.cart, []),
    persist() {
      save(KEYS.cart, this.items);
      updateCartBadge();
    },
    find(id) {
      return this.items.find((item) => item.id === id);
    },
    add(product, qty) {
      const stock = product.current_stock;
      const line = this.find(product.id);
      const nextQty = Math.min((line ? line.qty : 0) + qty, stock, 99);
      if (nextQty <= 0) return 0;
      if (line) {
        line.qty = nextQty;
        line.snap = snapshot(product);
      } else {
        this.items.unshift({ id: product.id, qty: nextQty, selected: true, snap: snapshot(product) });
      }
      this.persist();
      return nextQty;
    },
    remove(ids) {
      this.items = this.items.filter((item) => !ids.includes(item.id));
      this.persist();
    },
    count() {
      return this.items.length;
    },
  };

  function snapshot(p) {
    return {
      name: p.name,
      brand: p.brand,
      unit_price: p.unit_price,
      list_price: p.list_price,
      image_url: p.image_url,
      category: p.category,
      current_stock: p.current_stock,
    };
  }

  function updateCartBadge(bump = false) {
    const badge = document.querySelector("[data-cart-count]");
    const count = cart.count();
    badge.hidden = count === 0;
    badge.textContent = count > 99 ? "99+" : String(count);
    if (bump) {
      badge.classList.remove("bump");
      void badge.offsetWidth;
      badge.classList.add("bump");
    }
  }

  // ---------- shared fragments ----------
  function thumb(p, alt = "") {
    if (p.image_url) {
      return `<img src="${esc(p.image_url)}" alt="${esc(alt)}" loading="lazy" decoding="async">`;
    }
    const cat = categoryOf(p.category);
    return `<div class="noimg">${icon(cat.icon)}</div>`;
  }

  function priceBlock(p) {
    const pct = discountPct(p);
    return `
      ${pct ? `<div class="list-price">정가<s>${won.format(p.list_price)}원</s></div>` : `<div class="list-price" aria-hidden="true">&nbsp;</div>`}
      <div class="price-row">
        ${pct ? `<span class="discount">${pct}%</span>` : ""}
        <span class="price">${money(p.unit_price)}</span>
      </div>`;
  }

  function stockLine(p) {
    if (p.current_stock <= 0) return `<div class="stock-line out">일시품절</div>`;
    if (p.current_stock <= 5) return `<div class="stock-line low">품절임박 · ${p.current_stock}개 남음</div>`;
    return "";
  }

  function shipLine(p) {
    return p.unit_price >= FREE_SHIPPING
      ? `<div class="ship-line free">${icon("truck")}무료배송</div>`
      : `<div class="ship-line">${icon("truck")}배송비 3,000원</div>`;
  }

  function card(p, { rank } = {}) {
    const out = p.current_stock <= 0;
    return `
      <article class="card">
        <a class="card-link" href="#/product/${p.id}">
          <div class="card-thumb">
            ${thumb(p, p.name)}
            ${rank ? `<span class="card-rank">${rank}</span>` : ""}
            ${out ? `<div class="soldout-veil"><span>일시품절</span></div>` : ""}
          </div>
          <div class="card-body">
            ${p.brand ? `<span class="card-brand">${esc(p.brand)}</span>` : ""}
            <span class="card-name">${esc(p.name)}</span>
            ${priceBlock(p)}
            ${shipLine(p)}
            ${stockLine(p)}
          </div>
        </a>
        ${out ? "" : `<button class="card-add" type="button" data-quick-add="${p.id}" aria-label="${esc(p.name)} 장바구니 담기">${icon("cart")}</button>`}
      </article>`;
  }

  function skeletonGrid(count, cls = "grid") {
    return `<div class="${cls}">${Array.from({ length: count }, () => `
      <div><div class="skeleton sk-thumb"></div><div class="skeleton sk-line"></div><div class="skeleton sk-line short"></div></div>`).join("")}</div>`;
  }

  function errorBox(error) {
    return `
      <div class="error-box">
        <strong>상품 정보를 불러오지 못했습니다</strong>
        <p>${esc(error.message || "잠시 후 다시 시도해 주세요.")}</p>
        ${error.requestId ? `<code>request_id ${esc(error.requestId)}</code>` : ""}
        <button class="btn" type="button" data-retry>다시 시도</button>
      </div>`;
  }

  // Product payloads seen on the current screen, used by quick-add buttons.
  const productCache = new Map();
  const remember = (items) => items.forEach((p) => productCache.set(p.id, p));

  // ---------- pages ----------
  async function renderHome(token) {
    app.innerHTML = `
      ${heroMarkup()}
      <div class="wrap">
        <div class="service-strip">
          <div>${icon("check")}<p><b>실재고 주문</b><span>주문 즉시 PostgreSQL 재고가 차감됩니다</span></p></div>
          <div>${icon("truck")}<p><b>20만 원 이상 무료배송</b><span>미만은 상품별 3,000원</span></p></div>
          <div>${icon("cart")}<p><b>장바구니 한 번에 주문</b><span>상품마다 주문번호가 발급됩니다</span></p></div>
          <div>${icon("user")}<p><b>주문내역 보관</b><span>이 브라우저에서 주문한 내역을 보여 줍니다</span></p></div>
        </div>
        <section class="section">
          <div class="section-head"><h2>카테고리</h2></div>
          <div class="cat-tiles">
            ${CATEGORIES.map((c) => `
              <a class="cat-tile" href="#/search?category=${c.slug}">
                <span class="tile-img"><img src="/static/assets/products/${c.image}.jpg" alt="" loading="lazy"></span>
                ${esc(c.label)}
              </a>`).join("")}
          </div>
        </section>
        <section class="section deal-section" data-deals>
          <div class="section-head">
            <h2><span class="deal-badge">특가</span>오늘의 할인<small>정가 대비 할인율이 높은 상품</small></h2>
            <a class="more-link" href="#/search?sort=discount">전체보기${icon("chevron")}</a>
          </div>
          ${skeletonGrid(5)}
        </section>
        <div data-cat-sections>
          ${CATEGORIES.map((c) => `
            <section class="section" data-cat-section="${c.slug}">
              <div class="section-head">
                <h2>${esc(c.label)}</h2>
                <a class="more-link" href="#/search?category=${c.slug}">더보기${icon("chevron")}</a>
              </div>
              ${skeletonGrid(5)}
            </section>`).join("")}
        </div>
      </div>`;
    startHero();

    try {
      const [deals, ...sections] = await Promise.all([
        listProducts({ sort: "discount", limit: 10 }),
        ...CATEGORIES.map((c) => listProducts({ category: c.slug, limit: 5 })),
      ]);
      if (token !== renderToken) return;
      remember(deals.items);
      sections.forEach((s) => remember(s.items));

      const dealNode = app.querySelector("[data-deals]");
      const discounted = deals.items.filter((p) => discountPct(p) > 0 && p.current_stock > 0);
      if (deals.total === 0) {
        dealNode.innerHTML = `
          <div class="empty"><strong>아직 진열된 상품이 없습니다</strong>
          <span>카탈로그를 채우려면 <code>make seed</code>를 실행하세요.</span></div>`;
      } else if (discounted.length === 0) {
        dealNode.remove();
      } else {
        dealNode.querySelector(".grid").outerHTML =
          `<div class="grid">${discounted.slice(0, 5).map((p, i) => card(p, { rank: i + 1 })).join("")}</div>`;
      }

      CATEGORIES.forEach((c, index) => {
        const node = app.querySelector(`[data-cat-section="${c.slug}"]`);
        const items = sections[index].items;
        if (!items.length) {
          node.remove();
          return;
        }
        node.querySelector(".section-head h2").innerHTML =
          `${esc(c.label)}<small>${sections[index].total}개 상품</small>`;
        node.querySelector(".grid").outerHTML = `<div class="grid">${items.map((p) => card(p)).join("")}</div>`;
      });
    } catch (error) {
      if (token !== renderToken) return;
      app.querySelector("[data-deals]").innerHTML = errorBox(error);
      app.querySelector("[data-cat-sections]").innerHTML = "";
    }
  }

  function heroMarkup() {
    return `
      <section class="hero" aria-roledescription="carousel" aria-label="기획전" data-hero>
        <div class="hero-track">
          ${HERO_SLIDES.map((s, i) => `
            <div class="hero-slide${i === 0 ? " is-active" : ""}" style="background:${s.bg}" aria-hidden="${i !== 0}" data-slide>
              <div class="hero-copy">
                <span class="hero-eyebrow">${esc(s.eyebrow)}</span>
                <h2>${s.title}</h2>
                <p>${esc(s.text)}</p>
                <a class="hero-cta" href="${s.href}" ${i !== 0 ? 'tabindex="-1"' : ""}>보러 가기${icon("chevron")}</a>
              </div>
              <div class="hero-media"><img src="${s.image}" alt="" ${i === 0 ? "" : 'loading="lazy"'}></div>
            </div>`).join("")}
        </div>
        <div class="hero-controls">
          <button class="prev" type="button" aria-label="이전 기획전" data-hero-step="-1">${icon("chevron")}</button>
          <span class="hero-count" data-hero-count>1 / ${HERO_SLIDES.length}</span>
          <button type="button" aria-label="다음 기획전" data-hero-step="1">${icon("chevron")}</button>
        </div>
      </section>`;
  }

  let heroIndex = 0;
  function showSlide(index) {
    const slides = app.querySelectorAll("[data-slide]");
    if (!slides.length) return;
    heroIndex = (index + slides.length) % slides.length;
    slides.forEach((slide, i) => {
      const active = i === heroIndex;
      slide.classList.toggle("is-active", active);
      slide.setAttribute("aria-hidden", String(!active));
      slide.querySelector(".hero-cta").tabIndex = active ? 0 : -1;
    });
    const count = app.querySelector("[data-hero-count]");
    if (count) count.textContent = `${heroIndex + 1} / ${slides.length}`;
  }

  function startHero() {
    stopHero();
    heroIndex = 0;
    const hero = app.querySelector("[data-hero]");
    if (!hero) return;
    const tick = () => showSlide(heroIndex + 1);
    heroTimer = setInterval(tick, 5000);
    hero.addEventListener("mouseenter", stopHeroTimerOnly);
    hero.addEventListener("focusin", stopHeroTimerOnly);
    hero.addEventListener("mouseleave", () => {
      stopHeroTimerOnly();
      heroTimer = setInterval(tick, 5000);
    });
  }

  function stopHeroTimerOnly() {
    clearInterval(heroTimer);
    heroTimer = null;
  }

  function stopHero() {
    stopHeroTimerOnly();
  }

  async function renderListing(token, params) {
    const q = (params.get("q") || "").trim();
    const category = params.get("category") || "";
    const sort = SORTS.some(([key]) => key === params.get("sort")) ? params.get("sort") : "recommended";
    const page = Math.max(1, Number.parseInt(params.get("page") || "1", 10) || 1);
    const cat = category ? categoryOf(category) : null;
    const title = q ? `'${q}'` : cat ? cat.label : "전체 상품";

    document.querySelector("[data-search-input]").value = q;
    document.querySelector("[data-search-category]").value = category;

    const link = (overrides) => {
      const next = new URLSearchParams({ q, category, sort, page: "1", ...overrides });
      [...next.keys()].forEach((k) => {
        if (!next.get(k) || (k === "sort" && next.get(k) === "recommended") || (k === "page" && next.get(k) === "1")) next.delete(k);
      });
      return `#/search?${next}`;
    };

    app.innerHTML = `
      <div class="wrap">
        <nav class="crumbs" aria-label="현재 위치"><a href="#/">홈</a><span aria-hidden="true">›</span>
          ${cat ? `<a href="${link({ q: "", category })}">${esc(cat.label)}</a>` : `<span>전체</span>`}
          ${q ? `<span aria-hidden="true">›</span><span>${esc(q)}</span>` : ""}
        </nav>
        <div class="listing">
          <aside class="filters" aria-label="카테고리 필터">
            <h3>카테고리</h3>
            <ul data-filter-list>
              <li><a class="${!category ? "is-active" : ""}" href="${link({ category: "" })}">전체 <small data-count=""></small></a></li>
              ${[...CATEGORIES, ETC].map((c) => `
                <li><a class="${category === c.slug ? "is-active" : ""}" href="${link({ category: c.slug })}">${esc(c.label)} <small data-count="${c.slug}"></small></a></li>`).join("")}
            </ul>
          </aside>
          <section class="result-panel">
            <h1 class="result-title"><strong>${esc(title)}</strong>${q ? "에 대한 검색결과" : ""}<small data-total></small></h1>
            <nav class="sort-bar" aria-label="정렬">
              ${SORTS.map(([key, label]) => `<a class="${sort === key ? "is-active" : ""}" href="${link({ sort: key })}" ${sort === key ? 'aria-current="true"' : ""}>${label}</a>`).join("")}
            </nav>
            <div data-results>${skeletonGrid(8, "grid grid-4")}</div>
          </section>
        </div>
      </div>`;

    try {
      const [result, ...counts] = await Promise.all([
        listProducts({ q, category, sort, limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE }),
        listProducts({ q, limit: 1 }),
        ...[...CATEGORIES, ETC].map((c) => listProducts({ q, category: c.slug, limit: 1 })),
      ]);
      if (token !== renderToken) return;
      remember(result.items);

      const [all, ...perCategory] = counts;
      app.querySelector('[data-count=""]').textContent = all.total;
      [...CATEGORIES, ETC].forEach((c, i) => {
        const node = app.querySelector(`[data-count="${c.slug}"]`);
        node.textContent = perCategory[i].total;
        if (c === ETC && perCategory[i].total === 0 && category !== "etc") node.closest("li").remove();
      });
      app.querySelector("[data-total]").textContent = `${won.format(result.total)}개`;

      const results = app.querySelector("[data-results]");
      if (!result.items.length) {
        results.innerHTML = `
          <div class="empty"><strong>${q ? `'${esc(q)}'에 대한 검색결과가 없습니다` : "상품이 없습니다"}</strong>
          <span>단어의 철자를 확인하거나 다른 검색어를 입력해 보세요.</span></div>`;
        return;
      }
      const pages = Math.ceil(result.total / PAGE_SIZE);
      results.innerHTML = `
        <div class="grid grid-4">${result.items.map((p) => card(p)).join("")}</div>
        ${pages > 1 ? `<nav class="pager" aria-label="페이지">${Array.from({ length: pages }, (_, i) => i + 1)
          .map((n) => (n === page ? `<span class="is-current" aria-current="page">${n}</span>` : `<a href="${link({ page: String(n) })}">${n}</a>`))
          .join("")}</nav>` : ""}`;
    } catch (error) {
      if (token !== renderToken) return;
      app.querySelector("[data-results]").innerHTML = errorBox(error);
    }
  }

  async function renderProduct(token, id) {
    app.innerHTML = `
      <div class="wrap">
        <div class="crumbs"><a href="#/">홈</a></div>
        <div class="pdp"><div class="skeleton sk-thumb"></div><div><div class="skeleton sk-line"></div><div class="skeleton sk-line short"></div></div></div>
      </div>`;
    let p;
    try {
      p = await api(`/products/${encodeURIComponent(id)}`);
    } catch (error) {
      if (token !== renderToken) return;
      app.innerHTML = `<div class="wrap">${error.status === 404 ? `
        <div class="error-box"><strong>상품을 찾을 수 없습니다</strong><p>판매가 종료되었거나 잘못된 주소입니다.</p><a class="btn" href="#/">홈으로</a></div>` : errorBox(error)}</div>`;
      return;
    }
    if (token !== renderToken) return;
    remember([p]);
    document.title = `${p.name} — 폴트마켓`;

    const cat = categoryOf(p.category);
    const out = p.current_stock <= 0;
    const max = Math.max(1, Math.min(p.current_stock, 99));

    app.innerHTML = `
      <div class="wrap">
        <nav class="crumbs" aria-label="현재 위치">
          <a href="#/">홈</a><span aria-hidden="true">›</span>
          <a href="#/search?category=${esc(cat.slug)}">${esc(cat.label)}</a>
        </nav>
        <div class="pdp">
          <div class="pdp-media">
            ${thumb(p, p.name)}
            ${out ? `<div class="soldout-veil"><span>일시품절</span></div>` : ""}
          </div>
          <div class="pdp-info">
            ${p.brand ? `<a class="pdp-brand" href="#/search?q=${encodeURIComponent(p.brand)}">${esc(p.brand)} 브랜드관 ›</a>` : ""}
            <h1>${esc(p.name)}</h1>
            <span class="pdp-sku">상품번호 ${p.id}</span>
            <div class="pdp-price">${priceBlock(p)}</div>
            <dl class="pdp-rows">
              <div><dt>배송비</dt><dd>${p.unit_price >= FREE_SHIPPING ? `<span class="green">무료배송</span>` : `3,000원`}
                <small>${p.unit_price >= FREE_SHIPPING ? "상품 금액 20만 원 이상" : "한 주문 상품 금액 20만 원 이상 무료배송"} · 우편번호 60~99 지역 +2,500원 · 3개부터 개당 +700원</small></dd></div>
              <div><dt>재고</dt><dd>${out ? "일시품절" : p.current_stock <= 5 ? `<span class="warn-text">품절임박 · ${p.current_stock}개 남음</span>` : `구매 가능 (${won.format(p.current_stock)}개)`}</dd></div>
              <div><dt>판매자</dt><dd>폴트마켓 직배송</dd></div>
            </dl>
            <div class="pdp-buy">
              <div class="qty" data-qty-box>
                <button type="button" data-step="-1" aria-label="수량 줄이기" ${out ? "disabled" : ""}>−</button>
                <input type="number" inputmode="numeric" min="1" max="${max}" value="1" aria-label="수량" data-qty-input ${out ? "disabled" : ""}>
                <button type="button" data-step="1" aria-label="수량 늘리기" ${out || max <= 1 ? "disabled" : ""}>+</button>
              </div>
              <button class="btn" type="button" data-add-cart ${out ? "disabled" : ""}>${icon("cart")}장바구니 담기</button>
              <button class="btn btn-primary" type="button" data-buy-now ${out ? "disabled" : ""}>${out ? "일시품절" : "바로구매 ›"}</button>
            </div>
            <p class="pdp-note">주문하면 실제 재고가 즉시 차감되고 주문번호가 발급됩니다. 결제는 진행되지 않습니다.</p>
          </div>
        </div>

        <div class="detail-tabs">
          <nav class="tab-bar">
            <a href="#/product/${p.id}" data-tab="tab-detail" class="is-active">상품상세</a>
            <a href="#/product/${p.id}" data-tab="tab-ship">배송/교환/반품</a>
            <a href="#/product/${p.id}" data-tab="tab-related">함께 보면 좋은 상품</a>
          </nav>
          <section class="tab-body" id="tab-detail">
            <h3>상품 설명</h3>
            <p class="desc-text">${esc(p.description || "등록된 상품 설명이 없습니다.")}</p>
            <table class="spec">
              <tr><th>상품명</th><td>${esc(p.name)}</td></tr>
              <tr><th>브랜드</th><td>${esc(p.brand || "-")}</td></tr>
              <tr><th>카테고리</th><td>${esc(cat.label)}</td></tr>
              <tr><th>판매가</th><td>${won.format(p.unit_price)}원${p.list_price ? ` (정가 ${won.format(p.list_price)}원)` : ""}</td></tr>
              <tr><th>상품번호</th><td>${p.id}</td></tr>
            </table>
          </section>
          <section class="tab-body" id="tab-ship">
            <h3>배송 정보</h3>
            <table class="spec ship-table">
              <tr><th>기본 배송비</th><td>주문 상품 금액 20만 원 미만 <b>3,000원</b>, 20만 원 이상 <b>무료</b></td></tr>
              <tr><th>지역 추가 배송비</th><td>우편번호 앞 두 자리가 60~99이면 <b>+2,500원</b></td></tr>
              <tr><th>포장 추가 비용</th><td>한 주문에 3개 이상 담으면 2개를 넘는 1개마다 <b>+700원</b></td></tr>
              <tr><th>교환·반품</th><td>이 데모 스토어는 주문 확정(CONFIRMED)만 지원하며 취소·반품 기능은 없습니다.</td></tr>
            </table>
          </section>
          <section class="tab-body" id="tab-related">
            <h3>${esc(cat.label)} 다른 상품</h3>
            <div data-related>${skeletonGrid(5)}</div>
          </section>
        </div>
      </div>`;

    const input = app.querySelector("[data-qty-input]");
    const box = app.querySelector("[data-qty-box]");
    const clamp = () => {
      const value = Math.min(max, Math.max(1, Number.parseInt(input.value, 10) || 1));
      input.value = value;
      box.querySelector('[data-step="-1"]').disabled = out || value <= 1;
      box.querySelector('[data-step="1"]').disabled = out || value >= max;
      return value;
    };
    if (!out) clamp();
    box.addEventListener("click", (event) => {
      const step = event.target.closest("[data-step]");
      if (!step) return;
      input.value = (Number.parseInt(input.value, 10) || 1) + Number(step.dataset.step);
      clamp();
    });
    input.addEventListener("change", clamp);

    app.querySelector("[data-add-cart]").addEventListener("click", () => addToCart(p, clamp()));
    app.querySelector("[data-buy-now]").addEventListener("click", () => {
      if (addToCart(p, clamp(), { silent: true })) {
        cart.items.forEach((item) => (item.selected = item.id === p.id));
        cart.persist();
        location.hash = "#/checkout";
      }
    });

    app.querySelector(".tab-bar").addEventListener("click", (event) => {
      const tab = event.target.closest("[data-tab]");
      if (!tab) return;
      event.preventDefault();
      app.querySelectorAll("[data-tab]").forEach((t) => t.classList.toggle("is-active", t === tab));
      document.getElementById(tab.dataset.tab).scrollIntoView({ behavior: "smooth" });
    });

    try {
      const related = await listProducts({ category: p.category, limit: 6 });
      if (token !== renderToken) return;
      const items = related.items.filter((item) => item.id !== p.id).slice(0, 5);
      remember(items);
      app.querySelector("[data-related]").innerHTML = items.length
        ? `<div class="grid">${items.map((item) => card(item)).join("")}</div>`
        : `<p class="desc-text">같은 카테고리의 다른 상품이 없습니다.</p>`;
    } catch {
      app.querySelector("[data-related]").innerHTML = "";
    }
  }

  function addToCart(product, qty, { silent = false } = {}) {
    const existing = cart.find(product.id);
    const before = existing ? existing.qty : 0;
    const after = cart.add(product, qty);
    if (after === 0) {
      toast("재고가 없어 담을 수 없습니다.", { error: true });
      return false;
    }
    if (!silent) {
      if (after < before + qty) {
        toast(`재고가 ${product.current_stock}개라 ${after}개까지만 담았습니다.`, { link: { href: "#/cart", label: "장바구니 보기" } });
      } else {
        toast("장바구니에 상품을 담았습니다.", { link: { href: "#/cart", label: "장바구니 보기" } });
      }
    }
    updateCartBadge(true);
    return true;
  }

  async function refreshCartProducts() {
    const results = await Promise.allSettled(cart.items.map((item) => api(`/products/${item.id}`)));
    const gone = [];
    results.forEach((result, index) => {
      const item = cart.items[index];
      if (result.status === "fulfilled") {
        item.snap = snapshot(result.value);
        if (item.snap.current_stock <= 0) item.selected = false;
      } else if (result.reason && result.reason.status === 404) {
        gone.push(item.id);
      }
    });
    if (gone.length) cart.remove(gone);
    else cart.persist();
    return gone.length;
  }

  async function renderCart(token) {
    app.innerHTML = `<div class="wrap"><h1 class="page-title">장바구니</h1>${skeletonGrid(1, "")}</div>`;
    let removed = 0;
    try {
      removed = await refreshCartProducts();
    } catch {
      /* show cached snapshots */
    }
    if (token !== renderToken) return;
    drawCart();
    if (removed) toast(`판매가 종료된 상품 ${removed}개를 장바구니에서 뺐습니다.`);
  }

  function lineIssue(item) {
    if (item.snap.current_stock <= 0) return "일시품절";
    if (item.qty > item.snap.current_stock) return `재고 ${item.snap.current_stock}개만 남았습니다`;
    return "";
  }

  function drawCart() {
    const postal = load(KEYS.postal, "");
    if (!cart.items.length) {
      app.innerHTML = `
        <div class="wrap"><h1 class="page-title">장바구니</h1>
          <div class="cart-box"><div class="empty"><strong>장바구니에 담은 상품이 없습니다</strong>
          <span>마음에 드는 상품을 담아 보세요.</span><a class="btn btn-primary" href="#/">쇼핑 계속하기</a></div></div>
        </div>`;
      return;
    }
    const selected = cart.items.filter((item) => item.selected && !lineIssue(item));
    const merchandise = selected.reduce((sum, item) => sum + item.snap.unit_price * item.qty, 0);
    const shipping = selected.reduce((sum, item) => sum + quoteShipping(postal, item.qty, item.snap.unit_price * item.qty), 0);
    const listTotal = selected.reduce((sum, item) => sum + (item.snap.list_price || item.snap.unit_price) * item.qty, 0);
    const selectable = cart.items.filter((item) => !lineIssue(item));
    const allChecked = selectable.length > 0 && selectable.every((item) => item.selected);

    app.innerHTML = `
      <div class="wrap">
        <h1 class="page-title">장바구니</h1>
        <div class="steps"><b>01 장바구니</b><span>›</span><span>02 주문/결제</span><span>›</span><span>03 주문완료</span></div>
        <div class="cart-layout">
          <section class="cart-box" aria-label="담은 상품">
            <div class="cart-toolbar">
              <label class="check"><input type="checkbox" data-check-all ${allChecked ? "checked" : ""}> 전체선택 (${selected.length}/${cart.items.length})</label>
              <button class="link-btn" type="button" data-remove-selected>선택삭제</button>
            </div>
            ${cart.items.map((item) => {
              const issue = lineIssue(item);
              const max = Math.max(1, Math.min(item.snap.current_stock, 99));
              return `
              <div class="cart-line${item.snap.current_stock <= 0 ? " is-out" : ""}" data-line="${item.id}">
                <label class="check"><input type="checkbox" data-check="${item.id}" ${item.selected && !issue ? "checked" : ""} ${item.snap.current_stock <= 0 ? "disabled" : ""} aria-label="${esc(item.snap.name)} 선택"></label>
                <a class="cart-thumb" href="#/product/${item.id}">${thumb(item.snap)}</a>
                <div class="cart-info">
                  <a href="#/product/${item.id}">${esc(item.snap.name)}</a>
                  <div class="meta">${won.format(item.snap.unit_price)}원 · 배송비 ${won.format(quoteShipping(postal, item.qty, item.snap.unit_price * item.qty))}원</div>
                  ${issue ? `<div class="meta warn">${esc(issue)}</div>` : ""}
                </div>
                <div class="qty qty-sm">
                  <button type="button" data-line-step="-1" aria-label="수량 줄이기" ${item.qty <= 1 ? "disabled" : ""}>−</button>
                  <input type="number" min="1" max="${max}" value="${item.qty}" aria-label="수량" data-line-qty>
                  <button type="button" data-line-step="1" aria-label="수량 늘리기" ${item.qty >= max ? "disabled" : ""}>+</button>
                </div>
                <div class="line-total">${won.format(item.snap.unit_price * item.qty)}원</div>
                <button class="line-remove" type="button" data-remove="${item.id}" aria-label="${esc(item.snap.name)} 삭제">${icon("close")}</button>
              </div>`;
            }).join("")}
          </section>
          <aside class="summary" aria-label="결제 예정 금액">
            <h2>결제 예정 금액</h2>
            <dl>
              <div><dt>상품 정가</dt><dd>${won.format(listTotal)}원</dd></div>
              <div><dt>할인 금액</dt><dd style="color:var(--sale)">−${won.format(listTotal - merchandise)}원</dd></div>
              <div><dt>배송비</dt><dd>+${won.format(shipping)}원</dd></div>
              <div class="total"><dt>총 결제금액</dt><dd>${won.format(merchandise + shipping)}원</dd></div>
            </dl>
            <button class="btn btn-primary btn-block" type="button" data-go-checkout ${selected.length ? "" : "disabled"}>구매하기 (${selected.length})</button>
            <p class="summary-note">상품마다 별도 주문으로 접수되어 배송비도 상품별로 계산됩니다.${postal ? "" : " 지역 추가 배송비는 주문 단계에서 우편번호로 확정됩니다."}</p>
          </aside>
        </div>
      </div>`;
  }

  function renderCheckout() {
    const lines = cart.items.filter((item) => item.selected && !lineIssue(item));
    if (!lines.length) {
      location.replace("#/cart");
      return;
    }
    const postal = load(KEYS.postal, "");
    app.innerHTML = `
      <div class="wrap">
        <h1 class="page-title">주문/결제</h1>
        <div class="steps"><span>01 장바구니</span><span>›</span><b>02 주문/결제</b><span>›</span><span>03 주문완료</span></div>
        <form class="cart-layout" data-checkout-form novalidate>
          <div>
            <section class="form-box">
              <h2>배송지</h2>
              <div class="field">
                <label for="postal">우편번호</label>
                <input id="postal" name="postal" inputmode="numeric" maxlength="5" placeholder="5자리 숫자" value="${esc(postal)}" autocomplete="postal-code" data-postal required>
                <span class="field-hint">우편번호 앞 두 자리가 60~99이면 지역 추가 배송비 2,500원이 붙습니다.</span>
                <span class="field-error" data-postal-error hidden>우편번호 5자리를 입력해 주세요.</span>
              </div>
              <div class="addr-presets" aria-label="예시 우편번호">
                <button type="button" data-preset="06236">서울 강남 06236</button>
                <button type="button" data-preset="16841">경기 용인 16841</button>
                <button type="button" data-preset="48058">부산 해운대 48058</button>
                <button type="button" data-preset="63309">제주 63309</button>
              </div>
            </section>
            <section class="form-box">
              <h2>주문 상품 ${lines.length}개</h2>
              <div data-co-lines></div>
            </section>
          </div>
          <aside class="summary" aria-label="최종 결제 금액">
            <h2>최종 결제 금액</h2>
            <dl data-co-summary></dl>
            <button class="btn btn-primary btn-block" type="submit" data-pay>주문하기</button>
            <p class="summary-note">주문하기를 누르면 상품마다 <code>POST /orders</code>가 호출되고 실제 재고가 차감됩니다. 결제는 진행되지 않습니다.</p>
          </aside>
        </form>
      </div>`;

    const input = app.querySelector("[data-postal]");
    const drawLines = (states = {}) => {
      const code = input.value.trim();
      app.querySelector("[data-co-lines]").innerHTML = lines.map((item) => {
        const merch = item.snap.unit_price * item.qty;
        const fee = quoteShipping(code, item.qty, merch);
        const state = states[item.id];
        return `
          <div class="co-line">
            <div class="cart-thumb">${thumb(item.snap)}</div>
            <div>
              <div class="name">${esc(item.snap.name)}</div>
              <div class="meta">${item.qty}개 · ${won.format(item.snap.unit_price)}원</div>
              ${state ? `<div class="state ${state.kind}">${state.kind === "ok" ? icon("check") : ""}${esc(state.text)}</div>` : ""}
            </div>
            <div class="amt">${won.format(merch + fee)}원<small>배송비 ${won.format(fee)}원 포함</small></div>
          </div>`;
      }).join("");
      const merchandise = lines.reduce((s, item) => s + item.snap.unit_price * item.qty, 0);
      const shipping = lines.reduce((s, item) => s + quoteShipping(code, item.qty, item.snap.unit_price * item.qty), 0);
      app.querySelector("[data-co-summary]").innerHTML = `
        <div><dt>상품금액</dt><dd>${won.format(merchandise)}원</dd></div>
        <div><dt>배송비</dt><dd>+${won.format(shipping)}원</dd></div>
        <div class="total"><dt>총 결제금액</dt><dd>${won.format(merchandise + shipping)}원</dd></div>`;
      app.querySelector("[data-pay]").textContent = `${won.format(merchandise + shipping)}원 주문하기`;
    };
    drawLines();

    input.addEventListener("input", () => {
      input.value = input.value.replace(/\D/g, "").slice(0, 5);
      app.querySelector("[data-postal-error]").hidden = true;
      drawLines();
    });
    app.querySelector(".addr-presets").addEventListener("click", (event) => {
      const preset = event.target.closest("[data-preset]");
      if (!preset) return;
      input.value = preset.dataset.preset;
      app.querySelector("[data-postal-error]").hidden = true;
      drawLines();
    });

    app.querySelector("[data-checkout-form]").addEventListener("submit", async (event) => {
      event.preventDefault();
      const code = input.value.trim();
      if (!/^\d{5}$/.test(code)) {
        app.querySelector("[data-postal-error]").hidden = false;
        input.focus();
        return;
      }
      save(KEYS.postal, code);
      const pay = app.querySelector("[data-pay]");
      pay.disabled = true;
      input.disabled = true;
      const states = {};
      const placed = [];
      const failed = [];
      for (const item of lines) {
        states[item.id] = { kind: "pending", text: "주문 접수 중…" };
        drawLines(states);
        try {
          const order = await api("/orders", {
            method: "POST",
            body: JSON.stringify({ product_id: item.id, quantity: item.qty, postal_code: code }),
          });
          placed.push({ ...order, name: item.snap.name, image_url: item.snap.image_url, category: item.snap.category });
          states[item.id] = { kind: "ok", text: `주문번호 ${order.id} 접수 완료` };
        } catch (error) {
          failed.push({ product_id: item.id, quantity: item.qty, name: item.snap.name, image_url: item.snap.image_url, code: error.code, message: error.message });
          states[item.id] = { kind: "fail", text: error.code === "INSUFFICIENT_STOCK" ? "재고가 부족해 주문하지 못했습니다" : error.message };
        }
        drawLines(states);
      }

      cart.remove(placed.map((order) => order.product_id));
      const record = { id: Date.now(), created_at: new Date().toISOString(), postal_code: code, orders: placed, failures: failed };
      if (placed.length) {
        const history = load(KEYS.orders, []);
        history.unshift(record);
        save(KEYS.orders, history.slice(0, 50));
      }
      try {
        sessionStorage.setItem("faultmart.lastCheckout", JSON.stringify(record));
      } catch {
        /* ignore */
      }
      location.hash = "#/complete";
    });
  }

  function orderLines(orders) {
    return orders.map((order) => `
      <div class="co-line">
        <a class="cart-thumb" href="#/product/${order.product_id}">${thumb(order)}</a>
        <div>
          <div class="name">${esc(order.name)}</div>
          <div class="meta">주문번호 ${order.id} · ${order.quantity}개 · ${won.format(order.unit_price)}원</div>
        </div>
        <div class="amt">${won.format(order.total_amount)}원<small>배송비 ${won.format(order.shipping_fee)}원</small></div>
      </div>`).join("");
  }

  function renderComplete() {
    let record = null;
    try {
      record = JSON.parse(sessionStorage.getItem("faultmart.lastCheckout") || "null");
    } catch {
      record = null;
    }
    if (!record) {
      location.replace("#/orders");
      return;
    }
    const ok = record.orders.length;
    const total = record.orders.reduce((s, o) => s + o.total_amount, 0);
    const title = !ok ? "주문하지 못했습니다" : record.failures.length ? "일부 상품만 주문되었습니다" : "주문이 완료되었습니다";
    app.innerHTML = `
      <div class="wrap">
        <div class="steps" style="margin-top:26px"><span>01 장바구니</span><span>›</span><span>02 주문/결제</span><span>›</span><b>03 주문완료</b></div>
        <section class="done-hero">
          <span class="done-icon${record.failures.length ? " partial" : ""}">${icon(record.failures.length ? "close" : "check")}</span>
          <h1>${title}</h1>
          <p>${ok ? `주문 ${ok}건 · 총 ${won.format(total)}원 · 배송지 우편번호 ${esc(record.postal_code)}` : "재고가 부족한 상품이 있어 주문이 접수되지 않았습니다."}</p>
          <div class="done-actions">
            <a class="btn" href="#/orders">주문내역 보기</a>
            <a class="btn btn-primary" href="#/">쇼핑 계속하기</a>
          </div>
        </section>
        ${ok ? `<section class="form-box" style="margin-top:12px"><h2>접수된 주문</h2>${orderLines(record.orders)}</section>` : ""}
        ${record.failures.length ? `
          <section class="form-box"><h2>주문하지 못한 상품</h2>
            ${record.failures.map((f) => `
              <div class="co-line">
                <a class="cart-thumb" href="#/product/${f.product_id}">${thumb(f)}</a>
                <div><div class="name">${esc(f.name)}</div><div class="state fail">${esc(f.code === "INSUFFICIENT_STOCK" ? "재고 부족" : f.message)}</div></div>
                <div class="amt">${f.quantity}개</div>
              </div>`).join("")}
            <div class="note-box">주문하지 못한 상품은 장바구니에 그대로 남아 있습니다.</div>
          </section>` : ""}
      </div>`;
  }

  function renderOrders() {
    const history = load(KEYS.orders, []);
    app.innerHTML = `
      <div class="wrap">
        <h1 class="page-title">주문내역</h1>
        ${history.length ? history.map((record) => `
          <article class="order-card">
            <header>
              <div><b>${new Date(record.created_at).toLocaleString("ko-KR", { dateStyle: "medium", timeStyle: "short" })}</b> 주문</div>
              <div><span class="badge">주문확정</span></div>
            </header>
            ${orderLines(record.orders)}
          </article>`).join("") + `<div class="note-box">이 브라우저에서 주문한 내역만 표시됩니다. 주문 자체는 서버 DB에 기록되어 있습니다.</div>` : `
          <div class="cart-box"><div class="empty"><strong>주문한 내역이 없습니다</strong>
          <span>첫 주문을 해 보세요.</span><a class="btn btn-primary" href="#/">쇼핑하러 가기</a></div></div>`}
      </div>`;
  }

  // ---------- router ----------
  function route() {
    const token = ++renderToken;
    stopHero();
    closeCatPanel();
    const hash = location.hash.replace(/^#/, "") || "/";
    const [path, query = ""] = hash.split("?");
    const params = new URLSearchParams(query);
    const parts = path.split("/").filter(Boolean);

    document.title = "폴트마켓 — Fault Commerce Lab";
    if (parts[0] !== "search") {
      document.querySelector("[data-search-input]").value = "";
      document.querySelector("[data-search-category]").value = "";
    }
    const activeCat = parts[0] === "search" ? params.get("category") : null;
    document.querySelectorAll("[data-quick-nav] a").forEach((a) => {
      a.classList.toggle("is-active", Boolean(activeCat) && a.dataset.cat === activeCat);
    });

    switch (parts[0]) {
      case undefined:
        renderHome(token);
        break;
      case "search":
        renderListing(token, params);
        break;
      case "product":
        renderProduct(token, parts[1]);
        break;
      case "cart":
        renderCart(token);
        break;
      case "checkout":
        renderCheckout();
        break;
      case "complete":
        renderComplete();
        break;
      case "orders":
        renderOrders();
        break;
      default:
        app.innerHTML = `<div class="wrap"><div class="error-box"><strong>페이지를 찾을 수 없습니다</strong><a class="btn" href="#/">홈으로</a></div></div>`;
    }
    window.scrollTo(0, 0);
  }

  // ---------- global wiring ----------
  function closeCatPanel() {
    const panel = document.querySelector("[data-cat-panel]");
    const toggle = document.querySelector("[data-cat-toggle]");
    panel.hidden = true;
    toggle.setAttribute("aria-expanded", "false");
  }

  function setupChrome() {
    const catLinks = CATEGORIES.map((c) => `<a href="#/search?category=${c.slug}" data-cat="${c.slug}">${icon(c.icon)}${esc(c.label)}</a>`).join("");
    document.querySelector("[data-cat-panel]").innerHTML = catLinks;
    document.querySelector("[data-quick-nav]").innerHTML =
      `<a class="is-deal" href="#/search?sort=discount">${icon("check")}오늘의 할인</a>${catLinks}<a href="#/search?sort=newest">${icon("etc")}신상품</a>`;
    document.querySelector("[data-search-category]").insertAdjacentHTML(
      "beforeend",
      CATEGORIES.map((c) => `<option value="${c.slug}">${esc(c.label)}</option>`).join(""),
    );

    const toggle = document.querySelector("[data-cat-toggle]");
    const panel = document.querySelector("[data-cat-panel]");
    toggle.addEventListener("click", (event) => {
      event.stopPropagation();
      const open = panel.hidden;
      panel.hidden = !open;
      toggle.setAttribute("aria-expanded", String(open));
    });
    document.addEventListener("click", (event) => {
      if (!event.target.closest("[data-cat-menu]")) closeCatPanel();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeCatPanel();
    });

    document.querySelector("[data-search-form]").addEventListener("submit", (event) => {
      event.preventDefault();
      const q = document.querySelector("[data-search-input]").value.trim();
      const category = document.querySelector("[data-search-category]").value;
      const params = new URLSearchParams();
      if (q) params.set("q", q);
      if (category) params.set("category", category);
      location.hash = `#/search?${params}`;
    });

    const header = document.querySelector("[data-header]");
    window.addEventListener("scroll", () => header.classList.toggle("is-scrolled", window.scrollY > 8), { passive: true });

    app.addEventListener("click", (event) => {
      const quick = event.target.closest("[data-quick-add]");
      if (quick) {
        event.preventDefault();
        const product = productCache.get(Number(quick.dataset.quickAdd));
        if (product) addToCart(product, 1);
        return;
      }
      const step = event.target.closest("[data-hero-step]");
      if (step) {
        stopHeroTimerOnly();
        showSlide(heroIndex + Number(step.dataset.heroStep));
        return;
      }
      if (event.target.closest("[data-retry]")) {
        route();
        return;
      }
      handleCartClick(event);
    });

    app.addEventListener("change", (event) => {
      const check = event.target.closest("[data-check]");
      if (check) {
        const item = cart.find(Number(check.dataset.check));
        if (item) item.selected = check.checked;
        cart.persist();
        drawCart();
        return;
      }
      if (event.target.closest("[data-check-all]")) {
        const on = event.target.checked;
        cart.items.forEach((item) => (item.selected = on && !lineIssue(item)));
        cart.persist();
        drawCart();
        return;
      }
      const qtyInput = event.target.closest("[data-line-qty]");
      if (qtyInput) setLineQty(Number(qtyInput.closest("[data-line]").dataset.line), Number.parseInt(qtyInput.value, 10) || 1);
    });

    window.addEventListener("hashchange", route);
    window.addEventListener("storage", (event) => {
      if (event.key === KEYS.cart) {
        cart.items = load(KEYS.cart, []);
        updateCartBadge();
      }
    });
  }

  function setLineQty(id, qty) {
    const item = cart.find(id);
    if (!item) return;
    item.qty = Math.min(Math.max(1, qty), Math.max(1, Math.min(item.snap.current_stock, 99)));
    if (!lineIssue(item)) item.selected = item.selected !== false;
    cart.persist();
    drawCart();
  }

  function handleCartClick(event) {
    const remove = event.target.closest("[data-remove]");
    if (remove) {
      cart.remove([Number(remove.dataset.remove)]);
      drawCart();
      return;
    }
    if (event.target.closest("[data-remove-selected]")) {
      const ids = cart.items.filter((item) => item.selected).map((item) => item.id);
      if (!ids.length) {
        toast("삭제할 상품을 선택해 주세요.");
        return;
      }
      cart.remove(ids);
      drawCart();
      return;
    }
    const lineStep = event.target.closest("[data-line-step]");
    if (lineStep) {
      const id = Number(lineStep.closest("[data-line]").dataset.line);
      const item = cart.find(id);
      if (item) setLineQty(id, item.qty + Number(lineStep.dataset.lineStep));
      return;
    }
    if (event.target.closest("[data-go-checkout]")) {
      location.hash = "#/checkout";
    }
  }

  setupChrome();
  updateCartBadge();
  route();
})();

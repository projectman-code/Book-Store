'use strict';
/* Pagewise book store: one-page app with hash routes (#/book/3).
   Rule: every piece of text that comes from the server or a user goes through esc() before it
   is put into HTML. That is what stops XSS. */

const state = { user: null, config: {}, cartCount: 0, wish: new Set(), cats: null, coupon: null, redirect: '', manageBooks: {} };
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const rs = n => '\u20B9' + Number(n || 0).toLocaleString('en-IN');
const isStaff = () => !!state.user && state.user.role !== 'customer';
const canShop = () => !state.user || state.user.role === 'customer';

/* ---------- icons ---------- */
const ICONS = {
  home: '<path d="M3 11l9-8 9 8"/><path d="M5 10v10h5v-6h4v6h5V10"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
  cart: '<path d="M3 4h2l2.4 10.2a2 2 0 002 1.5h7.7a2 2 0 002-1.5L21 8H6"/><circle cx="10" cy="20" r="1.4"/><circle cx="17" cy="20" r="1.4"/>',
  orders: '<path d="M21 8l-9-5-9 5 9 5 9-5z"/><path d="M3 8v8l9 5 9-5V8"/><path d="M12 13v8"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1-4 4-6 8-6s7 2 8 6"/>',
  heart: '<path d="M12 21s-8-5.2-8-11a4.6 4.6 0 018-3 4.6 4.6 0 018 3c0 5.8-8 11-8 11z"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.01"/>',
  alert: '<path d="M12 3l10 18H2L12 3z"/><path d="M12 10v5M12 18v.01"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  minus: '<path d="M5 12h14"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
  book: '<path d="M4 5a2 2 0 012-2h13v16H6a2 2 0 00-2 2V5z"/><path d="M4 19a2 2 0 012-2h13"/>',
  manage: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  filter: '<path d="M4 6h16M7 12h10M10 18h4"/>',
  back: '<path d="M15 5l-7 7 7 7"/>',
  receipt: '<path d="M6 3h12v18l-3-2-3 2-3-2-3 2V3z"/><path d="M9 8h6M9 12h6"/>',
  logout: '<path d="M15 4h3a2 2 0 012 2v12a2 2 0 01-2 2h-3"/><path d="M10 8l-4 4 4 4M6 12h10"/>',
  cash: '<rect x="2.5" y="6" width="19" height="12" rx="2"/><circle cx="12" cy="12" r="2.6"/>'
};
const icon = (n, cls) => `<svg class="i ${cls || ''}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[n]}</svg>`;

/* ---------- toast messages ---------- */
function toast(message, o = {}) {
  const type = o.type || 'info';
  const ms = o.ms || (type === 'error' ? 6000 : 4200);
  const box = $('#toasts');
  while (box.children.length >= 2) box.firstElementChild.remove();
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.setAttribute('role', type === 'error' ? 'alert' : 'status');
  el.style.setProperty('--ms', ms + 'ms');
  const ico = { success: 'check', error: 'x', warn: 'alert', info: 'info' }[type];
  el.innerHTML = `<div class="t-ico">${icon(ico)}</div><div class="t-body"><b>${esc(o.title || message)}</b>${o.title ? `<span>${esc(message)}</span>` : ''}</div>` +
    (o.action ? `<button class="t-act" type="button">${esc(o.action.label)}</button>` : '') +
    `<button class="t-x" type="button" aria-label="Dismiss">${icon('x')}</button><i class="t-bar"></i>`;
  let remaining = ms, started = Date.now(), timer;
  const close = () => { clearTimeout(timer); el.classList.add('out'); setTimeout(() => el.remove(), 200); };
  const start = () => { started = Date.now(); timer = setTimeout(close, remaining); };
  el.addEventListener('mouseenter', () => { clearTimeout(timer); remaining -= Date.now() - started; });
  el.addEventListener('mouseleave', start);
  $('.t-x', el).addEventListener('click', close);
  if (o.action) $('.t-act', el).addEventListener('click', () => { close(); o.action.onClick(); });
  box.appendChild(el);
  start();
}
const fail = (e, title) => toast(e && e.message ? e.message : 'Something went wrong.', { type: 'error', title: title || undefined });

/* ---------- talking to the server ---------- */
class ApiErr extends Error { constructor(m, status) { super(m); this.status = status; } }
async function api(path, { method = 'GET', data } = {}) {
  const opts = { method, headers: {}, credentials: 'same-origin' };
  if (method !== 'GET') { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(data || {}); }
  let res;
  try { res = await fetch(path, opts); } catch (e) { throw new ApiErr('No internet connection. Check your network and try again.', 0); }
  let json = {};
  try { json = await res.json(); } catch (e) { /* not JSON */ }
  if (!res.ok) {
    if (res.status === 401 && state.user) { state.user = null; renderChrome(); }
    throw new ApiErr(json.error || 'Something went wrong. Please try again.', res.status);
  }
  return json;
}
async function busy(btn, fn) {
  if (btn) { btn.disabled = true; btn.dataset.label = btn.innerHTML; btn.textContent = 'Please wait...'; }
  try { return await fn(); } finally { if (btn && btn.isConnected) { btn.disabled = false; btn.innerHTML = btn.dataset.label; } }
}

/* ---------- small helpers ---------- */
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
function fmt(s, withTime = true) {
  if (!s) return '';
  const [d, t = '00:00:00'] = s.split(' ');
  const [y, m, dd] = d.split('-').map(Number);
  const [hh, mm] = t.split(':').map(Number);
  let out = `${dd} ${MONTHS[m - 1]}`;
  if (y !== new Date().getFullYear()) out += ` ${y}`;
  if (withTime) out += `, ${hh % 12 || 12}:${String(mm).padStart(2, '0')} ${hh < 12 ? 'am' : 'pm'}`;
  return out;
}
const STATUS_LABEL = { placed: 'Order placed', packed: 'Packed', shipped: 'Shipped', delivered: 'Delivered', cancelled: 'Cancelled', refused: 'Returned to store', returned: 'Returned', requested: 'Requested', approved: 'Approved', rejected: 'Rejected' };
const statusChip = s => `<span class="status ${esc(s)}">${esc(STATUS_LABEL[s] || s)}</span>`;
function payText(o) {
  return { pending: `Pay ${rs(o.total)} in cash when it arrives`, paid: 'Paid in cash on delivery', not_applicable: 'No payment needed', refund_pending: 'Refund pending. The store will pay you back by hand.' }[o.payment_status] || '';
}
const go = hash => { if (location.hash === hash) render(true); else location.hash = hash; };
async function getCats() {
  if (!state.cats) state.cats = (await api('/api/categories')).categories;
  return state.cats;
}
function setCart(n) {
  state.cartCount = n;
  $$('[data-cartcount]').forEach(b => { b.dataset.n = n; b.textContent = n; });
}
function needLogin(msg) {
  state.redirect = location.hash || '#/';
  toast(msg, { type: 'info', title: 'Log in first', action: { label: 'Log in', onClick: () => { location.hash = '#/login'; } } });
}

/* ---------- covers (made from the title, so no image files are needed) ---------- */
const PALETTE = [['#1d2b64', '#f5a524'], ['#0e5e5e', '#fde9b8'], ['#4a1d4f', '#f7c6e0'], ['#1f4d36', '#e8f2b8'], ['#f2a58b', '#2a1210'],
  ['#cfe0f5', '#14305b'], ['#f5c04a', '#2b2208'], ['#2b2f3a', '#f3f3f3'], ['#7a2e2e', '#ffe3c2'], ['#33407a', '#c9d6ff']];
function genCover(id, title, author) {
  const [bg, fg] = PALETTE[(Number(id) * 7 + String(title).length) % PALETTE.length];
  const len = String(title).length;
  return `<div class="cover ${len > 38 ? 'xlong' : len > 22 ? 'long' : ''}" style="--bg:${bg};--fg:${fg}"><div class="cover-title">${esc(title)}</div><div class="cover-author">${esc(author)}</div></div>`;
}
function cover(b) {
  if (b.cover_url) return `<img class="cimg" src="${esc(b.cover_url)}" alt="" loading="lazy" data-id="${b.id}" data-t="${esc(b.title)}" data-a="${esc(b.author)}">`;
  return genCover(b.id, b.title, b.author);
}
// If a cover link is broken, swap in the generated cover.
document.addEventListener('error', e => {
  const t = e.target;
  if (t && t.tagName === 'IMG' && t.classList.contains('cimg')) {
    const tmp = document.createElement('div');
    tmp.innerHTML = genCover(t.dataset.id, t.dataset.t, t.dataset.a);
    t.replaceWith(tmp.firstElementChild);
  }
}, true);

const stars = b => b.review_count
  ? `<div class="rate"><span class="stars" style="--r:${Number(b.rating) || 0}" aria-hidden="true">\u2605\u2605\u2605\u2605\u2605</span><span class="sr">Rated ${esc(b.rating)} out of 5.</span><span>${esc(b.rating)} (${b.review_count})</span></div>`
  : '<div class="rate">No reviews yet</div>';

function bookCard(b) {
  const out = b.stock <= 0, low = b.stock > 0 && b.stock <= 5, saved = state.wish.has(b.id);
  const shop = canShop();
  return `<article class="card">
    ${shop ? `<button class="wish ${saved ? 'on' : ''}" type="button" data-act="wish" data-id="${b.id}" aria-pressed="${saved}" aria-label="${saved ? 'Remove from' : 'Add to'} wishlist: ${esc(b.title)}">${icon('heart', saved ? 'fill' : '')}</button>` : ''}
    <a class="cover-link" href="#/book/${b.id}" tabindex="-1" aria-hidden="true">${cover(b)}</a>
    <h3><a href="#/book/${b.id}">${esc(b.title)}</a></h3>
    <div class="author">${esc(b.author)}</div>
    ${stars(b)}
    <div class="row between"><span class="price">${rs(b.price)}</span>
      ${out ? '<span class="tag out">Out of stock</span>' : shop ? `<button class="btn btn-gold btn-sm" type="button" data-act="add" data-id="${b.id}" data-title="${esc(b.title)}">Add</button>` : ''}</div>
    ${low ? `<span class="tag low">Only ${b.stock} left</span>` : ''}
  </article>`;
}

const emptyState = (ic, title, text, btn) => `<div class="empty"><div class="art">${icon(ic)}</div><h2>${esc(title)}</h2><p>${esc(text)}</p>${btn || ''}</div>`;
const skeleton = () => `<div class="skel" style="height:220px;border-radius:22px"></div><div class="grid mt">${'<div class="skel tall"></div>'.repeat(8)}</div>`;

/* ---------- sheets (bottom sheet on phones, dialog on desktop) ---------- */
function openSheet(html) {
  const root = document.createElement('div');
  root.className = 'backdrop';
  root.innerHTML = `<div class="sheet" role="dialog" aria-modal="true">${html}</div>`;
  const prev = document.activeElement;
  const onKey = e => { if (e.key === 'Escape') ctl.close(); };
  const ctl = {
    root,
    close() {
      if (!root.isConnected) return;
      root.remove();
      document.body.style.overflow = '';
      document.removeEventListener('keydown', onKey);
      if (prev && prev.focus) prev.focus();
      if (ctl.onclose) ctl.onclose();
    }
  };
  root.addEventListener('mousedown', e => { if (e.target === root) ctl.close(); });
  root.addEventListener('click', e => { if (e.target.closest('[data-close]')) ctl.close(); });
  document.addEventListener('keydown', onKey);
  $('#modalRoot').appendChild(root);
  document.body.style.overflow = 'hidden';
  const first = $('input:not([type=hidden]):not([type=radio]), textarea, select', root) || $('.btn-gold', root);
  if (first) setTimeout(() => first.focus(), 60);
  return ctl;
}
/* ask(): a small question sheet. mode: none (just confirm), radio (pick a reason) or text (write a reason). */
function ask({ title, text = '', mode = 'none', options = [], placeholder = '', ok = 'Confirm', cancel = 'Keep it' }) {
  return new Promise(resolve => {
    const body = mode === 'radio'
      ? `<div class="radio-list">${options.map(o => `<label><input type="radio" name="ans" value="${esc(o)}"><span>${esc(o)}</span></label>`).join('')}</div>`
      : mode === 'text' ? `<label class="field mt"><span class="sr">Reason</span><textarea name="ans" maxlength="200" placeholder="${esc(placeholder)}"></textarea></label>` : '';
    const s = openSheet(`<form novalidate><h2>${esc(title)}</h2><p class="muted">${esc(text)}</p>${body}<div class="form-error" role="alert"></div>
      <div class="sheet-actions"><button type="button" class="btn btn-line" data-close>${esc(cancel)}</button><button class="btn btn-gold" type="submit">${esc(ok)}</button></div></form>`);
    let done = false;
    const finish = v => { if (done) return; done = true; s.close(); resolve(v); };
    s.onclose = () => finish(null);
    $('form', s.root).addEventListener('submit', e => {
      e.preventDefault();
      if (mode === 'none') return finish(true);
      const val = mode === 'radio' ? ($('input[name=ans]:checked', s.root) || {}).value : $('textarea', s.root).value.trim();
      if (!val || (mode === 'text' && val.length < 3)) { $('.form-error', s.root).textContent = mode === 'radio' ? 'Choose a reason to continue.' : 'Write a short reason (at least 3 letters).'; return; }
      finish(val);
    });
  });
}
const formError = (form, msg) => { const el = $('.form-error', form); if (el) el.textContent = msg || ''; };

/* ---------- page chrome: nav, top buttons, bottom tabs ---------- */
function routeInfo() {
  const h = location.hash.slice(1) || '/';
  const [path, qs = ''] = h.split('?');
  return { parts: path.split('/').filter(Boolean), q: new URLSearchParams(qs) };
}
function renderChrome() {
  const name = routeInfo().parts[0] || 'home';
  const tabOf = { home: 'home', search: 'search', book: 'search', cart: 'cart', checkout: 'cart', orders: 'orders', order: 'orders', manage: 'manage', wishlist: 'profile', profile: 'profile', login: 'profile', register: 'profile' }[name];
  const acct = state.user ? '#/profile' : '#/login';
  const items = isStaff()
    ? [['home', '#/', 'Shop', 'home'], ['search', '#/search', 'Browse', 'search'], ['manage', '#/manage', 'Manage', 'manage'], ['profile', acct, 'Account', 'user']]
    : [['home', '#/', 'Home', 'home'], ['search', '#/search', 'Browse', 'search'], ['cart', '#/cart', 'Cart', 'cart'], ['orders', '#/orders', 'Orders', 'orders'], ['profile', acct, state.user ? 'Account' : 'Log in', 'user']];
  $('#tabbar').innerHTML = items.map(([k, href, label, ic]) =>
    `<a class="tab ${tabOf === k ? 'on' : ''}" href="${href}" ${tabOf === k ? 'aria-current="page"' : ''}>${icon(ic)}<span>${label}</span>${k === 'cart' ? `<span class="badge" data-cartcount data-n="${state.cartCount}">${state.cartCount}</span>` : ''}</a>`).join('');
  $('#nav').innerHTML = items.filter(i => i[0] !== 'profile' && i[0] !== 'cart').map(([k, href, label]) => `<a href="${href}" class="${tabOf === k ? 'on' : ''}">${label}</a>`).join('');
  $('#topActions').innerHTML =
    (canShop() ? `<a class="icon-btn" href="${state.user ? '#/wishlist' : '#/login'}" aria-label="Wishlist">${icon('heart')}</a>
      <a class="icon-btn" href="#/cart" aria-label="Cart">${icon('cart')}<span class="badge" data-cartcount data-n="${state.cartCount}">${state.cartCount}</span></a>` : '') +
    `<a class="icon-btn" href="${acct}" aria-label="${state.user ? 'Account' : 'Log in'}">${icon('user')}</a>`;
}

/* ---------- router ---------- */
let renderId = 0;
async function render(keepScroll) {
  const my = ++renderId;
  const { parts, q } = routeInfo();
  const name = parts[0] || 'home';
  const main = $('#main');
  main.classList.toggle('pad-buy', name === 'book' && canShop());
  document.body.classList.toggle('has-buybar', name === 'book' && canShop());
  renderChrome();
  const needUser = ['cart', 'checkout', 'orders', 'order', 'wishlist', 'profile'];
  if (needUser.includes(name) && !state.user) { state.redirect = location.hash; location.hash = '#/login'; return; }
  if (name === 'manage' && !isStaff()) { toast('Only store staff can open this page.', { type: 'warn' }); location.hash = state.user ? '#/' : '#/login'; return; }
  if (['cart', 'checkout', 'orders', 'wishlist'].includes(name) && isStaff()) { location.hash = '#/manage'; return; }
  if (!keepScroll) { main.innerHTML = skeleton(); window.scrollTo(0, 0); }
  const views = {
    home: viewHome, search: () => viewSearch(q), book: () => viewBook(parts[1]), cart: viewCart, checkout: viewCheckout,
    orders: viewOrders, order: () => viewOrder(parts[1], q), wishlist: viewWishlist, profile: viewProfile,
    login: () => viewAuth('login'), register: () => viewAuth('register'), manage: () => viewManage(parts[1], q)
  };
  const scrollY = window.scrollY;
  let html;
  try { html = await (views[name] || viewHome)(); }
  catch (e) {
    html = emptyState('alert', e.status === 404 ? 'We could not find that' : 'Something went wrong', e.message, '<button class="btn btn-dark" type="button" data-act="retry">Try again</button>');
  }
  if (my !== renderId) return;  // user already went somewhere else
  main.innerHTML = html;
  if (keepScroll) window.scrollTo(0, scrollY);
  renderChrome();
}

/* ---------- views: shop ---------- */
async function viewHome() {
  const [top, fresh, cats] = await Promise.all([api('/api/books?sort=rating'), api('/api/books?sort=newest'), getCats()]);
  const t = top.books;
  const fan = [t[1], t[0], t[2]].filter(Boolean);
  return `<section class="hero">
    <div>
      <h1>Books worth a weekend.</h1>
      <p>Order from your phone, pay cash when it arrives, and cancel any time before it ships.</p>
      <form class="hero-search" role="search" data-form="search"><span>${icon('search')}</span><input type="search" name="q" placeholder="Search title, author or ISBN" aria-label="Search books" autocomplete="off"></form>
      <div class="hero-perks"><span><b>Free</b> delivery</span><span><b>7-day</b> returns</span><span><b>Pay</b> on delivery</span></div>
    </div>
    <div class="fan" aria-label="Top rated books">${fan.map(b => `<a href="#/book/${b.id}" aria-label="${esc(b.title)}">${cover(b)}</a>`).join('')}</div>
  </section>
  <div class="chips mt" role="list">
    <a class="chip on" href="#/search">All books</a>
    ${cats.map(c => `<a class="chip" href="#/search?category=${c.id}">${esc(c.name)}<span class="n">${c.count}</span></a>`).join('')}
  </div>
  <div class="section-head"><h2>Top rated</h2><a href="#/search?sort=rating">See all</a></div>
  <div class="hscroll">${t.slice(0, 8).map(bookCard).join('')}</div>
  <div class="section-head"><h2>New on the shelf</h2><a href="#/search">Browse everything</a></div>
  <div class="grid">${fresh.books.slice(0, 10).map(bookCard).join('')}</div>`;
}

async function viewSearch(q) {
  const params = new URLSearchParams();
  ['q', 'category', 'min', 'max', 'sort', 'page'].forEach(k => { if (q.get(k)) params.set(k, q.get(k)); });
  const [res, cats] = await Promise.all([api('/api/books?' + params), getCats()]);
  const active = ['category', 'min', 'max'].filter(k => q.get(k)).length;
  const catName = (cats.find(c => String(c.id) === q.get('category')) || {}).name;
  const link = page => { const p = new URLSearchParams(params); p.set('page', page); return '#/search?' + p; };
  const sort = q.get('sort') || 'newest';
  return `<div class="row between wrap"><div><h1 style="font-size:32px">${q.get('q') ? `Results for \u201C${esc(q.get('q'))}\u201D` : catName ? esc(catName) : 'Browse books'}</h1>
      <p class="muted small mt" style="margin-top:6px">${res.total} ${res.total === 1 ? 'book' : 'books'} found</p></div>
    <div class="row"><select data-change="sort" aria-label="Sort books" style="width:auto;min-height:44px">
      ${[['newest', 'Newest'], ['rating', 'Top rated'], ['price_asc', 'Price: low to high'], ['price_desc', 'Price: high to low'], ['title', 'Title A to Z']].map(([v, l]) => `<option value="${v}" ${sort === v ? 'selected' : ''}>${l}</option>`).join('')}</select>
      <button class="btn btn-line" type="button" data-act="filters">${icon('filter')}Filters${active ? ` (${active})` : ''}</button></div></div>
  <form class="hero-search mt" style="max-width:none" role="search" data-form="search"><span>${icon('search')}</span><input type="search" name="q" value="${esc(q.get('q') || '')}" placeholder="Search title, author or ISBN" aria-label="Search books"></form>
  ${active || q.get('q') ? '<p class="mt"><a href="#/search" class="small"><b>Clear search and filters</b></a></p>' : ''}
  <div class="mt">${res.books.length ? `<div class="grid">${res.books.map(bookCard).join('')}</div>`
      : emptyState('search', 'No books match that', 'Try a shorter word, check the spelling, or clear the filters.', '<a class="btn btn-dark" href="#/search">Show all books</a>')}</div>
  ${res.pages > 1 ? `<nav class="pager" aria-label="Pages">${res.page > 1 ? `<a class="btn btn-line btn-sm" href="${link(res.page - 1)}">Previous</a>` : ''}<span class="muted small">Page ${res.page} of ${res.pages}</span>${res.page < res.pages ? `<a class="btn btn-line btn-sm" href="${link(res.page + 1)}">Next</a>` : ''}</nav>` : ''}`;
}

async function openFilters() {
  const cats = await getCats();
  const q = routeInfo().q;
  const s = openSheet(`<form data-form="filters"><h2>Filters</h2><div class="form-grid mt">
    <label class="field"><span>Category</span><select name="category"><option value="">All categories</option>${cats.map(c => `<option value="${c.id}" ${q.get('category') === String(c.id) ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select></label>
    <div class="form-grid two"><label class="field"><span>Lowest price (\u20B9)</span><input name="min" type="number" min="0" inputmode="numeric" value="${esc(q.get('min') || '')}" placeholder="0"></label>
    <label class="field"><span>Highest price (\u20B9)</span><input name="max" type="number" min="0" inputmode="numeric" value="${esc(q.get('max') || '')}" placeholder="1000"></label></div></div>
    <div class="sheet-actions"><button class="btn btn-line" type="button" data-act="clear-filters">Clear</button><button class="btn btn-gold" type="submit">Show books</button></div></form>`);
  state.sheet = s;
}

async function viewBook(id) {
  const d = await api('/api/books/' + encodeURIComponent(id));
  const b = d.book, out = b.stock <= 0, saved = state.wish.has(b.id), shop = canShop();
  return `<a class="row muted small" href="#/search" style="width:fit-content">${icon('back')} All books</a>
  <div class="detail mt"><div>${cover(b)}</div>
    <div class="stack">
      <div><a class="chip" href="#/search?category=${b.category_id}">${esc(b.category)}</a>${b.active ? '' : ' <span class="tag low">Hidden from the store</span>'}</div>
      <h1>${esc(b.title)}</h1>
      <p class="muted">by ${esc(b.author)}</p>
      ${stars(b)}
      ${b.description ? `<p style="max-width:62ch">${esc(b.description)}</p>` : ''}
      <dl class="facts"><dt>ISBN</dt><dd>${esc(b.isbn)}</dd><dt>Availability</dt><dd>${out ? 'Out of stock' : b.stock <= 5 ? `Only ${b.stock} left` : 'In stock'}</dd><dt>Payment</dt><dd>Cash on delivery</dd><dt>Returns</dt><dd>Within ${state.config.return_days || 7} days of delivery</dd></dl>
      ${shop ? `<div class="buybar"><span class="price" style="font-size:24px">${rs(b.price)}</span>
        <button class="btn btn-line" type="button" data-act="wish" data-id="${b.id}" aria-pressed="${saved}" aria-label="${saved ? 'Remove from' : 'Add to'} wishlist">${icon('heart', saved ? 'fill' : '')}</button>
        ${out ? '<button class="btn btn-dark" disabled>Out of stock</button>' : `<button class="btn btn-gold" type="button" data-act="add" data-id="${b.id}" data-title="${esc(b.title)}">Add to cart</button>`}</div>`
      : `<p class="price" style="font-size:24px">${rs(b.price)}</p>`}
    </div></div>
  <section class="mt-lg"><h2>Reviews</h2>
    ${d.reviews.length ? `<div class="mt">${d.reviews.map(r => `<div class="review"><div class="row between"><b>${esc(r.name)}</b><span class="muted small">${fmt(r.created_at, false)}</span></div>
      <div class="rate"><span class="stars" style="--r:${Number(r.rating)}" aria-hidden="true">\u2605\u2605\u2605\u2605\u2605</span><span class="sr">${Number(r.rating)} out of 5</span></div>${r.comment ? `<p style="margin-top:6px">${esc(r.comment)}</p>` : ''}</div>`).join('')}</div>`
      : '<p class="muted mt">No reviews yet. Readers can review a book after it has been delivered to them.</p>'}
    ${d.can_review ? `<form class="panel mt stack" data-form="review" data-id="${b.id}"><h3>Rate this book</h3>
      <div class="star-pick" role="radiogroup" aria-label="Your rating">${[5, 4, 3, 2, 1].map(n => `<input type="radio" name="rating" id="st${n}" value="${n}"><label for="st${n}" title="${n} star${n > 1 ? 's' : ''}">\u2605</label>`).join('')}</div>
      <label class="field"><span>Your review (optional)</span><textarea name="comment" maxlength="500" placeholder="What did you like or dislike?"></textarea></label>
      <div class="form-error" role="alert"></div><button class="btn btn-dark" type="submit">Post review</button></form>`
      : d.reviewed ? '<p class="muted mt small">You have already reviewed this book. Thank you!</p>' : ''}
  </section>
  ${d.also_bought.length ? `<div class="section-head"><h2>Readers also bought</h2></div><div class="hscroll">${d.also_bought.map(bookCard).join('')}</div>` : ''}`;
}

async function viewWishlist() {
  const d = await api('/api/wishlist');
  state.wish = new Set(d.ids);
  return `<h1 style="font-size:32px">Your wishlist</h1><div class="mt">${d.books.length ? `<div class="grid">${d.books.map(bookCard).join('')}</div>`
    : emptyState('heart', 'Nothing saved yet', 'Tap the heart on any book to keep it here for later.', '<a class="btn btn-dark" href="#/search">Find books</a>')}</div>`;
}

/* ---------- views: cart and checkout ---------- */
async function loadCart() {
  const cart = await api('/api/cart');
  setCart(cart.count);
  if (state.coupon) {  // check the coupon again, because the cart may have changed
    try { state.coupon = await api('/api/coupons/check?code=' + encodeURIComponent(state.coupon.code)); }
    catch (e) { toast(e.message, { type: 'warn', title: 'Coupon removed' }); state.coupon = null; }
  }
  return cart;
}
const cartProblem = i => !i.active ? 'No longer available' : i.stock <= 0 ? 'Out of stock' : i.qty > i.stock ? `Only ${i.stock} left. Lower the quantity.` : '';
function summary(cart, withButton) {
  const disc = state.coupon ? state.coupon.discount : 0;
  return `<div class="panel sticky"><h3>Order summary</h3><div class="mt">
    <div class="sum-row"><span>Subtotal (${cart.count} ${cart.count === 1 ? 'book' : 'books'})</span><span>${rs(cart.subtotal)}</span></div>
    ${disc ? `<div class="sum-row" style="color:var(--green)"><span>Coupon ${esc(state.coupon.code)}</span><span>\u2212 ${rs(disc)}</span></div>` : ''}
    <div class="sum-row"><span>Delivery</span><span>Free</span></div>
    <div class="sum-row total"><span>Total</span><span>${rs(cart.subtotal - disc)}</span></div></div>
    <div class="cod-note mt">${icon('cash')}<span><b>Cash on delivery.</b> You pay when the books arrive. Nothing is charged now.</span></div>
    ${withButton || ''}</div>`;
}
async function viewCart() {
  const cart = await loadCart();
  if (!cart.items.length) return `<h1 style="font-size:32px">Your cart</h1>${emptyState('cart', 'Your cart is empty', 'Add a few books and they will wait for you here.', '<a class="btn btn-gold" href="#/search">Start browsing</a>')}`;
  const bad = cart.items.some(cartProblem);
  return `<h1 style="font-size:32px">Your cart</h1><div class="two-col mt"><div class="panel">${cart.items.map(i => `<div class="line-item">
      <a href="#/book/${i.book_id}" aria-hidden="true" tabindex="-1">${i.cover_url ? `<img class="cimg" src="${esc(i.cover_url)}" alt="" data-id="${i.book_id}" data-t="${esc(i.title)}" data-a="${esc(i.author)}">` : genCover(i.book_id, i.title, i.author)}</a>
      <div class="stack" style="--gap:8px"><div class="row between" style="align-items:flex-start"><div><a href="#/book/${i.book_id}"><b>${esc(i.title)}</b></a><div class="muted small">${esc(i.author)}</div></div><b>${rs(i.price * i.qty)}</b></div>
        ${cartProblem(i) ? `<div class="small" style="color:var(--red);font-weight:600">${esc(cartProblem(i))}</div>` : ''}
        <div class="row between"><div class="qty" role="group" aria-label="Quantity of ${esc(i.title)}"><button type="button" data-act="qty" data-id="${i.book_id}" data-qty="${i.qty - 1}" aria-label="One less" ${i.qty <= 1 ? 'disabled' : ''}>${icon('minus')}</button><b aria-live="polite">${i.qty}</b>
          <button type="button" data-act="qty" data-id="${i.book_id}" data-qty="${i.qty + 1}" aria-label="One more" ${i.qty >= (state.config.max_qty || 10) ? 'disabled' : ''}>${icon('plus')}</button></div>
          <button class="btn btn-line btn-sm" type="button" data-act="remove" data-id="${i.book_id}" data-qty="${i.qty}" data-title="${esc(i.title)}">${icon('trash')}Remove</button></div></div></div>`).join('')}</div>
    <div class="stack"><div class="panel"><h3>Have a coupon?</h3>
      ${state.coupon ? `<div class="coupon-ok"><span>${icon('check')} ${esc(state.coupon.code)} saves you ${rs(state.coupon.discount)}</span><button class="btn btn-line btn-sm" type="button" data-act="coupon-remove">Remove</button></div>`
      : `<form class="coupon mt" data-form="coupon"><input name="code" placeholder="Enter code" maxlength="20" aria-label="Coupon code" autocomplete="off"><button class="btn btn-dark" type="submit">Apply</button></form>`}</div>
      ${summary(cart, `<a class="btn btn-gold btn-block mt" href="#/checkout" ${bad ? 'aria-disabled="true" style="opacity:.5;pointer-events:none"' : ''}>Go to checkout</a>${bad ? '<p class="hint">Fix the items marked in red to continue.</p>' : ''}`)}</div></div>`;
}
async function viewCheckout() {
  const cart = await loadCart();
  if (!cart.items.length) { location.hash = '#/cart'; return ''; }
  if (cart.items.some(cartProblem)) { toast('Fix the items marked in red first.', { type: 'warn' }); location.hash = '#/cart'; return ''; }
  const u = state.user;
  return `<a class="row muted small" href="#/cart" style="width:fit-content">${icon('back')} Back to cart</a><h1 style="font-size:32px;margin-top:10px">Checkout</h1>
  <form class="two-col mt" data-form="checkout"><div class="stack"><div class="panel"><h3>Delivery address</h3><div class="form-grid two mt">
      <label class="field"><span>Full name</span><input name="name" value="${esc(u.name)}" required minlength="2" maxlength="50" autocomplete="name"></label>
      <label class="field"><span>Phone number</span><input name="phone" value="${esc(u.phone)}" required inputmode="numeric" pattern="\\d{10}" maxlength="10" autocomplete="tel" title="10 digits"></label>
      <label class="field full"><span>Address</span><textarea name="address" required minlength="5" maxlength="200" style="min-height:76px" placeholder="House number, street, area" autocomplete="street-address"></textarea></label>
      <label class="field"><span>City</span><input name="city" required minlength="2" maxlength="50" autocomplete="address-level2"></label>
      <label class="field"><span>Pincode</span><input name="pincode" required inputmode="numeric" pattern="\\d{6}" maxlength="6" autocomplete="postal-code" title="6 digits"></label></div></div>
    <div class="panel"><h3>Payment</h3><div class="radio-list"><label><input type="radio" checked disabled><span><b>Cash on delivery</b><br><span class="muted small">Pay the delivery person when your books arrive.</span></span></label></div></div></div>
    <div>${summary(cart, `<div class="form-error" role="alert"></div><button class="btn btn-gold btn-block" type="submit">Place order \u00B7 ${rs(cart.subtotal - (state.coupon ? state.coupon.discount : 0))}</button>`)}</div></form>`;
}

/* ---------- views: orders ---------- */
const miniCovers = o => `<div class="mini-covers">${o.items.slice(0, 5).map(i => genCover(i.book_id, i.title, i.author)).join('')}</div>`;
async function viewOrders() {
  const d = await api('/api/orders');
  setCart(state.cartCount);
  if (!d.orders.length) return `<h1 style="font-size:32px">Your orders</h1>${emptyState('orders', 'No orders yet', 'When you order books, you can track and cancel them here.', '<a class="btn btn-gold" href="#/search">Find a book</a>')}`;
  return `<h1 style="font-size:32px">Your orders</h1><div class="mt">${d.orders.map(o => `<a class="order-card" href="#/order/${o.id}">
    <div class="row between"><b>${esc(o.code)}</b>${statusChip(o.status)}</div>
    <div class="muted small">${fmt(o.created_at)}</div>${miniCovers(o)}
    <div class="row between mt" style="margin-top:12px"><span class="muted small">${o.items.reduce((n, i) => n + i.qty, 0)} items</span><b>${rs(o.total)}</b></div></a>`).join('')}</div>`;
}
const EVENT = { placed: ['Order placed', ''], packed: ['Packed', ''], shipped: ['Shipped', ''], delivered: ['Delivered', ''], cancelled: ['Cancelled', 'bad'], refused: ['Refused. Returned to store', 'warn'], return_requested: ['Return requested', 'warn'], return_rejected: ['Return rejected', 'bad'], returned: ['Return approved', 'warn'] };
function timeline(o) {
  const done = o.events.map(e => `<li class="${(EVENT[e.status] || ['', ''])[1]}"><span class="dot">${icon('check')}</span><b>${esc((EVENT[e.status] || [e.status])[0])}</b><span class="muted small">${fmt(e.at)}${e.note ? ' \u2013 ' + esc(e.note) : ''}</span></li>`);
  const flow = ['placed', 'packed', 'shipped', 'delivered'], at = flow.indexOf(o.status);
  const todo = at >= 0 && at < 3 ? flow.slice(at + 1).map(s => `<li class="todo"><span class="dot"></span><b>${EVENT[s][0]}</b></li>`) : [];
  return `<ol class="timeline">${done.join('')}${todo.join('')}</ol>`;
}
const NEXT = { placed: [['packed', 'Mark packed']], packed: [['shipped', 'Mark shipped']], shipped: [['delivered', 'Mark delivered'], ['refused', 'Customer refused']] };
const staffActions = o => (NEXT[o.status] || []).map(([to, label], i) => `<button class="btn btn-sm ${i ? 'btn-line' : 'btn-gold'}" type="button" data-act="status" data-id="${o.id}" data-to="${to}">${label}</button>`).join('')
  + (o.can_cancel ? `<button class="btn btn-sm btn-danger" type="button" data-act="cancel" data-id="${o.id}">Cancel order</button>` : '');
async function viewOrder(id, q) {
  const { order: o } = await api('/api/orders/' + encodeURIComponent(id));
  const staff = isStaff(), rr = o.return_request;
  return `<a class="row muted small" href="${staff ? '#/manage/orders' : '#/orders'}" style="width:fit-content">${icon('back')} ${staff ? 'All orders' : 'Your orders'}</a>
  ${q.get('placed') ? `<div class="success-banner mt"><div class="tick">${icon('check')}</div><div><h2 style="color:#fff">Order placed</h2><p style="opacity:.8">We will pack it soon. ${esc(payText(o))}.</p></div></div>` : ''}
  <div class="row between wrap mt"><div><h1 style="font-size:32px">${esc(o.code)}</h1><p class="muted small">Placed ${fmt(o.created_at)}${staff ? ' by ' + esc(o.customer_name) : ''}</p></div>${statusChip(o.status)}</div>
  <div class="two-col mt"><div class="stack">
    <div class="panel"><h3>Items</h3><div class="mt">${o.items.map(i => `<div class="line-item"><a href="#/book/${i.book_id}" tabindex="-1" aria-hidden="true">${genCover(i.book_id, i.title, i.author)}</a>
      <div class="row between" style="align-items:flex-start"><div><a href="#/book/${i.book_id}"><b>${esc(i.title)}</b></a><div class="muted small">${esc(i.author)}</div><div class="small">${rs(i.price)} \u00D7 ${i.qty}</div></div><b>${rs(i.price * i.qty)}</b></div></div>`).join('')}</div>
      <div class="sum-row"><span>Subtotal</span><span>${rs(o.subtotal)}</span></div>${o.discount ? `<div class="sum-row" style="color:var(--green)"><span>Coupon ${esc(o.coupon_code)}</span><span>\u2212 ${rs(o.discount)}</span></div>` : ''}
      <div class="sum-row total"><span>Total</span><span>${rs(o.total)}</span></div></div>
    <div class="panel"><h3>Progress</h3><div class="mt">${timeline(o)}</div></div></div>
  <div class="stack">
    <div class="panel"><h3>Payment</h3><p class="mt"><b>Cash on delivery</b></p><p class="muted small">${esc(payText(o))}</p></div>
    <div class="panel"><h3>Delivery address</h3><p class="mt"><b>${esc(o.ship_name)}</b><br>${esc(o.ship_address)}<br>${esc(o.ship_city)} - ${esc(o.ship_pincode)}<br><span class="muted">${esc(o.ship_phone)}</span></p></div>
    ${o.status === 'shipped' && !staff ? `<div class="panel"><p class="small">Your order is on the way. If you do not want it, refuse it at the door and we will restock it. Nothing is charged.</p></div>` : ''}
    ${rr ? `<div class="panel"><div class="row between"><h3>Return</h3>${statusChip(rr.status)}</div><p class="muted small mt">${esc(rr.reason)}${rr.staff_note ? '<br>Store note: ' + esc(rr.staff_note) : ''}</p></div>` : ''}
    <div class="actions">
      ${!staff && o.can_cancel ? `<button class="btn btn-danger" type="button" data-act="cancel" data-id="${o.id}">Cancel order</button>` : ''}
      ${!staff && o.can_return ? `<button class="btn btn-line" type="button" data-act="return" data-id="${o.id}">Return books (until ${esc(o.return_until)})</button>` : ''}
      ${staff ? staffActions(o) : ''}
      <a class="btn btn-line" href="/invoice/${o.id}" target="_blank" rel="noopener">${icon('receipt')}Invoice</a></div></div></div>`;
}

/* ---------- views: account ---------- */
async function viewProfile() {
  const u = state.user;
  return `<h1 style="font-size:32px">Account</h1><div class="panel mt stack"><div class="row between"><div><b style="font-size:19px">${esc(u.name)}</b><div class="muted">${esc(u.email)}</div><div class="muted small">${esc(u.phone)}</div></div><span class="status ${isStaff() ? 'packed' : ''}">${esc(u.role[0].toUpperCase() + u.role.slice(1))}</span></div></div>
  <div class="stack mt">${isStaff() ? `<a class="btn btn-gold btn-block" href="#/manage">${icon('manage')}Manage the store</a>`
    : `<a class="btn btn-line btn-block" href="#/orders">${icon('orders')}My orders</a><a class="btn btn-line btn-block" href="#/wishlist">${icon('heart')}My wishlist</a>`}
    <button class="btn btn-dark btn-block" type="button" data-act="logout">${icon('logout')}Log out</button></div>`;
}
async function viewAuth(mode) {
  if (state.user) { location.hash = '#/profile'; return ''; }
  const reg = mode === 'register';
  return `<div style="max-width:440px;margin:10px auto 0"><h1 style="font-size:34px">${reg ? 'Create your account' : 'Welcome back'}</h1>
  <p class="muted" style="margin-top:8px">${reg ? 'It takes a minute. You pay in cash, so no card details.' : 'Log in to see your cart and orders.'}</p>
  <form class="panel mt stack" data-form="${mode}" novalidate>
    ${reg ? '<label class="field"><span>Full name</span><input name="name" autocomplete="name" maxlength="50"></label>' : ''}
    <label class="field"><span>Email</span><input name="email" type="email" autocomplete="email" maxlength="100"></label>
    ${reg ? '<label class="field"><span>Phone number</span><input name="phone" inputmode="numeric" maxlength="10" autocomplete="tel"><div class="hint">10 digits. Used only for delivery.</div></label>' : ''}
    <label class="field"><span>Password</span><input name="password" type="password" autocomplete="${reg ? 'new-password' : 'current-password'}" maxlength="64">${reg ? '<div class="hint">At least 6 characters.</div>' : ''}</label>
    <div class="form-error" role="alert"></div><button class="btn btn-gold btn-block" type="submit">${reg ? 'Create account' : 'Log in'}</button></form>
  <p class="mt small muted" style="text-align:center">${reg ? 'Already have an account? <a href="#/login"><b>Log in</b></a>' : 'New here? <a href="#/register"><b>Create an account</b></a>'}</p>
  ${!reg && state.config.demo ? `<div class="panel mt"><p class="small muted">Demo accounts. Tap one to fill the form.</p><div class="actions mt" style="margin-top:10px">
    <button class="btn btn-line btn-sm" type="button" data-act="demo" data-e="demo@bookstore.com" data-p="Demo@123">Customer</button>
    <button class="btn btn-line btn-sm" type="button" data-act="demo" data-e="staff@bookstore.com" data-p="Staff@123">Staff</button>
    <button class="btn btn-line btn-sm" type="button" data-act="demo" data-e="admin@bookstore.com" data-p="Admin@123">Admin</button></div></div>` : ''}</div>`;
}

/* ---------- views: manage (staff and admin) ---------- */
async function viewManage(sub, q) {
  const admin = state.user.role === 'admin';
  const tabs = [['dashboard', 'Dashboard', 1], ['orders', 'Orders', 0], ['returns', 'Returns', 0], ['books', 'Books', 0], ['coupons', 'Coupons', 1], ['users', 'Users', 1], ['activity', 'Activity', 1]].filter(t => admin || !t[2]);
  sub = sub || (admin ? 'dashboard' : 'orders');
  if (!tabs.some(t => t[0] === sub)) sub = 'orders';
  const body = await ({ dashboard: mDashboard, orders: mOrders, returns: mReturns, books: mBooks, coupons: mCoupons, users: mUsers, activity: mActivity }[sub])(q);
  return `<h1 style="font-size:32px">Manage store</h1><nav class="mtabs mt" aria-label="Manage sections">${tabs.map(t => `<a href="#/manage/${t[0]}" class="${sub === t[0] ? 'on' : ''}" ${sub === t[0] ? 'aria-current="page"' : ''}>${t[1]}</a>`).join('')}</nav>${body}`;
}
async function mDashboard() {
  const d = await api('/api/admin/dashboard');
  const max = Math.max(1, ...d.last_7_days.map(x => x.sales));
  const topMax = Math.max(1, ...d.top_books.map(x => x.sold));
  return `<section class="sales-panel"><p style="opacity:.75">Sales this month</p><div class="big">${rs(d.sales_month)}</div>
    <div class="bars" role="img" aria-label="Sales for the last 7 days">${d.last_7_days.map(x => `<div title="${rs(x.sales)}"><i style="height:${Math.round(x.sales / max * 100)}%"></i><span>${new Date(x.day + 'T00:00:00').toLocaleDateString('en-IN', { weekday: 'short' })}</span></div>`).join('')}</div></section>
  <div class="facts-row"><div><b>${rs(d.sales_today)}</b><span>Sales today</span></div><div><b>${d.orders_today}</b><span>Orders today</span></div>
    <div><b>${d.pending_orders}</b><span>To pack or ship</span></div><div><b>${d.pending_returns}</b><span>Returns to review</span></div></div>
  <div class="two-col"><div class="panel"><h3>Best sellers</h3>${d.top_books.length ? d.top_books.map(b => `<div class="mt" style="margin-top:14px"><div class="row between"><span>${esc(b.title)}</span><b>${b.sold}</b></div><div class="meter"><i style="width:${Math.round(b.sold / topMax * 100)}%"></i></div></div>`).join('') : '<p class="muted mt">No sales yet.</p>'}</div>
    <div class="panel"><h3>Running low</h3>${d.low_stock.length ? d.low_stock.map(b => `<div class="row between mt" style="margin-top:12px"><span>${esc(b.title)}</span><span class="tag ${b.stock ? 'low' : 'out'}">${b.stock ? b.stock + ' left' : 'Out'}</span></div>`).join('') + '<a class="btn btn-line btn-sm mt" href="#/manage/books?low=1">Update stock</a>' : '<p class="muted mt">Every book is well stocked.</p>'}</div></div>
  <p class="muted small mt">${d.customers} customers \u00B7 ${Object.entries(d.by_status).map(([k, v]) => `${v} ${esc(STATUS_LABEL[k] || k).toLowerCase()}`).join(', ')}</p>`;
}
async function mOrders(q) {
  const status = q.get('status') || '', text = q.get('q') || '';
  const d = await api('/api/orders?' + new URLSearchParams({ status, q: text }));
  const chip = (s, l) => `<a class="chip ${status === s ? 'on' : ''}" href="#/manage/orders${s ? '?status=' + s : ''}">${l}</a>`;
  return `<form class="hero-search" style="max-width:none" role="search" data-form="msearch" data-base="orders"><span>${icon('search')}</span><input type="search" name="q" value="${esc(text)}" placeholder="Order number (BK00012), customer name or phone"><input type="hidden" name="status" value="${esc(status)}"></form>
  <div class="chips mt">${chip('', 'All')}${['placed', 'packed', 'shipped', 'delivered', 'cancelled', 'refused', 'returned'].map(s => chip(s, STATUS_LABEL[s])).join('')}</div>
  <div class="mt">${d.orders.length ? d.orders.map(o => `<div class="list-row"><div class="row between"><a href="#/order/${o.id}"><b>${esc(o.code)}</b></a>${statusChip(o.status)}</div>
    <div class="row between"><span>${esc(o.customer_name)} <span class="muted small">${esc(o.ship_phone)}</span></span><b>${rs(o.total)}</b></div>
    <div class="muted small">${fmt(o.created_at)}. ${o.items.map(i => esc(i.title) + ' \u00D7 ' + i.qty).join(', ')}</div>
    <div class="actions">${staffActions(o)}<a class="btn btn-sm btn-line" href="#/order/${o.id}">Details</a></div></div>`).join('') : emptyState('orders', 'No orders found', 'Try another status or a different search.')}</div>`;
}
async function mReturns(q) {
  const status = q.get('status') || 'requested';
  const d = await api('/api/returns?status=' + encodeURIComponent(status));
  return `<div class="chips">${['requested', 'approved', 'rejected'].map(s => `<a class="chip ${status === s ? 'on' : ''}" href="#/manage/returns?status=${s}">${STATUS_LABEL[s]}</a>`).join('')}</div>
  <div class="mt">${d.returns.length ? d.returns.map(r => `<div class="list-row"><div class="row between"><a href="#/order/${r.order_id}"><b>${esc(r.code)}</b></a>${statusChip(r.status)}</div>
    <div>${esc(r.customer_name)} <span class="muted small">${esc(r.ship_phone)}</span></div><div><b>Reason:</b> ${esc(r.reason)}</div>${r.staff_note ? `<div class="muted small">Note: ${esc(r.staff_note)}</div>` : ''}
    <div class="muted small">Requested ${fmt(r.created_at)}. Order value ${rs(r.total)}</div>
    ${r.status === 'requested' ? `<div class="actions"><button class="btn btn-sm btn-gold" type="button" data-act="return-decide" data-id="${r.id}" data-ok="1">Approve return</button><button class="btn btn-sm btn-danger" type="button" data-act="return-decide" data-id="${r.id}" data-ok="0">Reject</button></div>` : ''}</div>`).join('')
    : emptyState('check', 'Nothing to review', 'Return requests from customers will show up here.')}</div>`;
}
async function mBooks(q) {
  const admin = state.user.role === 'admin';
  const params = new URLSearchParams({ q: q.get('q') || '', page: q.get('page') || 1 });
  if (q.get('low')) params.set('low_stock', '1');
  const d = await api('/api/admin/books?' + params);
  d.books.forEach(b => { state.manageBooks[b.id] = b; });
  const pg = p => `#/manage/books?${new URLSearchParams({ q: q.get('q') || '', page: p, ...(q.get('low') ? { low: 1 } : {}) })}`;
  return `<div class="row between wrap"><form class="hero-search grow" style="margin:0;max-width:420px" role="search" data-form="msearch" data-base="books"><span>${icon('search')}</span><input type="search" name="q" value="${esc(q.get('q') || '')}" placeholder="Search books"></form>
    ${admin ? `<button class="btn btn-gold" type="button" data-act="book-form">${icon('plus')}Add book</button>` : ''}</div>
  <div class="chips mt"><a class="chip ${q.get('low') ? '' : 'on'}" href="#/manage/books">All books</a><a class="chip ${q.get('low') ? 'on' : ''}" href="#/manage/books?low=1">Low stock</a></div>
  <div class="mt">${d.books.length ? d.books.map(b => `<div class="list-row"><div class="row between" style="align-items:flex-start"><div><b>${esc(b.title)}</b>${b.active ? '' : ' <span class="tag out">Hidden</span>'}<div class="muted small">${esc(b.author)}. ${esc(b.category)}. ISBN ${esc(b.isbn)}</div></div><b>${rs(b.price)}</b></div>
    <div class="row between wrap"><form class="stock-edit" data-form="stock" data-id="${b.id}"><label class="small muted" for="s${b.id}">Stock</label><input id="s${b.id}" name="stock" type="number" min="0" max="100000" value="${b.stock}" inputmode="numeric"><button class="btn btn-sm btn-dark" type="submit">Save</button></form>
      <div class="actions">${admin ? `<button class="btn btn-sm btn-line" type="button" data-act="book-form" data-id="${b.id}">Edit</button>` : ''}<a class="btn btn-sm btn-line" href="#/book/${b.id}">View</a></div></div></div>`).join('')
    : emptyState('book', 'No books found', 'Try a different search.')}</div>
  ${d.pages > 1 ? `<nav class="pager"><span>${d.page > 1 ? `<a class="btn btn-line btn-sm" href="${pg(d.page - 1)}">Previous</a>` : ''}</span><span class="muted small">Page ${d.page} of ${d.pages}</span><span>${d.page < d.pages ? `<a class="btn btn-line btn-sm" href="${pg(d.page + 1)}">Next</a>` : ''}</span></nav>` : ''}`;
}
async function mCoupons() {
  const d = await api('/api/admin/coupons');
  return `<div class="row between"><p class="muted">Discount codes customers can use at checkout.</p><button class="btn btn-gold" type="button" data-act="coupon-form">${icon('plus')}New coupon</button></div>
  <div class="mt">${d.coupons.map(c => {
    const expired = c.expires_on && c.expires_on < d.today, full = c.used_count >= c.usage_limit;
    const st = !c.active ? 'Off' : expired ? 'Expired' : full ? 'Used up' : 'Active';
    return `<div class="list-row"><div class="row between"><b style="font-size:18px">${esc(c.code)}</b><span class="status ${st === 'Active' ? 'delivered' : 'cancelled'}">${st}</span></div>
    <div>${c.percent}% off${c.max_discount ? `, up to ${rs(c.max_discount)}` : ''}${c.min_amount ? `, on orders over ${rs(c.min_amount)}` : ''}</div>
    <div class="muted small">Used ${c.used_count} of ${c.usage_limit}. ${c.expires_on ? 'Expires ' + fmt(c.expires_on, false) : 'No expiry date'}</div>
    <div class="actions"><button class="btn btn-sm btn-line" type="button" data-act="coupon-toggle" data-code="${esc(c.code)}">${c.active ? 'Switch off' : 'Switch on'}</button></div></div>`;
  }).join('')}</div>`;
}
async function mUsers() {
  const d = await api('/api/admin/users');
  return `<div class="row between"><p class="muted">Customers, staff and admins.</p><button class="btn btn-gold" type="button" data-act="user-form">${icon('plus')}Add user</button></div>
  <div class="mt">${d.users.map(u => {
    const me = u.id === state.user.id;
    return `<div class="list-row"><div class="row between"><div><b>${esc(u.name)}</b>${me ? ' <span class="muted small">(you)</span>' : ''}<div class="muted small">${esc(u.email)} \u00B7 ${esc(u.phone)}</div></div>${u.active ? '' : '<span class="status cancelled">Disabled</span>'}</div>
    <div class="row wrap"><select data-change="user-role" data-id="${u.id}" aria-label="Role for ${esc(u.name)}" style="width:auto" ${me ? 'disabled' : ''}>${['customer', 'staff', 'admin'].map(r => `<option value="${r}" ${u.role === r ? 'selected' : ''}>${r[0].toUpperCase() + r.slice(1)}</option>`).join('')}</select>
    ${me ? '' : `<button class="btn btn-sm btn-line" type="button" data-act="user-toggle" data-id="${u.id}" data-active="${u.active ? 0 : 1}">${u.active ? 'Disable account' : 'Enable account'}</button>`}</div></div>`;
  }).join('')}</div>`;
}
async function mActivity() {
  const d = await api('/api/admin/audit');
  return d.log.length ? `<div>${d.log.map(l => `<div class="list-row"><div class="row between"><b>${esc(l.user_name)}</b><span class="muted small">${fmt(l.at)}</span></div><div>${esc(l.detail)}</div><div class="muted small">${esc(l.action.replace(/_/g, ' '))}</div></div>`).join('')}</div>`
    : emptyState('info', 'No activity yet', 'Stock, price and order changes by staff appear here.');
}

/* ---------- form sheets used by the manage pages ---------- */
async function bookForm(id) {
  const cats = await getCats();
  const b = id ? state.manageBooks[id] : null;
  const s = openSheet(`<form data-form="book" data-id="${id || ''}" novalidate><h2>${b ? 'Edit book' : 'Add a book'}</h2><div class="form-grid two mt">
    <label class="field full"><span>Title</span><input name="title" maxlength="150" value="${esc(b ? b.title : '')}"></label>
    <label class="field"><span>Author</span><input name="author" maxlength="100" value="${esc(b ? b.author : '')}"></label>
    <label class="field"><span>ISBN</span><input name="isbn" maxlength="20" value="${esc(b ? b.isbn : '')}" placeholder="10 or 13 digits"></label>
    <label class="field"><span>Category</span><select name="category_id">${cats.map(c => `<option value="${c.id}" ${b && b.category_id === c.id ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select></label>
    <label class="field"><span>Price (\u20B9)</span><input name="price" type="number" min="1" inputmode="numeric" value="${b ? b.price : ''}"></label>
    <label class="field"><span>Stock</span><input name="stock" type="number" min="0" inputmode="numeric" value="${b ? b.stock : ''}"></label>
    <label class="field"><span>Cover image link (optional)</span><input name="cover_url" maxlength="300" value="${esc(b ? b.cover_url : '')}" placeholder="https://..."></label>
    <label class="field full"><span>Description</span><textarea name="description" maxlength="1000">${esc(b ? b.description : '')}</textarea></label>
    ${b ? `<label class="row full"><input type="checkbox" name="active" style="width:22px;min-height:22px" ${b.active ? 'checked' : ''}><span>Show this book in the store</span></label>` : ''}</div>
    <div class="form-error" role="alert"></div><div class="sheet-actions"><button class="btn btn-line" type="button" data-close>Cancel</button><button class="btn btn-gold" type="submit">${b ? 'Save changes' : 'Add book'}</button></div></form>`);
  state.sheet = s;
}
function simpleForm(title, name, fields, submit) {
  state.sheet = openSheet(`<form data-form="${name}" novalidate><h2>${title}</h2><div class="form-grid two mt">${fields}</div><div class="form-error" role="alert"></div>
    <div class="sheet-actions"><button class="btn btn-line" type="button" data-close>Cancel</button><button class="btn btn-gold" type="submit">${submit}</button></div></form>`);
}
const fld = (label, name, extra = '', full = false) => `<label class="field ${full ? 'full' : ''}"><span>${label}</span><input name="${name}" ${extra}></label>`;

/* ---------- click handlers (event delegation: one listener for every data-act button) ---------- */
async function refreshSession() {
  if (!state.user) { setCart(0); state.wish = new Set(); return; }
  if (state.user.role === 'customer') {
    const [c, w] = await Promise.all([api('/api/cart'), api('/api/wishlist')]);
    setCart(c.count); state.wish = new Set(w.ids);
  }
}
const acts = {
  retry: () => render(),
  async add(el) {
    if (!state.user) return needLogin('Log in to add books to your cart.');
    await busy(el, async () => {
      try {
        const r = await api('/api/cart', { method: 'POST', data: { book_id: Number(el.dataset.id), qty: 1 } });
        setCart(r.count);
        toast(el.dataset.title + ' is in your cart.', { type: 'success', title: 'Added to cart', action: { label: 'View cart', onClick: () => { location.hash = '#/cart'; } } });
      } catch (e) { fail(e, 'Could not add the book'); }
    });
  },
  async wish(el) {
    if (!state.user) return needLogin('Log in to save books to your wishlist.');
    const id = Number(el.dataset.id);
    try {
      const r = await api('/api/wishlist/' + id, { method: 'POST' });
      r.saved ? state.wish.add(id) : state.wish.delete(id);
      $$(`[data-act=wish][data-id="${id}"]`).forEach(b => { b.classList.toggle('on', r.saved); b.setAttribute('aria-pressed', r.saved); const ic = $('svg', b); if (ic) ic.classList.toggle('fill', r.saved); });
      toast(r.saved ? 'Saved to your wishlist.' : 'Removed from your wishlist.', { type: r.saved ? 'success' : 'info' });
      if (routeInfo().parts[0] === 'wishlist') render(true);
    } catch (e) { fail(e); }
  },
  async qty(el) {
    try { await api('/api/cart/' + el.dataset.id, { method: 'PUT', data: { qty: Number(el.dataset.qty) } }); } catch (e) { fail(e, 'Could not change the quantity'); }
    render(true);
  },
  async remove(el) {
    const { id, qty, title } = el.dataset;
    try {
      await api('/api/cart/' + id, { method: 'DELETE' });
      toast(title + ' was removed.', { type: 'info', title: 'Removed from cart', action: { label: 'Undo', onClick: async () => { try { await api('/api/cart', { method: 'POST', data: { book_id: Number(id), qty: Number(qty) } }); render(true); } catch (e) { fail(e); } } } });
    } catch (e) { fail(e); }
    render(true);
  },
  'coupon-remove': () => { state.coupon = null; toast('Coupon removed.', { type: 'info' }); render(true); },
  filters: openFilters,
  'clear-filters': () => { if (state.sheet) state.sheet.close(); go('#/search'); },
  demo(el) { const f = $('form[data-form=login]'); f.email.value = el.dataset.e; f.password.value = el.dataset.p; f.password.focus(); },
  async logout() {
    try { await api('/api/logout', { method: 'POST' }); } catch (e) { /* ignore */ }
    state.user = null; state.coupon = null; await refreshSession();
    toast('You are logged out.', { type: 'info', title: 'See you soon' }); go('#/');
  },
  async cancel(el) {
    const id = el.dataset.id, staff = isStaff();
    const reason = await ask(staff
      ? { title: 'Cancel this order?', text: 'Write the reason. The customer will see it, and the books go back on the shelf.', mode: 'text', placeholder: 'For example: book damaged in store', ok: 'Cancel order' }
      : { title: 'Cancel your order?', text: 'Tell us why. The books go back on the shelf and nothing is charged.', mode: 'radio', options: state.config.cancel_reasons, ok: 'Cancel order' });
    if (!reason) return;
    try { await api(`/api/orders/${id}/cancel`, { method: 'POST', data: { reason } }); toast('The order was cancelled and the stock is back on the shelf.', { type: 'success', title: 'Order cancelled' }); }
    catch (e) { fail(e, 'Could not cancel'); }
    render(true);
  },
  async return(el) {
    const reason = await ask({ title: 'Return these books?', text: `Why do you want to return them? Returns are accepted for ${state.config.return_days} days after delivery.`, mode: 'radio', options: state.config.return_reasons, ok: 'Request return' });
    if (!reason) return;
    try { await api(`/api/orders/${el.dataset.id}/return`, { method: 'POST', data: { reason } }); toast('The store will review it and update you here.', { type: 'success', title: 'Return requested' }); }
    catch (e) { fail(e, 'Could not request a return'); }
    render(true);
  },
  async status(el) {
    const to = el.dataset.to;
    if (to === 'delivered' && !(await ask({ title: 'Mark as delivered?', text: 'Confirm that the delivery person collected the cash.', ok: 'Yes, delivered', cancel: 'Not yet' }))) return;
    if (to === 'refused' && !(await ask({ title: 'Customer refused delivery?', text: 'The books will go back on the shelf.', ok: 'Yes, restock', cancel: 'Go back' }))) return;
    try { await api(`/api/orders/${el.dataset.id}/status`, { method: 'POST', data: { status: to } }); toast('Order updated to ' + STATUS_LABEL[to].toLowerCase() + '.', { type: 'success', title: 'Status saved' }); }
    catch (e) { fail(e, 'Could not update'); }
    render(true);
  },
  async 'return-decide'(el) {
    const ok = el.dataset.ok === '1';
    const note = ok ? await ask({ title: 'Approve this return?', text: 'The books go back on the shelf. Refund the cash to the customer by hand.', ok: 'Approve return', cancel: 'Go back' })
      : await ask({ title: 'Reject this return?', text: 'Write a short reason. The customer will see it.', mode: 'text', ok: 'Reject return' });
    if (!note) return;
    try { await api(`/api/returns/${el.dataset.id}/decide`, { method: 'POST', data: { approve: ok, note: ok ? '' : note } }); toast(ok ? 'Return approved. Stock restored.' : 'Return rejected.', { type: 'success', title: 'Decision saved' }); }
    catch (e) { fail(e); }
    render(true);
  },
  'book-form': el => bookForm(el.dataset.id),
  'coupon-form': () => simpleForm('New coupon', 'couponNew',
    fld('Code', 'code', 'maxlength="20" placeholder="DIWALI15"') + fld('Discount (%)', 'percent', 'type="number" min="1" max="90"') + fld('Minimum order (\u20B9)', 'min_amount', 'type="number" min="0" value="0"') +
    fld('Maximum discount (\u20B9, 0 = no limit)', 'max_discount', 'type="number" min="0" value="0"') + fld('Expiry date (optional)', 'expires_on', 'type="date"') + fld('Times it can be used', 'usage_limit', 'type="number" min="1" value="100"'), 'Create coupon'),
  async 'coupon-toggle'(el) { try { await api('/api/admin/coupons/' + encodeURIComponent(el.dataset.code), { method: 'PATCH' }); toast('Coupon updated.', { type: 'success' }); } catch (e) { fail(e); } render(true); },
  'user-form': () => simpleForm('Add a user', 'userNew',
    fld('Full name', 'name', 'maxlength="50"') + fld('Email', 'email', 'type="email" maxlength="100"') + fld('Phone (10 digits)', 'phone', 'inputmode="numeric" maxlength="10"') + fld('Password', 'password', 'type="password" maxlength="64"') +
    '<label class="field"><span>Role</span><select name="role"><option value="staff">Staff</option><option value="customer">Customer</option><option value="admin">Admin</option></select></label>', 'Add user'),
  async 'user-toggle'(el) { try { await api('/api/admin/users/' + el.dataset.id, { method: 'PATCH', data: { active: el.dataset.active === '1' } }); toast('User updated.', { type: 'success' }); } catch (e) { fail(e); } render(true); }
};
document.addEventListener('click', e => {
  const el = e.target.closest('[data-act]');
  if (!el || !acts[el.dataset.act]) return;
  e.preventDefault();
  Promise.resolve(acts[el.dataset.act](el, e)).catch(fail);
});
document.addEventListener('change', async e => {
  const el = e.target.closest('[data-change]');
  if (!el) return;
  if (el.dataset.change === 'sort') { const q = routeInfo().q; q.set('sort', el.value); q.delete('page'); go('#/search?' + q); }
  if (el.dataset.change === 'user-role') {
    try { await api('/api/admin/users/' + el.dataset.id, { method: 'PATCH', data: { role: el.value } }); toast('Role changed to ' + el.value + '.', { type: 'success' }); } catch (err) { fail(err); }
    render(true);
  }
});

/* ---------- form handlers ---------- */
const forms = {
  search(f) { const v = f.q.value.trim(); go(v ? '#/search?q=' + encodeURIComponent(v) : '#/search'); },
  filters(f) {
    const q = routeInfo().q;
    ['category', 'min', 'max'].forEach(k => { f[k].value ? q.set(k, f[k].value) : q.delete(k); });
    q.delete('page'); if (state.sheet) state.sheet.close(); go('#/search?' + q);
  },
  msearch(f) { const p = new URLSearchParams(); if (f.q.value.trim()) p.set('q', f.q.value.trim()); if (f.status && f.status.value) p.set('status', f.status.value); go(`#/manage/${f.dataset.base}${p.toString() ? '?' + p : ''}`); },
  async login(f) {
    formError(f);
    await busy($('button[type=submit]', f), async () => {
      try {
        const r = await api('/api/login', { method: 'POST', data: { email: f.email.value.trim(), password: f.password.value } });
        await afterAuth(r.user, 'Welcome back, ' + r.user.name.split(' ')[0] + '.');
      } catch (e) { formError(f, e.message); fail(e, 'Could not log in'); }
    });
  },
  async register(f) {
    formError(f);
    await busy($('button[type=submit]', f), async () => {
      try {
        const r = await api('/api/register', { method: 'POST', data: { name: f.name.value, email: f.email.value.trim(), phone: f.phone.value.trim(), password: f.password.value } });
        await afterAuth(r.user, 'Your account is ready, ' + r.user.name.split(' ')[0] + '.');
      } catch (e) { formError(f, e.message); fail(e, 'Could not create the account'); }
    });
  },
  async coupon(f) {
    const code = f.code.value.trim();
    if (!code) return toast('Type a coupon code first.', { type: 'warn' });
    await busy($('button', f), async () => {
      try { state.coupon = await api('/api/coupons/check?code=' + encodeURIComponent(code)); toast(`You save ${rs(state.coupon.discount)} on this order.`, { type: 'success', title: state.coupon.code + ' applied' }); render(true); }
      catch (e) { fail(e, 'Coupon not applied'); }
    });
  },
  async checkout(f) {
    formError(f);
    const data = Object.fromEntries(new FormData(f)); data.coupon = state.coupon ? state.coupon.code : ''; data.payment_method = 'COD';
    await busy($('button[type=submit]', f), async () => {
      try {
        const r = await api('/api/orders', { method: 'POST', data });
        state.coupon = null; setCart(0);
        toast(`Pay ${rs(r.order.total)} in cash when it arrives.`, { type: 'success', title: 'Order placed' });
        location.hash = `#/order/${r.order.id}?placed=1`;
      } catch (e) {
        formError(f, e.message); fail(e, 'Could not place the order');
        if (e.status === 409) location.hash = '#/cart'; else if (e.status === 422) { state.coupon = null; render(true); }
      }
    });
  },
  async review(f) {
    formError(f);
    const rating = (f.querySelector('input[name=rating]:checked') || {}).value;
    if (!rating) return formError(f, 'Pick a star rating first.');
    await busy($('button[type=submit]', f), async () => {
      try { await api(`/api/books/${f.dataset.id}/reviews`, { method: 'POST', data: { rating: Number(rating), comment: f.comment.value } }); toast('Thank you for rating this book.', { type: 'success', title: 'Review posted' }); render(true); }
      catch (e) { formError(f, e.message); fail(e); }
    });
  },
  async stock(f) {
    try { await api(`/api/admin/books/${f.dataset.id}/stock`, { method: 'PATCH', data: { stock: f.stock.value } }); toast('Stock saved.', { type: 'success' }); render(true); }
    catch (e) { fail(e, 'Could not save the stock'); }
  },
  async book(f) {
    formError(f);
    const id = f.dataset.id, data = Object.fromEntries(new FormData(f));
    data.active = id ? !!f.active.checked : true;
    await busy($('button[type=submit]', f), async () => {
      try {
        await api(id ? '/api/admin/books/' + id : '/api/admin/books', { method: id ? 'PUT' : 'POST', data });
        state.cats = null; state.sheet.close(); toast(id ? 'Book details saved.' : 'The book is now in the store.', { type: 'success', title: id ? 'Saved' : 'Book added' }); render(true);
      } catch (e) { formError(f, e.message); }
    });
  },
  async couponNew(f) {
    formError(f);
    await busy($('button[type=submit]', f), async () => {
      try { await api('/api/admin/coupons', { method: 'POST', data: Object.fromEntries(new FormData(f)) }); state.sheet.close(); toast('Customers can use it right away.', { type: 'success', title: 'Coupon created' }); render(true); }
      catch (e) { formError(f, e.message); }
    });
  },
  async userNew(f) {
    formError(f);
    await busy($('button[type=submit]', f), async () => {
      try { await api('/api/admin/users', { method: 'POST', data: Object.fromEntries(new FormData(f)) }); state.sheet.close(); toast('They can log in now.', { type: 'success', title: 'User added' }); render(true); }
      catch (e) { formError(f, e.message); }
    });
  }
};
document.addEventListener('submit', e => {
  const f = e.target.closest('form[data-form]');
  if (!f || !forms[f.dataset.form]) return;
  e.preventDefault();
  Promise.resolve(forms[f.dataset.form](f)).catch(fail);
});
async function afterAuth(user, message) {
  state.user = user;
  await refreshSession();
  toast(message, { type: 'success', title: 'Logged in' });
  const next = state.redirect && !/login|register/.test(state.redirect) ? state.redirect : (user.role === 'customer' ? '#/' : '#/manage');
  state.redirect = '';
  go(next);
}

/* ---------- start ---------- */
window.addEventListener('hashchange', () => render());
(async function boot() {
  try {
    const [cfg, me] = await Promise.all([api('/api/config'), api('/api/me')]);
    state.config = cfg; state.user = me.user;
    await refreshSession();
  } catch (e) { toast(e.message, { type: 'error', title: 'Cannot reach the store' }); }
  render();
})();

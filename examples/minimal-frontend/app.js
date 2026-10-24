// A minimal replacement frontend for Semechka, built only on the documented API through sdk/js/semechka.mjs.
// It signs in (with two-step codes), lists conversations, chooses a model from the person's catalogue, streams replies
// with Stop, makes new versions of a reply and moves between them, and paints the organisation's theme.
// Every text from the server is set with textContent: nothing a model writes can become markup or script.
import { Semechka, SemechkaError } from './semechka.mjs';

const api = new Semechka();
const app = document.getElementById('app');
let me = null, catalog = null, convs = [], thread = null, running = null;

/** Build an element: h('button', { class: 'primary', onclick }, 'Send'). Strings become text nodes. */
function h(tag, props = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.className = v;
    else el.setAttribute(k, v === true ? '' : String(v));
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return el;
}
const say = (e) => (e instanceof SemechkaError ? e.message : 'Something went wrong. Try again.');

// ---- start ---------------------------------------------------------------------------------------------------------
async function start() {
  try { me = await api.me(); } catch (e) { if (e instanceof SemechkaError && e.status === 401) return signIn(); throw e; }
  const [spec] = await Promise.all([api.themeTokens(), loadCatalog(), loadList()]);
  const t = api.resolveTheme(me, spec);
  api.paint(document.documentElement, t.values, t.variant);
  document.title = me.ui.brand?.tab_title || me.ui.brand?.app_name || 'Chat';
  route();
}
window.addEventListener('hashchange', () => me && route());

async function loadCatalog() {
  try { catalog = await api.catalog('text'); } catch (e) { catalog = { error: say(e), models: [], groups: [], ungrouped: [] }; }
}
async function loadList() { convs = await api.conversations({ limit: 100 }); }

// ---- sign-in -------------------------------------------------------------------------------------------------------
function signIn(needCode = false, saved = {}) {
  const err = h('p', { class: 'err', role: 'alert' });
  const email = h('input', { type: 'email', autocomplete: 'username', required: true, value: saved.email ?? '' });
  const password = h('input', { type: 'password', autocomplete: 'current-password', required: true });
  const code = h('input', { inputmode: 'numeric', autocomplete: 'one-time-code', placeholder: '123456 or a recovery code' });
  const form = h('form', { class: 'signin', onsubmit: async (ev) => {
    ev.preventDefault(); err.textContent = '';
    const c = code.value.trim();
    try {
      await api.login({ email: email.value, password: password.value, ...(c ? (/^\d{6}$/.test(c) ? { totp: c } : { recoveryCode: c }) : {}) });
      start();
    } catch (e) {
      if (e instanceof SemechkaError && e.code === 'mfa_required' && !needCode) return signIn(true, { email: email.value });
      err.textContent = say(e);
    }
  } },
  h('h1', {}, 'Sign in'),
  h('label', {}, 'Email', email), h('label', {}, 'Password', password),
  needCode ? h('label', {}, 'Authentication code', code) : null,
  err, h('button', { class: 'primary', type: 'submit' }, 'Sign in'));
  app.replaceChildren(form);
  (needCode ? password : email).focus();
}

// ---- the chat screen -----------------------------------------------------------------------------------------------
async function route() {
  const id = location.hash.match(/^#\/c\/([\w-]+)/)?.[1] ?? null;
  thread = null;
  if (id) {
    try { thread = await api.thread(id, { limit: 100 }); } catch (e) { location.hash = ''; return; }
  }
  render();
}

function modelSelect() {
  if (catalog.error) return h('span', { class: 'err' }, catalog.error);
  const byId = Object.fromEntries(catalog.models.map((m) => [m.id, m]));
  const sel = h('select', { id: 'model', 'aria-label': 'Model' });
  // the administrator's groups and order, then the rest
  for (const g of catalog.groups) sel.append(h('optgroup', { label: g.name }, g.models.map((id) => h('option', { value: id }, byId[id].alias))));
  for (const id of catalog.ungrouped) sel.append(h('option', { value: id }, byId[id].alias));
  sel.value = thread?.model_id && byId[thread.model_id] ? thread.model_id : catalog.default ?? '';
  return sel;
}

function messageView(m) {
  const el = h('div', { class: `msg ${m.role}`, 'data-id': m.id }, m.content);
  if (m.role === 'assistant') {
    const s = m.siblings;
    el.append(h('div', { class: 'meta' },
      m.model ?? '', m.status !== 'complete' ? ` · ${m.status}` : '',
      s && s.count > 1 ? h('button', { 'aria-label': 'Previous version', disabled: !s.prev, onclick: () => showVersion(s.prev) }, '‹') : null,
      s && s.count > 1 ? `${s.index + 1}/${s.count}` : '', // index is zero-based
      s && s.count > 1 ? h('button', { 'aria-label': 'Next version', disabled: !s.next, onclick: () => showVersion(s.next) }, '›') : null,
      h('button', { onclick: () => regenerate(m), disabled: !!running }, 'New version')));
  }
  return el;
}

function render() {
  const list = h('ul', {}, convs.map((c) => h('li', {}, h('a', { href: `#/c/${c.id}`, 'aria-current': thread?.id === c.id ? 'page' : null }, c.title || 'Untitled'))));
  const side = h('nav', { class: 'side', 'aria-label': 'Conversations' },
    h('button', { onclick: () => { location.hash = ''; } }, 'New conversation'), list,
    h('button', { onclick: async () => { await api.logout(); me = null; signIn(); } }, 'Sign out'));
  const box = h('div', { class: 'thread', id: 'thread' },
    thread ? thread.messages.map(messageView) : h('h1', {}, me.ui.brand?.welcome_title || 'How can I help?'));
  const input = h('textarea', { id: 'msg', 'aria-label': 'Message', placeholder: 'Message', onkeydown: (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } } });
  const status = h('p', { id: 'status', class: 'muted', role: 'status' });
  const composer = h('div', { class: 'composer' }, input, h('div', { class: 'row' }, modelSelect(), h('span', { class: 'grow' }),
    h('button', { id: 'stop', hidden: true, onclick: () => running?.stop() }, 'Stop'),
    h('button', { id: 'send', class: 'primary', onclick: send, disabled: !catalog.models.length }, 'Send')), status);
  app.replaceChildren(h('div', { class: 'shell' }, side, h('section', { class: 'main', 'aria-label': 'Conversation' }, box, composer)));
  box.scrollTop = box.scrollHeight;
  input.focus();
}

// ---- turns ---------------------------------------------------------------------------------------------------------
function busy(on, text = '') {
  document.getElementById('send').disabled = on; document.getElementById('stop').hidden = !on;
  document.getElementById('status').textContent = text;
}

/** Stream one reply into a new message element; returns the final 'done' event. */
async function streamInto(call) {
  const box = document.getElementById('thread');
  const live = h('div', { class: 'msg assistant' });
  box.append(live);
  busy(true, 'Assistant is replying');
  running = call({ onEvent: (ev) => {
    if (ev.type === 'delta' && ev.content) { live.textContent += ev.content; box.scrollTop = box.scrollHeight; }
    else if (ev.type === 'queue' && !ev.running) busy(true, `Waiting: position ${ev.position}`);
    else if (ev.type === 'error') busy(true, ev.message);
  } });
  try { return await running.done; } finally { running = null; busy(false); }
}

async function send() {
  const input = document.getElementById('msg'), content = input.value.trim(), modelId = document.getElementById('model')?.value;
  if (!content || !modelId || running) return;
  try {
    let id = thread?.id;
    if (!id) { id = (await api.createConversation({ modelId })).id; }
    input.value = '';
    document.getElementById('thread').append(messageView({ role: 'user', content, id: 'pending' }));
    const last = thread?.messages.at(-1)?.id ?? null;
    await streamInto((o) => api.send(id, { content, modelId, parentId: last }, o));
    // the server stored both messages (also when stopped); show them as stored
    await loadList();
    if (location.hash !== `#/c/${id}`) location.hash = `#/c/${id}`; else route();
  } catch (e) { busy(false, say(e)); }
}

async function regenerate(m) {
  if (running) return;
  const modelId = document.getElementById('model')?.value || m.model_id;
  try {
    document.querySelector(`[data-id="${CSS.escape(m.id)}"]`)?.remove();
    await streamInto((o) => api.regenerate(thread.id, m.id, modelId, o));
    route();
  } catch (e) { busy(false, say(e)); }
}

async function showVersion(messageId) {
  try { thread = await api.showBranch(thread.id, messageId, 100); render(); } catch (e) { busy(false, say(e)); }
}

start().catch((e) => { app.replaceChildren(h('p', { class: 'err', role: 'alert' }, say(e))); });



(function () {
  'use strict';

  if (window.__coherentLoaded) return;
  window.__coherentLoaded = true;

  const DEBUG = true;
  const LOG = DEBUG ? (...a) => console.log('[coherent]', ...a) : () => {};
  const WARN = (...a) => console.warn('[coherent]', ...a);

  // Content-script build marker — printed on every injection. Confirms which
  // content-script version is live from the LinkedIn tab's DevTools (the SW
  // build marker only proves the worker; this proves the page code).
  const COHERENT_CS_BUILD = '2026-06-28-connect-premium-fallback-v43';
  LOG('content_linkedin.js loaded — build', COHERENT_CS_BUILD);

  // 45s: the DM flow alone can spend ~8s waiting for Send to enable plus the
  // overlay-cleanup + window-find retry passes — 25s left no headroom on a
  // slow page and timed out flows that would have succeeded.
  const COMMAND_TIMEOUT_MS = 45_000;
  const POLL_INTERVAL_MS = 250;
  const HEAL_TIMEOUT_MS = 20_000;

  // ── Default selector registry ───────────────────────────────────────────
  // Primary + fallback per intent. Most are aria-label or role-based to
  // survive LinkedIn class-name obfuscation.
  const DEFAULT_REGISTRY = {
    connectButton:        { primary: '[aria-label^="Invite "]',                      fallback: 'a[href*="custom-invite"]',     failCount: 0 },
    moreButton:           { primary: 'button[aria-label="More actions"]',            fallback: 'button[aria-label*="More"]',    failCount: 0 },
    addNoteButton:        { primary: 'button[aria-label="Add a note"]',              fallback: null,                            failCount: 0 },
    noteTextarea:         { primary: 'textarea[name="message"]',                     fallback: '#custom-message',               failCount: 0 },
    sendInvitationButton: { primary: 'button[aria-label="Send invitation"]',         fallback: 'button[aria-label*="Send"]',    failCount: 0 },
    messageButton:        { primary: 'button[aria-label^="Message "]',               fallback: null,                            failCount: 0 },
    profileName:          { primary: 'main h1',                                       fallback: 'main section h1',               failCount: 0 },
    composeEditor:        { primary: 'div.msg-form__contenteditable[contenteditable="true"]', fallback: 'div[role="textbox"][contenteditable="true"]', failCount: 0 },
    sendDmButton:         { primary: 'button.msg-form__send-button',                  fallback: 'button[aria-label^="Press enter to send"]', failCount: 0 },
    connectionCard:       { primary: '[componentkey] a[href*="/in/"]',                fallback: 'main a[href*="/in/"]',          failCount: 0 },
  };

  let registry = JSON.parse(JSON.stringify(DEFAULT_REGISTRY));
  const healedThisSession = new Set();

  // ── Shadow-DOM-aware query helpers ──────────────────────────────────────
  // LinkedIn's "New message" pop-out and floating chat are rendered inside an
  // OPEN shadow root attached to #interop-outlet (data-testid="interop-shadowdom").
  // Plain document.querySelector cannot pierce shadow boundaries — we have to
  // walk known shadow roots explicitly. Confirmed via diagnostic on 2026-06-01.
  // Collect EVERY searchable scope: the main document, all open shadow roots
  // (recursively — LinkedIn nests them), and all same-origin IFRAME documents
  // (recursively too). The new chat experience rendered ZERO editors visible
  // from the top document (observed live: eds=0 with the pane open on
  // screen), which means it lives in an embedded frame — reachable via
  // contentDocument since it's same-origin.
  function collectSearchScopes() {
    const scopes = [];
    const seen = new Set();
    const add = (node) => {
      if (!node || seen.has(node)) return;
      seen.add(node);
      scopes.push(node);
      let all;
      try { all = node.querySelectorAll('*'); } catch { return; }
      all.forEach((el) => {
        if (el.shadowRoot) add(el.shadowRoot);
        if (el.tagName === 'IFRAME') {
          try { if (el.contentDocument) add(el.contentDocument); } catch { /* cross-origin */ }
        }
      });
    };
    add(document);
    return scopes;
  }

  function deepQuerySelector(selector) {
    for (const scope of collectSearchScopes()) {
      try {
        const el = scope.querySelector(selector);
        if (el) return el;
      } catch { /* invalid selector for this context */ }
    }
    return null;
  }

  function deepQuerySelectorAll(selector) {
    const out = [];
    for (const scope of collectSearchScopes()) {
      try { out.push(...scope.querySelectorAll(selector)); } catch {}
    }
    return out;
  }

  async function loadRegistry() {
    try {
      const data = await chrome.storage.local.get(['selectorRegistry']);
      if (data.selectorRegistry) {
        registry = { ...DEFAULT_REGISTRY, ...data.selectorRegistry };
        LOG('Loaded selector registry from storage');
      }
    } catch (e) { LOG('loadRegistry error:', e); }
  }

  async function saveRegistry() {
    try { await chrome.storage.local.set({ selectorRegistry: registry }); }
    catch (e) { LOG('saveRegistry error:', e); }
  }

  function trySelector(intent) {
    const entry = registry[intent];
    if (!entry) return null;
    let el = deepQuerySelector(entry.primary);
    if (el) { entry.failCount = 0; return el; }
    if (entry.fallback) {
      el = deepQuerySelector(entry.fallback);
      if (el) {
        // Promote the working fallback to primary
        entry.primary = entry.fallback;
        entry.fallback = null;
        entry.failCount = 0;
        saveRegistry();
        return el;
      }
    }
    entry.failCount++;
    return null;
  }

  function findButtonByText(regex) {
    const buttons = deepQuerySelectorAll('button');
    return buttons.find(b => regex.test((b.innerText || b.textContent || '').trim()));
  }

  loadRegistry();

  // ── DOM snapshot for AI healer (shadow-DOM aware) ───────────────────────
  // LinkedIn renders its invite/compose modals inside OPEN shadow roots, which
  // `cloneNode(true)` does NOT copy — that's why earlier heals saw an empty
  // page and returned no selectors. We serialise via `outerHTML` (which DOES
  // include shadow subtrees) and, when a dialog is open, place it FIRST so the
  // element we actually need to heal survives the byte-cap truncation.
  function getPageSnapshot(maxBytes = 10000) {
    const parts = [];
    const dialog = deepQuerySelector(
      '[data-test-modal], .send-invite-modal, [role="dialog"], [aria-modal="true"], .artdeco-modal, .msg-form',
    );
    if (dialog) parts.push('<!-- OPEN_DIALOG (may live in a shadow root) -->' + dialog.outerHTML);
    const main = document.querySelector('main')
              || document.querySelector('[role="main"]')
              || document.body;
    if (main) parts.push('<!-- MAIN_COLUMN -->' + main.outerHTML);

    const wrapper = document.createElement('div');
    wrapper.innerHTML = parts.join('');
    wrapper.querySelectorAll('script, style, noscript, svg, link, [hidden]').forEach(el => el.remove());
    wrapper.querySelectorAll('img').forEach(img => img.setAttribute('src', ''));
    let html = wrapper.innerHTML.replace(/\s+/g, ' ');
    if (html.length > maxBytes) html = html.slice(0, maxBytes);
    return html;
  }

  function pageType() {
    const u = location.href;
    if (u.includes('/in/')) return 'profile';
    if (u.includes('/messaging/')) return 'messaging';
    if (u.includes('/search/')) return 'search';
    if (u.includes('/feed/')) return 'feed';
    if (u.includes('/company/')) return 'company';
    return 'other';
  }

  // ── Request AI heal and wait ────────────────────────────────────────────
  // Routes through background.js (which has the token + can make CORS requests).
  function requestHealAndWait(intent, timeoutMs = HEAL_TIMEOUT_MS) {
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        WARN(`Heal timeout for "${intent}" after ${timeoutMs}ms`);
        resolve(false);
      }, timeoutMs);
      const failedSelectors = [];
      const entry = registry[intent];
      if (entry) {
        if (entry.primary)  failedSelectors.push(entry.primary);
        if (entry.fallback) failedSelectors.push(entry.fallback);
      }
      const payload = {
        intent,
        page_type: pageType(),
        url: location.href,
        failed_selectors: failedSelectors,
        dom_snapshot: getPageSnapshot(),
      };
      chrome.runtime.sendMessage({ kind: 'heal-request', payload }, (response) => {
        clearTimeout(timer);
        if (chrome.runtime.lastError) {
          WARN('heal-request failed:', chrome.runtime.lastError.message);
          resolve(false);
          return;
        }
        if (!response || !response.ok || !response.selectors || response.selectors.length === 0) {
          WARN(`AI heal returned no selectors for "${intent}"`);
          resolve(false);
          return;
        }
        const newPrimary = response.selectors[0];
        const newFallback = response.selectors[1] || null;
        registry[intent] = { primary: newPrimary, fallback: newFallback, failCount: 0 };
        saveRegistry();
        LOG(`Selector healed: ${intent} → "${newPrimary}"`);
        resolve(true);
      });
    });
  }

  // ── Resilient finder ────────────────────────────────────────────────────
  // Tries: registry → registry fallback → text-based hardcoded fallback → AI heal → retry registry
  async function findResilient(intent, textFallbackRegex = null) {
    // 1. registry
    let el = trySelector(intent);
    if (el) return el;
    // 2. text-based hardcoded fallback (e.g. innerText matches /^connect$/i)
    if (textFallbackRegex) {
      el = findButtonByText(textFallbackRegex);
      if (el) return el;
    }
    // 3. AI heal (once per intent per session to avoid loops)
    if (!healedThisSession.has(intent)) {
      healedThisSession.add(intent);
      LOG(`All strategies failed for "${intent}" — requesting AI heal...`);
      const healed = await requestHealAndWait(intent);
      if (healed) {
        // 4. retry registry with new selectors
        el = trySelector(intent);
        if (el) return el;
        // 5. last-ditch text fallback again
        if (textFallbackRegex) {
          el = findButtonByText(textFallbackRegex);
          if (el) return el;
        }
      }
    }
    return null;
  }

  // ── Promise helpers ─────────────────────────────────────────────────────
  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  async function waitFor(predicate, timeoutMs = COMMAND_TIMEOUT_MS) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      try {
        const result = await predicate();
        if (result) return result;
      } catch (e) { /* keep polling */ }
      await sleep(POLL_INTERVAL_MS);
    }
    throw new Error(`waitFor timed out after ${timeoutMs}ms`);
  }

  // ── Navigation ──────────────────────────────────────────────────────────
  async function ensureOnTarget(targetUrl) {
    if (location.href.startsWith(targetUrl.replace(/\/$/, ''))) return { navigated: false };
    location.assign(targetUrl);
    return { navigated: true };
  }

  // ── DM compose editor (multi-strategy, handles "New message" pop-out) ──
  function findComposeEditor() {
    // Selectors confirmed via shadow-DOM diagnostic on 2026-06-01.
    // LinkedIn's "New message" composer is at: #interop-outlet >> shadowRoot >>
    //   <div role="textbox" contenteditable="true" aria-label="Write a message…"
    //        class="msg-form__contenteditable ...">
    const selectors = [
      'div.msg-form__contenteditable[contenteditable="true"]',
      'div[role="textbox"][contenteditable="true"][aria-label*="message" i]',
      'div[role="textbox"][contenteditable="true"]',
      'div[contenteditable="true"][aria-label*="Write" i]',
      'div[contenteditable="true"][aria-label*="message" i]',
      'div[contenteditable="true"][data-placeholder*="message" i]',
      'textarea[placeholder*="message" i]',
      'textarea[aria-label*="message" i]',
    ];
    for (const sel of selectors) {
      const el = deepQuerySelector(sel);
      // offsetParent is null for shadow-DOM elements when checked from main doc,
      // so we accept any element the deep query returned.
      if (el) return el;
    }
    // Last resort: any visible contenteditable across shadow boundaries.
    const editables = deepQuerySelectorAll('[contenteditable="true"]');
    if (editables.length > 0) {
      // Prefer largest visible
      editables.sort((a, b) => (b.offsetWidth * b.offsetHeight) - (a.offsetWidth * a.offsetHeight));
      return editables[0];
    }
    return null;
  }

  async function focusComposerArea() {
    // First: try clicking the editor directly (most reliable for shadow-DOM)
    const editor = findComposeEditor();
    if (editor) { editor.click(); editor.focus?.(); await sleep(200); return true; }
    // Click any visible "Write a message…" placeholder text node
    const placeholder = deepQuerySelectorAll('div, p, span')
      .find(el => /^(write a message|type a message|message)/i.test((el.innerText || '').trim()));
    if (placeholder) { placeholder.click(); await sleep(350); return true; }
    return false;
  }

  // ── Shadow-DOM-aware text insertion ─────────────────────────────────────
  // The composer lives inside #interop-outlet's OPEN shadow root and is a
  // controlled component (Lexical/Quill-style). We can mutate its DOM, but
  // that alone doesn't update the controller's "isEmpty" flag, which is what
  // gates the Send button. We use a layered approach:
  //   Strategy A: ClipboardEvent('paste') with DataTransfer — LinkedIn's
  //     msg-form has an explicit paste handler that processes clipboardData
  //     and updates the controlled state. Most reliable across React versions.
  //   Strategy B: beforeinput → DOM mutation → input (all composed:true)
  //     using the shadow root's own getSelection() API. Fallback if paste
  //     was rejected.
  //   Strategy C: Bubble the input event to .msg-form / shadow host too,
  //     in case the controller listens on the parent form instead of editor.
  // After insertion we wait up to 1.5s for the controller to update; if Send
  // stays disabled we surface a real failure so the watchdog can retry.
  function pasteInsert(editor, body) {
    try {
      const dt = new DataTransfer();
      dt.setData('text/plain', body);
      const ev = new ClipboardEvent('paste', {
        bubbles: true, cancelable: true, composed: true,
        clipboardData: dt,
      });
      // Some Chromium builds drop clipboardData on synthetic events
      if (!ev.clipboardData) {
        Object.defineProperty(ev, 'clipboardData', { value: dt });
      }
      return editor.dispatchEvent(ev);
    } catch (e) { return false; }
  }

  function beforeInputInsert(editor, body) {
    // Ranges/elements MUST come from the editor's own document — the editor
    // may live in an iframe, and a top-document Range over foreign nodes
    // throws WrongDocumentError.
    const doc = editor.ownerDocument || document;
    const root = editor.getRootNode();
    const sel = (root && typeof root.getSelection === 'function')
      ? root.getSelection()
      : (doc.defaultView || window).getSelection();
    try {
      const range = doc.createRange();
      range.selectNodeContents(editor);
      range.collapse(false);
      if (sel) { sel.removeAllRanges(); sel.addRange(range); }
    } catch (e) { /* shadow selection can be picky */ }

    const accepted = editor.dispatchEvent(new InputEvent('beforeinput', {
      bubbles: true, cancelable: true, composed: true,
      inputType: 'insertText', data: body,
    }));

    if (accepted) {
      while (editor.firstChild) editor.removeChild(editor.firstChild);
      const p = doc.createElement('p');
      p.textContent = body;
      editor.appendChild(p);
      try {
        const r2 = doc.createRange();
        r2.selectNodeContents(p);
        r2.collapse(false);
        if (sel) { sel.removeAllRanges(); sel.addRange(r2); }
      } catch (e) { /* ignore */ }
    }

    editor.dispatchEvent(new InputEvent('input', {
      bubbles: true, cancelable: false, composed: true,
      inputType: 'insertText', data: body,
    }));
  }

  function bubbleInputToParents(editor, body) {
    // Some controllers register the listener on the form/dialog, not editor.
    // Fire input on the closest msg-form and the shadow host so a parent-side
    // listener has a chance to react.
    const inputEv = () => new InputEvent('input', {
      bubbles: true, composed: true,
      inputType: 'insertText', data: body,
    });
    const form = editor.closest?.('form, .msg-form, [data-test-msg-form]');
    if (form) form.dispatchEvent(inputEv());
    const root = editor.getRootNode();
    if (root && root.host) root.host.dispatchEvent(inputEv());
  }

  function insertTextIntoEditor(editor, body) {
    editor.focus();
    // A: paste handler (most reliable). If it succeeds, the controller
    // usually inserts the text itself, so we DON'T need to mutate the DOM.
    const pasted = pasteInsert(editor, body);
    const haveTextAfterPaste = (editor.innerText || '').trim().length > 0;
    if (pasted && haveTextAfterPaste) {
      bubbleInputToParents(editor, body);
      editor.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
      LOG('Insert strategy: paste');
      return;
    }
    // B: explicit DOM mutation + composed events
    beforeInputInsert(editor, body);
    bubbleInputToParents(editor, body);
    editor.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
    LOG('Insert strategy: beforeinput+DOM');
  }

  function isComposerEmpty(editor) {
    if (!editor) return true;
    return (editor.innerText || editor.textContent || '').trim().length === 0;
  }

  // ── DM: helpers to message the RIGHT person ─────────────────────────────
  async function closeAllMessageOverlays() {
    // The chat-window close button (class msg-overlay-bubble-header__control)
    // has NO aria-label — only the TEXT "Close your conversation …". Match on
    // text (+ aria/title as backup) and loop until every window is gone. This
    // is what finally clears the leftover-window pileup that funneled every
    // message into one chat.
    //
    // Minimized pills: a collapsed conversation renders NO close control at
    // all, so earlier cleanup passes left one pill behind every time. Clicking
    // the pill's header restores the window, which makes the close button
    // appear on the next pass. (The Messaging LIST bubble is not a
    // conversation bubble, so it never matches and stays untouched.)
    for (let pass = 0; pass < 8; pass++) {
      const pills = deepQuerySelectorAll(
        '.msg-overlay-conversation-bubble--is-minimized [class*="bubble-header"], '
        + '.msg-overlay-conversation-bubble[class*="minimized"] [class*="header"]');
      let expanded = 0;
      for (const h of pills) {
        try { h.click(); expanded++; } catch { /* pill already gone */ }
      }
      if (expanded) { LOG(`Expanded ${expanded} minimized chat pill(s).`); await sleep(700); }

      const textCloser = (b) => {
        const hay = ((b.innerText || '') + ' ' + (b.getAttribute('aria-label') || '')
          + ' ' + (b.getAttribute('title') || '')).toLowerCase();
        return hay.includes('close your conv') || hay.includes('close conversation');
      };
      const iconCloser = (b) => !!b.querySelector(
        'svg[data-test-icon*="close"], svg[data-test-icon*="cancel"], '
        + 'use[href*="close"], li-icon[type*="cancel"]');
      const closers = deepQuerySelectorAll('button, [role="button"]').filter(textCloser);
      // The NEW large chat pane's close control carries no matching text —
      // only an icon. Search for icon closers INSIDE each known conversation
      // container (anchored on its editor), so we never close unrelated UI.
      for (const c of findConversationContainers()) {
        const btn = [...c.querySelectorAll('button, [role="button"]')]
          .find((b) => textCloser(b) || iconCloser(b));
        if (btn && !closers.includes(btn)) closers.push(btn);
      }
      if (!closers.length && !expanded) break;
      closers.forEach((b) => { try { b.click(); } catch {} });
      await sleep(500);
    }
  }
  function findProfileMessageButton() {
    // Top-card Message button has aria-label "Message <Name>"; sidebar-rail
    // buttons are plain "Message". Prefer the labelled top-card one, earliest in
    // <main>, so we never open a conversation for the wrong (sidebar) person.
    const main = document.querySelector('main') || document;
    const labelled = [...main.querySelectorAll('button, a')].find((el) =>
      /^message\s+\S/i.test(el.getAttribute('aria-label') || ''));
    if (labelled) return labelled;
    return [...main.querySelectorAll('button, a')].find((el) =>
      /^message$/i.test((el.innerText || '').trim())) || null;
  }
  // The profile top-card Message control is an <a> pointing at
  //   /messaging/compose/?...recipient=<ENCODED-URN>...screenContext=NON_SELF_PROFILE_VIEW
  // (the URL is in data-original-url and/or href). That encoded URN (ACoAA…) is
  // the recipient's GROUND-TRUTH id. We use it to (a) click the correct control
  // and (b) positively identify the chat window it opens — immune to display-
  // name confusion (a leftover "Kamalesh" window cannot carry Shivendra's URN).
  // Discovered live 2026-06-15 from the inspected DOM.
  function profileMessageControl() {
    const main = document.querySelector('main') || document;
    const urlOf = (a) => a.getAttribute('data-original-url') || a.getAttribute('href') || '';
    const links = [...main.querySelectorAll(
      'a[data-original-url*="/messaging/compose"], a[href*="/messaging/compose"]')];
    // Prefer the control explicitly tagged as the profile-view compose overlay.
    const pick = links.find((a) => /NON_SELF_PROFILE_VIEW/.test(urlOf(a)))
      || links.find((a) => /recipient=/.test(urlOf(a)))
      || links[0] || null;
    if (!pick) return null;
    const m = urlOf(pick).match(/recipient=([A-Za-z0-9_-]+)/);
    return { el: pick, urn: m ? m[1] : '' };
  }
  function dmNamesMatch(a, b) {
    const norm = (s) => (s || '').toLowerCase().replace(/[^a-z ]/g, ' ').replace(/\s+/g, ' ').trim();
    a = norm(a); b = norm(b);
    if (!a || !b) return false;          // can't determine → NO match (fail-closed)
    const at = a.split(' '), bt = b.split(' ');
    // When BOTH sides carry a full name, require two shared tokens (first AND
    // last) — first-token-only equality let "Aditya <anyone>" match "Aditya
    // Jha", which is exactly the wrong-person class this guard exists to stop.
    const overlap = at.filter((t) => t.length > 1 && bt.includes(t)).length;
    if (at.length >= 2 && bt.length >= 2) return overlap >= 2;
    return at[0] === bt[0] || at.some((t) => t.length > 2 && bt.includes(t));
  }
  // Find the conversation window that belongs to the TARGET, matched by its OWN
  // header name / readable vanity. Each window is checked independently, so a
  // leftover window for someone else can never be mistaken for the target's —
  // we then type into and send from ONLY this window. This is the definitive
  // fix for "messages all landed in one person's chat".
  // The logged-in account's display name — the NEW large chat pane renders it
  // as its top header, which the old "first title wins" reader returned as
  // "the recipient" (observed live: every container identified as the OWN
  // account, so no target could ever match). Skip it.
  function accountOwnName() {
    const el = document.querySelector(
      'img.global-nav__me-photo[alt], .global-nav__me img[alt], '
      + 'header img[alt][class*="me-photo"]');
    return (el?.getAttribute('alt') || '').trim();
  }

  // ALL candidate recipient names inside `scope`, own-account name excluded.
  // Names sitting inside a conversation LIST row are EXCLUDED: the large
  // centred messaging pane wraps the active thread AND the inbox list in ONE
  // container, so list names made ANY queued target "positively match" the
  // pane while its single composer belonged to whichever thread happened to
  // be open — every DM funneled into one person's chat (observed live
  // 2026-06-12, "all DMs went to one person").
  const CONV_LIST_SEL = 'li, [role="listitem"], [class*="listitem"], '
    + '[class*="conversation-list"], [class*="conversations-container"]';
  function collectNamesIn(scope) {
    const own = accountOwnName();
    const out = [];
    const push = (t) => {
      t = (t || '').trim();
      if (!t || /^messaging\b/i.test(t) || /^you are on/i.test(t)) return;
      if (own && dmNamesMatch(t, own)) return;       // never "match" ourselves
      if (!out.includes(t)) out.push(t);
    };
    const inList = (el) => !!(el.closest && el.closest(CONV_LIST_SEL));
    const sel = '[class*="bubble-header"] [class*="title"], [class*="header"] [class*="title"], '
      + '.msg-overlay-bubble-header__title, [data-test-conversation-title], '
      + '.msg-entity-lockup__entity-title, h1, h2, h3';
    scope.querySelectorAll(sel).forEach((el) => { if (!inList(el)) push(el.innerText); });
    scope.querySelectorAll('a[href*="/in/"]').forEach((a) => {
      if (!inList(a)) push(a.innerText || a.querySelector('img')?.getAttribute('alt'));
    });
    scope.querySelectorAll('img[alt]').forEach((img) => { if (!inList(img)) push(img.getAttribute('alt')); });
    return out.slice(0, 8);
  }

  // Recipient names for ONE editor, resolved from its NEAREST named ancestor.
  // Walking outward (through shadow hosts) and stopping at the FIRST ancestor
  // that yields any name keeps the scope to this editor's own chat bubble /
  // thread column — names from sibling threads or the inbox list can't leak in.
  function namesNearEditor(editor) {
    let node = editor.parentElement
      || ((editor.getRootNode && editor.getRootNode().host) || null);
    while (node) {
      const names = collectNamesIn(node);
      if (names.length) return names;
      node = node.parentElement
        || ((node.getRootNode && node.getRootNode().host) || null);
    }
    return [];
  }
  // Discover every open conversation CONTAINER by anchoring on its editor and
  // walking up to the nearest plausible wrapper. LinkedIn now renders chats in
  // (at least) two shapes: the small corner bubble (.msg-overlay-conversation-
  // bubble) and a LARGE centred pane (seen live 2026-06-12) that the old
  // bubble-only selector never matched — every DM failed-closed with wins=0 /
  // name-mismatch even though the right chat was open on screen.
  // closest() stops at shadow boundaries — walk up through hosts too, so an
  // editor nested in a shadow root still finds its outer conversation wrapper.
  function composedClosest(el, selector) {
    let node = el;
    while (node) {
      const hit = node.closest && node.closest(selector);
      if (hit) return hit;
      const root = node.getRootNode && node.getRootNode();
      node = (root && root.host) ? root.host : null;
    }
    return null;
  }
  const CONV_EDITOR_SEL = '.msg-form__contenteditable, div[role="textbox"][contenteditable], '
    + 'div[contenteditable], textarea[name="message"]';
  function findConversationEditors() {
    return deepQuerySelectorAll(CONV_EDITOR_SEL);
  }
  function findConversationContainers() {
    const containers = [];
    for (const ed of findConversationEditors()) {
      const c = composedClosest(ed,
        '.msg-overlay-conversation-bubble, .msg-convo-wrapper, .msg-thread, '
        + '[role="dialog"], [aria-modal="true"], aside, section')
        || ed.parentElement;
      if (c && !containers.includes(c)) containers.push(c);
    }
    return containers;
  }
  // Does this conversation container belong to the recipient identified by
  // `urn` (the encoded ACoAA… id from the profile's Message compose link)?
  // Thread avatars / profile links inside the open chat reference the recipient
  // by that SAME encoded vanity, so an exact URN hit is unambiguous — unlike a
  // display name, a leftover "Kamalesh" window cannot carry Shivendra's URN.
  function containerHasUrn(scope, urn) {
    if (!urn || !scope) return false;
    let hits;
    try {
      hits = [...scope.querySelectorAll(
        `a[href*="${urn}"], [data-original-url*="${urn}"], [href*="${urn}"]`)];
    } catch { return false; }
    // CRITICAL: exclude hits inside the inbox conversation LIST. The big centred
    // messaging pane bundles the list (which links to EVERYONE, including this
    // target) together with the single open thread. A list hit would vouch for
    // whatever thread happens to be open → "all DMs to one person". Only a hit
    // in the open thread's OWN header/body (outside any list row) counts, so if
    // the target's thread isn't actually open we get NO match and abort.
    return hits.some((el) => !(el.closest && el.closest(CONV_LIST_SEL)));
  }
  function findConversationFor(targetName, targetUrn) {
    // Identify the TARGET's own chat window, judged PER EDITOR. Two passes:
    //   1. URN match — ground truth, immune to name confusion (Kamalesh).
    //   2. positive NAME match — fallback when the URN isn't rendered in the DOM.
    // Returns the matched editor itself (typing/sending uses exactly it). There
    // is deliberately NO "use the only open window" fallback: if we can't
    // positively confirm the recipient, we abort — a wrong-person send stays
    // structurally impossible.
    if (!targetName && !targetUrn) return null;
    const winOf = (ed) => composedClosest(ed,
      '.msg-overlay-conversation-bubble, .msg-convo-wrapper, .msg-thread, '
      + '[role="dialog"], [aria-modal="true"], aside, section')
      || ed.parentElement;
    if (targetUrn) {
      for (const ed of findConversationEditors()) {
        const win = winOf(ed);
        if (win && containerHasUrn(win, targetUrn)) {
          return { editor: ed, win, names: namesNearEditor(ed), via: 'urn' };
        }
      }
    }
    if (targetName) {
      for (const ed of findConversationEditors()) {
        const names = namesNearEditor(ed);
        if (names.some((n) => dmNamesMatch(n, targetName))) {
          return { editor: ed, win: winOf(ed), names, via: 'name' };
        }
      }
    }
    return null;
  }

  // Resolve the profile's display name from several independent sources — the
  // whole DM flow keys off a positive name match, so an empty name used to
  // guarantee a dm_target_window_not_found / profile_dom_mismatch abort even
  // when everything else worked. Sources in order of trust:
  //   1. the HEALABLE 'profileName' selector (registry → AI-healed top-card h1)
  //   2. document.title ("(3) John Doe | LinkedIn") — clean and reliable
  //   3. aria-label of the top-card Message / Invite controls (last resort)
  //
  // (1) routes through the selector registry so the watchdog can heal it in
  // real time when LinkedIn drifts (observed live: h1="none", and the old
  // aria-scan grabbing a "with Premium" badge instead of the name). The result
  // is always re-validated against the target slug by the caller, so a stale
  // value is rejected rather than acted on.
  function resolveProfileName() {
    // 1. healable profile-name element (shadow-DOM aware)
    const reg = registry['profileName'];
    for (const sel of [reg && reg.primary, reg && reg.fallback]) {
      if (!sel) continue;
      try {
        const n = (deepQuerySelector(sel)?.innerText || '').trim();
        if (n) return n;
      } catch { /* invalid selector for this context */ }
    }
    // 2. document.title — survives top-card class obfuscation entirely
    const t = (document.title || '').replace(/^\(\d+\)\s*/, '');
    const tm = t.match(/^(.+?)\s*[|\-–]\s*LinkedIn/i);
    if (tm && tm[1].trim()) return tm[1].trim();
    // 3. aria-label of the top-card Message / Invite controls. Scope to the
    // TOP CARD (first section of main) — the "People also viewed" rails carry
    // "Message <other person>" buttons that an unscoped scan would mis-resolve.
    const main = document.querySelector('main section')
              || document.querySelector('main')
              || document;
    for (const el of main.querySelectorAll('button, a')) {
      const aria = el.getAttribute('aria-label') || '';
      const m = aria.match(/^message\s+(.+)$/i)
        || aria.match(/^invite\s+(.+?)\s+to connect/i);
      if (m && m[1].trim()) return m[1].trim();
    }
    return '';
  }

  // Hard page-identity guard: the vanity in the address bar MUST match the
  // command's target URL. The background normally navigates the tab first, but
  // its unresponsive-tab fallback can dispatch to a tab sitting on a DIFFERENT
  // page (observed live: cmd targeting one person executed on another's page —
  // resolveProfileName returned the WRONG person's name, and every downstream
  // "safety" check would have validated against that wrong name). Never act on
  // a page whose vanity doesn't match the command.
  function vanityOf(url) {
    const m = (url || '').match(/\/in\/([^/?#]+)/);
    return m ? m[1].toLowerCase() : '';
  }
  function assertOnTargetProfile(cmd) {
    const want = vanityOf(cmd.target_li_url);
    const have = vanityOf(location.href);
    if (want && have !== want) {
      throw new Error(`wrong_profile_page [want=${want} have=${have || 'none'}]`);
    }
  }

  // URL matching alone is NOT enough: during an SPA transition the address bar
  // already shows the target while the DOM still renders the PREVIOUS page
  // (observed live — a command resolved the prior page's person as "the
  // target" and would have validated everything against the wrong name). So
  // before acting we also require the rendered profile name to plausibly match
  // the target slug. Letters-only normalization handles both slug styles:
  // 'vimarshdwivedi' ↔ "Vimarsh Dwivedi", 'aditya-jha-36193a251' ↔ "Aditya Jha".
  function nameMatchesSlug(name, slug) {
    const letters = (s) => (s || '').toLowerCase().replace(/[^a-z]/g, '');
    const n = letters(name), s = letters(slug);
    if (!n || !s) return false;
    if (s.includes(n) || n.includes(s)) return true;
    const tokens = (name || '').toLowerCase().split(/[^a-z]+/).filter(t => t.length > 1);
    return tokens.length > 0 && tokens.every(t => s.includes(t));
  }
  async function waitForTargetProfileDom(cmd, timeoutMs = 9000) {
    const want = vanityOf(cmd.target_li_url);
    const name = await waitFor(() => {
      const n = resolveProfileName();
      return (n && (!want || nameMatchesSlug(n, want))) ? n : null;
    }, timeoutMs).catch(() => null);
    if (!name) {
      const seen = resolveProfileName();
      const h1 = (document.querySelector('main h1')?.innerText || '').trim();
      throw new Error(
        `profile_dom_mismatch [want=${want} domName="${seen || 'none'}" `
        + `h1="${h1 || 'none'}" url=${location.pathname}]`,
      );
    }
    return name;
  }

  // ── DM execution ────────────────────────────────────────────────────────
  async function executeDm(cmd) {
    await waitFor(() => document.querySelector('main'));

    // Fast-path: LinkedIn's Message button sometimes does a full-page navigation
    // to /messaging/thread/new?... instead of opening a modal overlay. When that
    // happens a later executeWithSelfHeal retry arrives with the URL already on
    // the messaging compose page and assertOnTargetProfile throws
    // wrong_profile_page [have=none]. Detect this and skip to the composer
    // directly — we're already on the right page.
    const onMessaging = location.pathname.startsWith('/messaging/');
    let targetName, targetUrn;
    if (onMessaging) {
      // Derive approximate display name from the profile vanity slug so
      // findConversationFor can do a name-based match in the compose form.
      const vanity = vanityOf(cmd.target_li_url);
      targetName = vanity.replace(/-\d+$/, '').replace(/-/g, ' ');
      targetUrn = '';
      LOG(`executeDm: messaging page — derived name="${targetName}"`);
    } else {
      assertOnTargetProfile(cmd);

      // Clear ALL leftover floating chat windows from a PREVIOUS DM *first*. They
      // overlay the profile top-card, so the identity check below would otherwise
      // read the messaging UI instead of the person (observed live: shivendra
      // failed profile_dom_mismatch with domName="Messaging" h1="none" right after
      // aditya's DM left an overlay open). Clean the page BEFORE verifying who we're
      // on — this is what makes back-to-back sends to many people reliable.
      await closeAllMessageOverlays();
      await sleep(500);

      // Who we must message — verified against the open conversation before
      // typing. Waits for the rendered profile to actually BE the target (URL
      // alone lies mid-transition); throws profile_dom_mismatch otherwise.
      targetName = await waitForTargetProfileDom(cmd);

      // Resolve the precise top-card Message control AND the recipient's encoded
      // URN from its compose href. The URN positively identifies the chat window
      // that opens, even if a leftover window for someone else is present.
      const control = profileMessageControl();
      targetUrn = (control && control.urn) || '';
      // Click the MAIN profile Message control (the compose <a>), never a sidebar
      // one. Fall back to the labelled top-card button, then registry/heal.
      const msgBtn = (control && control.el)
        || findProfileMessageButton()
        || await waitFor(() => findResilient('messageButton', /^message$/i), COMMAND_TIMEOUT_MS);
      if (!msgBtn) throw new Error('messageButton_not_found_even_after_heal');
      msgBtn.click();
      LOG(`Clicked Message for "${targetName}" (urn=${targetUrn || 'n/a'}) — waiting for composer dialog…`);
    }

    // Wait for the dialog to appear and finish animating in. LinkedIn renders
    // the "New message" composer inside #interop-outlet's open shadow DOM, so
    // we wait for either: the shadow root to have a msg-form, or a dialog
    // wrapper in the main doc.
    await waitFor(
      () => deepQuerySelector('.msg-form, form.msg-form, [role="dialog"], [aria-modal="true"]'),
      10_000,
    ).catch(() => null);
    await sleep(700);

    // Lazy-focus trick: click the placeholder so contenteditable attaches
    await focusComposerArea();
    await sleep(250);

    // ── Find the TARGET's OWN conversation window ───────────────────────
    // Operate ONLY inside the window that belongs to this person (matched by
    // its own header / readable vanity). Even if a leftover window for someone
    // else is open, we never type into or send from it. If the target's window
    // can't be found, we ABORT rather than risk messaging the wrong person.
    const targetVanity = (location.pathname.match(/\/in\/([^/?#]+)/) || [])[1] || '';
    let conv = await waitFor(
      () => findConversationFor(targetName, targetUrn), 7000,
    ).catch(() => null);
    if (!conv) {
      if (!onMessaging) {
        // One clean in-flow retry before aborting: a leftover/minimized window
        // can swallow the first Message click (LinkedIn focuses it instead of
        // opening the target's). Nothing has been typed or sent yet, so this is
        // safe. Clear overlays again, re-click Message, wait once more.
        LOG('Target conversation not found — clearing overlays and re-clicking Message…');
        await closeAllMessageOverlays();
        await sleep(500);
        const againCtl = profileMessageControl();
        const again = (againCtl && againCtl.el) || findProfileMessageButton();
        if (again) {
          again.click();
          await sleep(1200);
          await focusComposerArea();
        }
        conv = await waitFor(
          () => findConversationFor(targetName, targetUrn), 8000,
        ).catch(() => null);
      } else {
        // On the full-page messaging compose, the DOM structure differs from the
        // floating overlay so findConversationFor may not match. Fall back to
        // using the visible editor directly — we navigated here FROM the target's
        // profile so it's guaranteed to be the right recipient.
        const ed = deepQuerySelector(CONV_EDITOR_SEL);
        if (ed) {
          const win = composedClosest(ed,
            '.msg-overlay-conversation-bubble, .msg-convo-wrapper, .msg-thread, '
            + '[role="dialog"], [aria-modal="true"], aside, section')
            || ed.parentElement;
          conv = { editor: ed, win, names: [targetName], via: 'messaging_page' };
          LOG(`executeDm: built conv from messaging page editor directly`);
        }
      }
    }
    if (!conv) {
      // Embed a compact DOM-state snapshot in the error so the backend's
      // activity log shows WHY the conversation wasn't found (no editors at
      // all vs editors whose scoped names mismatch) — debuggable without
      // attaching DevTools to the LinkedIn tab.
      const editors = findConversationEditors();
      const cands = editors
        .map((ed) => namesNearEditor(ed).slice(0, 4).join('+') || 'unnamed')
        .join(' / ');
      const frames = document.querySelectorAll('iframe').length;
      throw new Error(
        `dm_target_window_not_found [target="${targetName}" urn=${targetUrn || 'n/a'} `
        + `eds=${editors.length} frames=${frames} scopes=${collectSearchScopes().length} `
        + `cands=${cands || 'none'} own="${accountOwnName() || '?'}"]`,
      );
    }
    const { editor, win } = conv;
    LOG(`Target conversation confirmed for "${targetName}" (names: ${conv.names.slice(0, 3).join(', ')}).`);
    if (!editor.isConnected) throw new Error('composeEditor_not_found_even_after_heal');

    // ── Text insertion (controlled-editor aware), scoped to this window ──
    editor.click();
    editor.focus();
    await sleep(150);
    if (editor.tagName === 'TEXTAREA' || editor.tagName === 'INPUT') {
      // Use the prototypes from the editor's OWN window (it may be an iframe's).
      const w = editor.ownerDocument?.defaultView || window;
      const proto = editor.tagName === 'TEXTAREA' ? w.HTMLTextAreaElement.prototype : w.HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, 'value').set.call(editor, cmd.body_text);
      editor.dispatchEvent(new Event('input',  { bubbles: true, composed: true }));
      editor.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
    } else {
      insertTextIntoEditor(editor, cmd.body_text);
    }
    await sleep(400);
    if (isComposerEmpty(editor)) throw new Error('composer_remained_empty_after_insertion');
    LOG('Composer body length:', (editor.innerText || '').trim().length);

    // ── Send — every handle (form, button) taken from THIS window only ──
    // Use the editor's OWN realm for constructors/events: in the iframe-hosted
    // pane, top-realm synthetic events can be ignored by the pane's framework.
    const EW = editor.ownerDocument?.defaultView || window;
    const form = editor.closest('form, .msg-form') || win.querySelector('form, .msg-form');
    const findSendBtn = () => {
      const direct = win.querySelector('button.msg-form__send-button, button[type="submit"]');
      if (direct) return direct;
      return [...win.querySelectorAll('button, [role="button"]')].find((b) => {
        const t = (b.innerText || '').trim().toLowerCase();
        const a = (b.getAttribute('aria-label') || '').toLowerCase();
        if (t === 'send' || a === 'send' || a.startsWith('press enter to send')
            || a === 'send message' || /^send\b/.test(a) || /^send\b/.test(t)) return true;
        // Icon-only send control in the new pane.
        return !!b.querySelector('svg[data-test-icon*="send"], use[href*="send"]');
      }) || null;
    };
    let sendBtn = findSendBtn();
    // Wait up to ~8s for the controller to enable Send after registering text.
    for (let i = 0; i < 26 && sendBtn && (sendBtn.disabled || sendBtn.getAttribute('aria-disabled') === 'true'); i++) {
      await sleep(300);
      sendBtn = findSendBtn() || sendBtn;
    }
    const sendState = () =>
      `btn=${sendBtn ? `"${(sendBtn.getAttribute('aria-label') || sendBtn.innerText || sendBtn.className || '?').toString().slice(0, 40)}"` : 'none'} `
      + `dis=${sendBtn ? (sendBtn.disabled || sendBtn.getAttribute('aria-disabled') === 'true') : '-'} form=${!!form}`;
    LOG('Send state:', sendState());
    // ── Send EXACTLY ONCE ───────────────────────────────────────────────
    // Each mechanism is followed by a POLL for delivery confirmation; we
    // escalate to the next one ONLY if the message has not gone out yet.
    // Confirmation = composer cleared OR our text now appears as a thread
    // message. The new pane keeps the composer text rendered even AFTER a
    // successful send, so the old `isComposerEmpty`-only guard let (b) and (c)
    // fire as DUPLICATES — observed live: the same DM sent 4× to one person.
    const sentYet = () =>
      !editor.isConnected || !win.isConnected
      || isComposerEmpty(editor) || dmAppearsSent(win, editor, cmd.body_text);
    const confirmSent = async (ms) => {
      const deadline = Date.now() + ms;
      while (Date.now() < deadline) {
        if (sentYet()) return true;
        await sleep(250);
      }
      return sentYet();
    };
    const SENT = () => ({ providerMessageId: `li-dm:${targetVanity}#${Date.now()}` });

    // (a) native click + realm-correct mouse sequence
    if (sendBtn) {
      try {
        sendBtn.click();
        ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach((t) =>
          sendBtn.dispatchEvent(new EW.MouseEvent(t, { bubbles: true, cancelable: true, composed: true, view: EW })));
        LOG('Clicked Send (native + mouse sequence).');
      } catch (e) { WARN('Send click failed:', e?.message || e); }
    }
    if (await confirmSent(3500)) { LOG('Send confirmed after click — NOT escalating.'); return SENT(); }

    // (b) form.requestSubmit() — only because (a) did NOT deliver
    if (form && typeof form.requestSubmit === 'function') {
      try { form.requestSubmit(sendBtn && sendBtn.type === 'submit' ? sendBtn : undefined); LOG('Send via form.requestSubmit().'); }
      catch (e) { /* fall through */ }
    }
    if (await confirmSent(3000)) { LOG('Send confirmed after requestSubmit.'); return SENT(); }

    // (c) Enter-to-send — only because (a) and (b) did NOT deliver
    try {
      editor.focus();
      const opts = { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true, composed: true };
      editor.dispatchEvent(new EW.KeyboardEvent('keydown', opts));
      editor.dispatchEvent(new EW.KeyboardEvent('keyup', opts));
      LOG('Tried Enter-to-send.');
    } catch (e) { /* best-effort */ }
    if (await confirmSent(3000)) { LOG('Send confirmed after Enter.'); return SENT(); }

    throw new Error(`send_click_was_noop_composer_still_has_text [${sendState()}]`);
  }

  // TRUE iff `body` appears as message CONTENT in this conversation, outside
  // the compose form. Guards the FAILURE path only: a send that worked but
  // left the composer text rendered must not be reported failed — the
  // watchdog's fragility retry would send the same DM again.
  function dmAppearsSent(win, editor, body) {
    const probe = (body || '').replace(/\s+/g, ' ').trim().slice(0, 60);
    if (probe.length < 8) return false;
    const form = composedClosest(editor, 'form, .msg-form');
    for (const el of win.querySelectorAll('p, span, [class*="event"], [class*="message"]')) {
      if (el.contains(editor) || editor.contains(el)) continue;
      if (form && (form.contains(el) || el.contains(form))) continue;
      const t = (el.innerText || '').replace(/\s+/g, ' ');
      if (t.includes(probe)) return true;
    }
    return false;
  }

  // ── Connect: locate the REAL profile connect control ────────────────────
  // LinkedIn's new profile UI renders the top-card "Connect" as an <a> (not a
  // <button>) that links to /preload/custom-invite/?vanityName=<profile>. The
  // page also has many <button>Connect</button> controls in the "People also
  // viewed / People similar" rails. Matching `button[aria-label^="Invite "]`
  // grabs a RAIL button and opens the wrong (or no) modal. We instead match the
  // invite control whose href carries THIS profile's vanityName — bulletproof
  // and immune to the rails.
  function profileVanity() {
    const m = location.pathname.match(/\/in\/([^/]+)/);
    return m ? m[1] : null;
  }

  function findConnectControl() {
    const vanity = profileVanity();
    // 1. The top-card invite anchor: href contains custom-invite + this vanity.
    if (vanity) {
      const byHref = deepQuerySelectorAll('a[href*="custom-invite"], a[href*="vanityName"]')
        .find(a => (a.getAttribute('href') || '').includes(vanity));
      if (byHref) return byHref;
    }
    // 2. Any <a>/<button> labelled "Invite <name> to connect". The rails also
    //    carry Invite buttons (for OTHER people), so: prefer one whose
    //    aria-label contains THIS profile's name, then one inside <main> and
    //    outside <aside>, earliest in DOM (top card precedes the rails).
    const invites = [
      ...deepQuerySelectorAll('a[aria-label^="Invite "]'),
      ...deepQuerySelectorAll('button[aria-label^="Invite "]'),
    ].filter(el => !el.closest('aside'));
    if (invites.length) {
      const name = resolveProfileName();
      const first = (name.split(' ')[0] || '').toLowerCase();
      if (first.length > 1) {
        const named = invites.find(el =>
          (el.getAttribute('aria-label') || '').toLowerCase().includes(first));
        if (named) return named;
      }
      const inMain = invites.find(el => el.closest('main'));
      if (inMain) return inMain;
      return invites[0];
    }
    // 3. Plain-text "Connect" control — scoped to <main> and outside <aside>,
    //    earliest in DOM, so a rail button can never be picked.
    const scope = document.querySelector('main') || document;
    const byText = [...scope.querySelectorAll('a, button, [role="button"]')].find(el =>
      /^connect$/i.test((el.innerText || el.textContent || '').trim())
      && !el.closest('aside'),
    );
    return byText || null;
  }

  // ── Connect: locate the right "send" button for each flow ───────────────
  // LinkedIn's invite modal has two distinct shapes:
  //   • no-note  → buttons are "Add a note" + "Send without a note" (or "Send")
  //   • with-note→ after clicking "Add a note", buttons are the note textarea +
  //                "Send invitation" (or "Send")
  // We score candidate buttons so we pick the genuine send action and NEVER the
  // "Add a note" button. Scoped to the open dialog so we don't grab a stray
  // "Send" button elsewhere on the page.
  function findConnectSendButton(withNote) {
    const dialog = deepQuerySelector('[data-test-modal], .send-invite-modal, [role="dialog"], [aria-modal="true"], .artdeco-modal');
    const buttons = dialog ? [...dialog.querySelectorAll('button')] : deepQuerySelectorAll('button');
    const score = (b) => {
      const text = (b.innerText || b.textContent || '').trim().toLowerCase();
      const aria = (b.getAttribute('aria-label') || '').trim().toLowerCase();
      const hay = `${text} | ${aria}`;
      if (/add a note/.test(hay)) return -1;            // never the send button
      if (withNote) {
        if (/send invitation/.test(hay)) return 100;
        if (text === 'send' || aria === 'send') return 80;
        if (/\bsend\b/.test(hay)) return 60;
      } else {
        if (/send without a note/.test(hay)) return 100;
        if (/send now/.test(hay)) return 90;
        if (text === 'send' || aria === 'send') return 80;
        if (/\bsend\b/.test(hay)) return 60;
      }
      return -1;
    };
    let best = null, bestScore = 0;
    for (const b of buttons) {
      const s = score(b);
      if (s > bestScore) { best = b; bestScore = s; }
    }
    return best;
  }

  // Confirm the invite actually went out. On success LinkedIn closes the modal
  // and usually shows an "Invitation sent" toast / flips the profile to
  // "Pending". If instead a modal lingers (e.g. the weekly invite-limit
  // dialog), it was NOT sent — return false so we surface a real failure.
  async function verifyConnectSent() {
    const deadline = Date.now() + 4000;
    while (Date.now() < deadline) {
      const dialogGone = !deepQuerySelector('[data-test-modal], .send-invite-modal, [role="dialog"], [aria-modal="true"], .artdeco-modal');
      const toast = deepQuerySelectorAll('[role="alert"], .artdeco-toast-item, .artdeco-toast-item__message')
        .some(el => /invitation sent|invite sent|sent your invitation/i.test(el.innerText || ''));
      const pending = !!findButtonByText(/^(pending|invitation sent)$/i);
      if (dialogGone || toast || pending) return true;
      await sleep(300);
    }
    return false;
  }

  // Read the profile top-card's primary action. The top card precedes the
  // "More profiles" rail in DOM, so the FIRST connect/pending/message control is
  // the one for THIS profile. Lets us detect an already-invited (Pending) or
  // already-connected profile and avoid timing out trying to re-connect.
  function topCardState() {
    const main = document.querySelector('main') || document;
    const btns = [...main.querySelectorAll('button, a')];
    for (const b of btns) {
      const t = (b.innerText || '').trim().toLowerCase();
      const aria = (b.getAttribute('aria-label') || '').toLowerCase();
      if (t === 'pending' || /^pending\b/.test(aria)) return 'pending';
      if (t === 'connect' || /^invite .* to connect$/.test(aria)) return 'connect';
      if (t === 'message' && !btns.some(x => /^connect$/i.test((x.innerText || '').trim()))) {
        // Message present with no Connect anywhere → already 1st-degree.
        return 'connected';
      }
    }
    return null;
  }

  // ── Like posts execution ─────────────────────────────────────────────────
  // Visits the profile's recent-activity feed and likes up to 2 posts that the
  // current user hasn't already liked. Safe: never likes own posts, skips any
  // post that is already in a liked/reacted state.
  async function executeLike(cmd) {
    await waitFor(() => document.querySelector('main'));
    // ensureOnTarget already navigated us to /recent-activity/all/ — wait for
    // the feed to populate (try both old and new LinkedIn feed class names)
    await waitFor(
      () => document.querySelector(
        '.feed-shared-update-v2, .occludable-update, [data-urn], .scaffold-finite-scroll__content'
      ), 12_000
    ).catch(() => null);
    await sleep(2000);

    const MAX_LIKES = 2;
    let liked = 0;

    // Collect all reaction/like buttons on the page — LinkedIn's aria-label
    // varies: "Like", "Like Vimarsh's post", "React Like", etc. Match anything
    // containing "Like" that isn't already pressed.
    const allBtns = Array.from(document.querySelectorAll('button[aria-label]'));
    const likeBtns = allBtns.filter(btn => {
      const label = btn.getAttribute('aria-label') || '';
      const pressed = btn.getAttribute('aria-pressed');
      // Must contain "Like" (case-insensitive), must NOT already be pressed/reacted
      return /like/i.test(label) && pressed !== 'true';
    });

    LOG(`executeLike: found ${likeBtns.length} likeable buttons on page`);

    for (const btn of likeBtns) {
      if (liked >= MAX_LIKES) break;
      btn.click();
      await sleep(900 + Math.random() * 700);
      liked++;
      LOG(`executeLike: liked post ${liked}/${MAX_LIKES}`);
    }

    if (liked === 0) {
      LOG('executeLike: no unliked posts found — all already liked or page empty');
    }
    return { providerMessageId: `liked:${liked}`, liked };
  }

  // ── Connect execution ───────────────────────────────────────────────────
  async function executeConnect(cmd) {
    await waitFor(() => document.querySelector('main'));
    assertOnTargetProfile(cmd);
    // Same stale-DOM gate as the DM flow — without it, topCardState() can read
    // the PREVIOUS page's "Pending"/"Message" buttons and report a false
    // already-connected success for the wrong person.
    await waitForTargetProfileDom(cmd);
    await sleep(400);

    // Already invited or already connected? That's a success, not a failure —
    // report done so the enrolment advances instead of retrying forever on a
    // profile that has no Connect button.
    const pre = topCardState();
    if (pre === 'pending' || pre === 'connected') {
      LOG(`Profile already ${pre} — treating connect as done.`);
      return { providerMessageId: `li-connect:already-${pre}#${Date.now()}` };
    }

    // Match the real top-card connect control (new <a> UI), not a sidebar rail.
    let connectBtn = findConnectControl();
    if (!connectBtn) {
      // Connect may be tucked under the "More" overflow menu.
      const moreBtn = await findResilient('moreButton', /^more$/i);
      if (moreBtn) {
        moreBtn.click();
        await sleep(600);
        connectBtn = findConnectControl();
      }
    }
    // Last-ditch: registry / AI heal (rarely needed now we match the new UI).
    if (!connectBtn) connectBtn = await findResilient('connectButton', /^connect$/i);
    if (!connectBtn) {
      // No Connect control — re-check whether that's because we're already
      // pending/connected (success) rather than a broken selector (failure).
      if (topCardState() === 'pending' || topCardState() === 'connected') {
        return { providerMessageId: `li-connect:already#${Date.now()}` };
      }
      throw new Error('connectButton_not_found_even_after_heal');
    }
    connectBtn.click();

    // Most flows open the "Add a note to your invitation?" modal; a few send
    // instantly with no modal. Wait briefly for it — if none appears but the
    // profile already flipped to "Pending", the invite is already out.
    const modal = await waitFor(
      () => deepQuerySelector('[data-test-modal], .send-invite-modal, [role="dialog"], [aria-modal="true"], .artdeco-modal'),
      6000,
    ).catch(() => null);
    if (!modal) {
      await sleep(800);
      if (findButtonByText(/^(pending|invitation sent)$/i)) {
        return { providerMessageId: `li-connect:${cmd.target_li_url}#${Date.now()}` };
      }
    }
    await sleep(300);

    // ── Premium-upsell guard ─────────────────────────────────────────────────
    // LinkedIn can show the "You're out of free custom notes" modal at TWO
    // points: (a) immediately after clicking Connect, or (b) after clicking
    // "Add a note". Check right here (before entering any note flow) so we
    // catch case (a) without waiting for a non-existent "Add a note" button.
    async function dismissPremiumPopupIfPresent() {
      const allDialogs = deepQuerySelectorAll('[role="dialog"], [aria-modal="true"], .artdeco-modal');
      const popup = allDialogs.find(el =>
        /out of free custom notes|personalized invites with premium/i.test(el.innerText || '')
      );
      if (!popup) return false;
      LOG('Premium upsell detected — dismissing, will send without note');
      const closeBtn =
        popup.querySelector('button[aria-label="Dismiss"]') ||
        popup.querySelector('button[aria-label="Close"]') ||
        popup.querySelector('button.artdeco-modal__dismiss') ||
        popup.querySelector('button.artdeco-button--circle') ||
        [...popup.querySelectorAll('button')].find(b => {
          const lbl = (b.getAttribute('aria-label') || '').toLowerCase();
          const txt = (b.innerText || b.textContent || '').trim();
          return /dismiss|close/i.test(lbl) || txt === '×' || txt === '✕' || txt === 'X' || (txt === '' && b.querySelector('svg,li-icon'));
        });
      if (closeBtn) { closeBtn.click(); }
      else { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })); }
      await sleep(700);
      // Re-click Connect if closing the popup also dismissed the connect dialog
      if (!findConnectSendButton(false)) {
        LOG('Connect dialog also closed — re-clicking Connect for no-note send');
        const reconnect = findConnectControl() || await findResilient('connectButton', /^connect$/i).catch(() => null);
        if (reconnect) { reconnect.click(); await sleep(800); }
      }
      return true; // premium popup was present
    }

    let withNote = !!cmd.body_text;
    // Case (a): premium popup appeared immediately after clicking Connect
    if (withNote && await dismissPremiumPopupIfPresent()) {
      withNote = false;
    }

    if (withNote) {
      // ── with-note flow ──────────────────────────────────────────────────
      const addNoteBtn = await waitFor(
        () => deepQuerySelector('button[aria-label="Add a note"]')
           || findResilient('addNoteButton', /^add a note$/i),
        8000,
      ).catch(() => null);
      if (!addNoteBtn) throw new Error('addNoteButton_not_found_even_after_heal');
      addNoteBtn.click();
      await sleep(600);

      // Case (b): premium popup appeared after clicking "Add a note"
      if (await dismissPremiumPopupIfPresent()) {
        withNote = false;
      }

      if (withNote) {
        let note = await waitFor(
          () => deepQuerySelector(
            '[data-test-modal] textarea, .send-invite-modal textarea, [role="dialog"] textarea, '
            + 'textarea#custom-message, textarea[name="message"]',
          ),
          5000,
        ).catch(() => null);
        if (!note) note = await findResilient('noteTextarea');
        if (!note) throw new Error('noteTextarea_not_found_even_after_heal');
        note.focus();
        // The note textarea is a controlled component — a direct `note.value = …`
        // assignment is swallowed by the framework's value tracker, leaving Send
        // disabled. Use the native prototype setter (same trick as the DM path)
        // so the tracked value updates and the input event actually registers.
        const valueSetter = Object.getOwnPropertyDescriptor(
          HTMLTextAreaElement.prototype, 'value',
        ).set;
        valueSetter.call(note, cmd.body_text.slice(0, 300));
        note.dispatchEvent(new Event('input',  { bubbles: true, composed: true }));
        note.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
        await sleep(250);
      }
    }

    // Wait for the correct send button to be ENABLED, not merely present.
    // (no-note → "Send without a note"/"Send"; with-note → "Send invitation".)
    // Clicking a disabled button is a silent no-op that would otherwise report
    // a fake success to the backend.
    let sendBtn = await waitFor(() => {
      const b = findConnectSendButton(withNote);
      if (!b) return null;
      return (!b.disabled && b.getAttribute('aria-disabled') !== 'true') ? b : null;
    }, 8000).catch(() => null);
    // Last-ditch: registry / AI-heal path.
    if (!sendBtn) {
      sendBtn = await waitFor(
        () => findResilient('sendInvitationButton', /^send( invitation| now| without a note)?$/i),
        4000,
      ).catch(() => null);
    }
    if (!sendBtn) throw new Error('sendInvitationButton_not_found_even_after_heal');
    if (sendBtn.disabled || sendBtn.getAttribute('aria-disabled') === 'true') {
      throw new Error('sendInvitationButton_disabled_note_didnt_register');
    }
    sendBtn.click();

    // Confirm the invite actually went out (modal closed / toast / Pending).
    if (!(await verifyConnectSent())) {
      throw new Error('connect_send_was_noop_or_rejected');
    }
    return { providerMessageId: `li-connect:${cmd.target_li_url}#${Date.now()}` };
  }

  // ── Realtime self-heal wrapper ──────────────────────────────────────────
  // The user's UX expectation is: "if it fails, the watchdog fixes the scraper
  // in real time and resends". This wrapper implements that loop in the
  // content script (so recovery is instant — no 30s poll, no 1-hour Tier 3
  // wait). On a fragility error it:
  //   1. Reports the failed attempt to the backend watchdog event log
  //      (so the dashboard reflects what's happening live).
  //   2. Identifies which selector intent broke (from the error name).
  //   3. Forces a fresh AI heal for that intent (bypassing the once-per-
  //      session gate so subsequent attempts can heal too).
  //   4. Closes any open dialog so the next attempt starts clean.
  //   5. Sleeps briefly to let LinkedIn settle, then retries inline.
  // Up to 2 retries (so 3 total attempts) — bounded so we don't loop forever
  // on a behavioural regression that selector-healing can't fix.
  const FRAGILITY_TO_INTENT = {
    composer_remained_empty_after_insertion: 'composeEditor',
    composeEditor_not_found_even_after_heal: 'composeEditor',
    sendDmButton_not_enabled_text_didnt_register: 'sendDmButton',
    sendDmButton_not_found_even_after_heal: 'sendDmButton',
    send_click_was_noop_composer_still_has_text: 'sendDmButton',
    messageButton_not_found_even_after_heal: 'messageButton',
    dm_target_window_not_found: 'messageButton',
    dm_target_name_unresolved: 'profileName',
    profile_dom_mismatch: 'profileName',
    connectButton_not_found_even_after_heal: 'connectButton',
    addNoteButton_not_found_even_after_heal: 'addNoteButton',
    noteTextarea_not_found_even_after_heal: 'noteTextarea',
    sendInvitationButton_not_found_even_after_heal: 'sendInvitationButton',
    sendInvitationButton_disabled_note_didnt_register: 'noteTextarea',
  };

  function noteToWatchdog(tier, status, message, extra = {}) {
    // Fire-and-forget: each retry/heal step is mirrored to the backend
    // watchdog so the dashboard shows the realtime recovery sequence.
    try {
      chrome.runtime.sendMessage({
        kind: 'watchdog-note',
        tier, status, message, ...extra,
      });
    } catch (e) { /* extension context already closed — fine */ }
  }

  async function dismissOpenDialog() {
    try {
      const closers = deepQuerySelectorAll('button').filter(b => {
        const aria = (b.getAttribute('aria-label') || '').toLowerCase();
        return aria === 'dismiss' || aria === 'close' ||
               aria.startsWith('close your conversation') ||
               aria.startsWith('close ');
      });
      if (closers.length > 0) closers[0].click();
    } catch (e) { /* best-effort */ }
  }

  async function executeWithSelfHeal(cmd, executor) {
    const MAX_ATTEMPTS = 3;
    let lastErr;
    for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
      try {
        if (attempt > 1) {
          noteToWatchdog(
            'extension_failure', 'issue',
            `cmd#${cmd.id} attempt ${attempt}/${MAX_ATTEMPTS} starting`,
            { cmd_id: cmd.id, attempt },
          );
        }
        const result = await executor(cmd);
        if (attempt > 1) {
          noteToWatchdog(
            'extension_failure', 'healed',
            `cmd#${cmd.id} recovered on attempt ${attempt} after realtime heal`,
            { cmd_id: cmd.id, attempt },
          );
        }
        return result;
      } catch (err) {
        lastErr = err;
        const msg = err?.message || String(err);
        const intent = Object.entries(FRAGILITY_TO_INTENT)
          .find(([key]) => msg.includes(key))?.[1];

        noteToWatchdog(
          'extension_failure', 'issue',
          `cmd#${cmd.id} attempt ${attempt}/${MAX_ATTEMPTS} failed: ${msg.slice(0, 200)}`,
          { cmd_id: cmd.id, attempt, intent: intent || null, error: msg.slice(0, 300) },
        );

        if (!intent || attempt === MAX_ATTEMPTS) {
          WARN(`Attempt ${attempt} failed (${msg}) — no more retries`);
          throw err;
        }

        // wrong_profile_page = the address bar is on a DIFFERENT person than the
        // command targets. That's a genuine navigation fault — the selectors are
        // fine, the PAGE is wrong — so healing is doomed; just settle and retry.
        // NOTE: profile_dom_mismatch is deliberately NOT here. By the time it
        // throws, assertOnTargetProfile has already confirmed the URL matches the
        // target, so the page is RIGHT and it's the name SELECTOR that broke
        // (observed live: h1="none"). That IS healable → fall through to the
        // heal path below (intent 'profileName') so the watchdog fixes it live.
        if (msg.includes('wrong_profile_page')) {
          LOG(`Attempt ${attempt} failed on page identity — retrying after settle (no heal)…`);
          await dismissOpenDialog();
          await sleep(2500);
          continue;
        }

        // Force re-heal: bypass once-per-session cache so the next attempt
        // really does request fresh selectors from Claude Haiku.
        healedThisSession.delete(intent);
        LOG(`Attempt ${attempt} failed; realtime healing intent "${intent}" then retrying…`);
        noteToWatchdog(
          'selector_heal', 'issue',
          `cmd#${cmd.id} requesting realtime heal for ${intent}`,
          { cmd_id: cmd.id, intent, attempt },
        );

        const healed = await requestHealAndWait(intent);
        noteToWatchdog(
          'selector_heal', healed ? 'healed' : 'issue',
          healed
            ? `cmd#${cmd.id} realtime heal for ${intent} succeeded`
            : `cmd#${cmd.id} realtime heal for ${intent} returned no selectors`,
          { cmd_id: cmd.id, intent, healed: !!healed },
        );

        if (!healed) {
          // Selector heal can't fix behavioural regressions (controller
          // state, paste handler removed, etc.). Try one more attempt
          // without heal — the form.requestSubmit / Enter fallbacks may
          // still work — but bail if even that fails.
          if (attempt === MAX_ATTEMPTS - 1) throw err;
        }

        await dismissOpenDialog();
        await sleep(1500);
      }
    }
    throw lastErr;
  }

  // ── Message handler from background ────────────────────────────────────
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    (async () => {
      // Background pushes a heal-result back when an out-of-band heal completes
      if (msg.kind === 'apply-heal' && msg.intent && msg.selectors) {
        registry[msg.intent] = {
          primary: msg.selectors[0],
          fallback: msg.selectors[1] || null,
          failCount: 0,
        };
        await saveRegistry();
        LOG(`Applied out-of-band heal for ${msg.intent}`);
        sendResponse({ ok: true });
        return;
      }

      // Background patrol trigger — fire-and-forget (the session can take
      // minutes; do NOT block sendResponse on it or the SW message times out).
      if (msg.kind === 'RUN_PATROL_SESSION') {
        runPatrolSession().catch((e) => LOG('patrol session error', e));
        sendResponse({ ok: true });
        return;
      }

      if (msg.kind !== 'execute-command') {
        sendResponse({ ok: false, error: 'unknown_message_kind' });
        return;
      }

      const cmd = msg.cmd;
      try {
        // For like_posts, navigate straight to the activity feed URL so the
        // single background retry lands on the page that executeLike expects.
        const navTarget = cmd.command_type === 'like_posts'
          ? cmd.target_li_url.replace(/\/$/, '') + '/recent-activity/all/'
          : cmd.target_li_url;
        const nav = await ensureOnTarget(navTarget);
        if (nav.navigated) {
          // Page is reloading — background will retry next poll cycle
          sendResponse({ ok: true, navigating: true });
          return;
        }
        let result;
        if (cmd.command_type === 'dm')           result = await executeWithSelfHeal(cmd, executeDm);
        else if (cmd.command_type === 'connect') result = await executeWithSelfHeal(cmd, executeConnect);
        else if (cmd.command_type === 'like_posts') result = await executeLike(cmd);
        else throw new Error(`unknown_command_type:${cmd.command_type}`);

        // Build fingerprint appended to every result: lets the backend prove
        // WHICH content-script version executed a command (the page console is
        // the only other place the marker shows, and nobody is watching it).
        chrome.runtime.sendMessage({
          kind: 'command-result',
          cmdId: cmd.id,
          status: 'done',
          providerMessageId: `${result.providerMessageId}|${COHERENT_CS_BUILD}`,
        });
        sendResponse({ ok: true });
      } catch (e) {
        WARN('execute failed:', e);
        const errMsg = `${String(e && e.message ? e.message : e)} [cs=${COHERENT_CS_BUILD}]`;
        chrome.runtime.sendMessage({
          kind: 'command-result',
          cmdId: cmd.id,
          status: 'failed',
          error: errMsg,
        });
        sendResponse({ ok: false, error: errMsg });
      }
    })();
    return true;  // async sendResponse
  });

  // ── Inbox scan for inbound DMs ──────────────────────────────────────────
  const seenMessageIds = new Set();
  async function scanInbox() {
    if (!location.pathname.startsWith('/messaging/')) return;

    // Left panel conversation list uses .msg-conversation-card__message-snippet
    // (NOT .msg-s-event-listitem__body which is used by the open thread on the right).
    // Each snippet lives inside a .msg-conversation-listitem which contains the
    // thread link a.msg-conversation-listitem__link with href="/messaging/thread/...".
    const snippetEls = Array.from(document.querySelectorAll('.msg-conversation-card__message-snippet'));

    LOG(`scanInbox: found ${snippetEls.length} conversation snippets`);
    const replies = [];
    for (const bodyEl of snippetEls.slice(0, 15)) {
      const listitem = bodyEl.closest('.msg-conversation-listitem');
      if (!listitem) continue;
      const snippet = (bodyEl.innerText || '').trim();
      // Skip outbound messages — LinkedIn prefixes them with "You:"
      if (!snippet || /^you\s*:/i.test(snippet)) continue;
      const from_name = (
        listitem.querySelector('.msg-conversation-card__participant-names')?.innerText?.trim() ||
        listitem.querySelector('.msg-conversation-listitem__participant-names')?.innerText?.trim() ||
        null
      );
      // thread_id is optional — LinkedIn no longer exposes it in the left-panel DOM.
      // Use name+snippet for deduplication; backend matches enrolment by from_name.
      const message_id = `li:${(from_name || 'unknown').slice(0, 40)}:${snippet.slice(0, 80)}`;
      if (seenMessageIds.has(message_id)) continue;
      seenMessageIds.add(message_id);
      replies.push({
        li_url: null,
        thread_id: null,
        message_id,
        body: snippet,
        received_at: new Date().toISOString(),
        from_name,
      });
    }
    if (replies.length > 0) {
      LOG(`Posting ${replies.length} inbound DM(s) to backend`);
      chrome.runtime.sendMessage({ kind: 'inbound-replies', replies });
    }
  }
  // ── Open-thread scan — captures messages inside the currently open thread ──
  // Runs when the user is on a /messaging/thread/ URL. Reads individual message
  // bubbles directly so replies are captured even if the user has already sent a
  // follow-up (which would hide them from the inbox list snippet).
  async function scanOpenThread() {
    const m = location.pathname.match(/^\/messaging\/thread\/([^/?]+)/);
    if (!m) return;
    const thread_id = m[1];

    // Resolve the other participant's name from the thread header
    const participantName = (
      document.querySelector('.msg-entity-lockup__entity-title')?.innerText?.trim() ||
      document.querySelector('.msg-thread-title__text')?.innerText?.trim() ||
      document.querySelector('h2[class*="thread"], [data-test-thread-subject]')?.innerText?.trim() ||
      null
    );

    // Find all message body elements in the open thread
    // Class confirmed from DOM inspection: msg-s-event-listitem__body
    const allBodyEls = Array.from(document.querySelectorAll('.msg-s-event-listitem__body'));

    const replies = [];
    for (const bodyEl of allBodyEls.slice(-30)) {
      const body = (bodyEl.innerText || '').trim();
      if (!body || body.length < 2) continue;

      // Walk up to find the message event container and detect if it's self/outbound.
      // LinkedIn marks self-messages with --is-self on an ancestor, or places them
      // in a right-aligned (no avatar) container.
      const container = bodyEl.closest(
        '.msg-s-event-with-indicator, .msg-s-event-listitem, [class*="message-event"]'
      );
      if (container) {
        const isSelf =
          container.classList.toString().includes('is-self') ||
          container.closest('[class*="is-self"]') ||
          // Outbound messages have no avatar img sibling; check parent
          (!container.querySelector('img') &&
           !container.parentElement?.querySelector('img[class*="presence"], img[class*="avatar"]'));
        if (isSelf) continue;
      }

      const msg_id = `lithread:${thread_id}:${body.slice(0, 80)}`;
      if (seenMessageIds.has(msg_id)) continue;
      seenMessageIds.add(msg_id);

      replies.push({
        li_url: null,
        thread_id,
        message_id: msg_id,
        body,
        received_at: new Date().toISOString(),
        from_name: participantName || null,
      });
    }
    if (replies.length > 0) {
      LOG(`scanOpenThread: ${replies.length} inbound message(s) found`);
      chrome.runtime.sendMessage({ kind: 'inbound-replies', replies });
    }
  }
  // ── End open-thread scan ────────────────────────────────────────────────────

  // ── Inbox self-scan loop (jittered, passive reads only) ─────────────────
  // The background patrol hits /messaging/ only ~once per 75-100 min. This
  // self-scheduler runs every 45-90s on messaging pages so replies are
  // captured promptly without waiting for a background-driven patrol.
  (function scheduleInboxRescan() {
    const delay = 45000 + Math.random() * 45000;
    setTimeout(async () => {
      try { await scanInbox(); } catch (e) { LOG('rescan err', e); }
      try { await scanOpenThread(); } catch (e) { LOG('rescan thread err', e); }
      scheduleInboxRescan();
    }, delay);
  })();

  // ── Connection-acceptance scan ──────────────────────────────────────────
  // Detects when a previously-invited lead has become a 1st-degree connection
  // so the backend can release the gated DM step. Opportunistic, like
  // scanInbox: it reads signals while the user is on the relevant LinkedIn
  // pages. The backend only acts on URLs that belong to an enrolment currently
  // awaiting acceptance, so over-reporting is harmless.
  const seenConnections = new Set();

  // Self-healing finder for connection-list anchors. Uses the registry
  // `connectionCard` selector; if it yields nothing on the dedicated
  // connections page (where we KNOW connections exist), it requests an AI heal
  // — the same recovery path the command flow uses — and mirrors the attempt
  // to the watchdog so a broken My Network scan is visible, not silent.
  async function getConnectionAnchors() {
    const entry = registry.connectionCard;
    let anchors = entry ? deepQuerySelectorAll(entry.primary) : [];
    if (!anchors.length && entry?.fallback) anchors = deepQuerySelectorAll(entry.fallback);
    if (!anchors.length && !healedThisSession.has('connectionCard')) {
      healedThisSession.add('connectionCard');
      noteToWatchdog('selector_heal', 'issue',
        'connectionCard found nothing on connections page — requesting heal',
        { intent: 'connectionCard' });
      const healed = await requestHealAndWait('connectionCard');
      noteToWatchdog('selector_heal', healed ? 'healed' : 'issue',
        healed ? 'connectionCard selector healed' : 'connectionCard heal returned nothing',
        { intent: 'connectionCard' });
      if (healed) anchors = deepQuerySelectorAll(registry.connectionCard.primary);
    }
    return anchors;
  }

  const addProfileUrl = (set, href) => {
    const m = (href || '').match(/\/in\/[^/?#]+/);
    if (m) set.add('https://www.linkedin.com' + m[0] + '/');
  };

  async function scanConnections() {
    const path = location.pathname;
    const onConnections = path.startsWith('/mynetwork/invite-connect/connections');
    const onNotifs = path.startsWith('/notifications') || path.startsWith('/feed');
    if (!onConnections && !onNotifs) return;

    const urls = new Set();
    // Connections list page → every card is a real 1st-degree connection.
    if (onConnections) {
      (await getConnectionAnchors()).forEach(a => addProfileUrl(urls, a.getAttribute('href')));
    }
    // Notifications / feed → ONLY cards explicitly announcing an acceptance
    // (precise — avoids "People you may know" suggestion false positives).
    if (onNotifs) {
      deepQuerySelectorAll('a[href*="/in/"]').forEach(a => {
        const card = a.closest('article, li, .nt-card, [data-view-name*="notification"]');
        const txt = (card?.innerText || '').toLowerCase();
        if (/accepted your invitation|is now a connection|now connected|accepted your connection/.test(txt)) {
          addProfileUrl(urls, a.getAttribute('href'));
        }
      });
    }

    const found = [...urls].filter(u => !seenConnections.has(u));
    if (!found.length) return;
    found.forEach(u => seenConnections.add(u));
    LOG(`Reporting ${found.length} observed connection(s) to backend`);
    chrome.runtime.sendMessage({
      kind: 'connections-seen',
      connections: found.map(li_url => ({
        li_url, accepted_at: new Date().toISOString(), source: path.split('/')[1] || 'scan',
      })),
    });
  }
  // (scanConnections is now driven by the background patrol — see runPatrolSession.)

  // ── Human-like warm-up engine ───────────────────────────────────────────
  // Conservative, heavily-jittered activity to make the account look human:
  // browse the home feed, use varied reactions, follow the occasional suggested
  // entity. Runs ONLY on /feed/, never while a real command is executing
  // (cmdBusyUntil), and respects low daily caps. All best-effort — warm-up
  // failures are silent and never affect outreach commands.
  const WARMUP = {
    caps: { likes: 5, follows: 3, feedViews: 8, profileViews: 8 },   // per day, conservative
    tickMinMs: 18 * 60_000,
    tickMaxMs: 45 * 60_000,
    reactChance: 0.45,
    followChance: 0.20,
  };
  const REACTION_LABELS = ['Celebrate', 'Support', 'Love', 'Insightful', 'Funny'];

  async function loadWarmup() {
    const { warmup = {}, warmupEnabled = true } =
      await chrome.storage.local.get(['warmup', 'warmupEnabled']);
    const today = new Date().toISOString().slice(0, 10);
    const w = (warmup.day === today)
      ? warmup
      : { day: today, likes: 0, follows: 0, feedViews: 0, profileViews: 0 };
    return { w, enabled: warmupEnabled !== false };
  }
  const saveWarmup = (w) => chrome.storage.local.set({ warmup: w });

  async function warmScrollFeed(w) {
    const passes = 3 + Math.floor(Math.random() * 4);
    for (let i = 0; i < passes; i++) {
      window.scrollBy({ top: 280 + Math.random() * 520, behavior: 'smooth' });
      await sleep(1100 + Math.random() * 2600);
    }
    w.feedViews++;
  }

  async function warmReact(w) {
    if (w.likes >= WARMUP.caps.likes) return false;
    const posts = deepQuerySelectorAll('div.feed-shared-update-v2, div[data-urn*="urn:li:activity"]');
    for (const post of posts.slice(0, 8)) {
      try {
        const likeBtn = post.querySelector(
          'button[aria-label^="React Like"], button[aria-label="Like"], button.react-button__trigger',
        );
        if (!likeBtn || likeBtn.getAttribute('aria-pressed') === 'true') continue;
        post.scrollIntoView({ block: 'center' });
        await sleep(800 + Math.random() * 1200);
        // Occasionally use a varied reaction via the hover menu; else plain Like.
        let target = likeBtn;
        if (Math.random() < 0.5) {
          likeBtn.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
          await sleep(900);
          const wanted = REACTION_LABELS[Math.floor(Math.random() * REACTION_LABELS.length)];
          const r = deepQuerySelector(`button[aria-label="React ${wanted}"]`);
          if (r) target = r;
        }
        target.click();
        w.likes++;
        await sleep(1000 + Math.random() * 1200);
        return true;
      } catch { /* try next post */ }
    }
    return false;
  }

  async function warmFollow(w) {
    if (w.follows >= WARMUP.caps.follows) return false;
    const btn = deepQuerySelectorAll('button').find((b) =>
      /^follow$/i.test((b.innerText || '').trim()) && b.getAttribute('aria-pressed') !== 'true');
    if (!btn) return false;
    try {
      btn.scrollIntoView({ block: 'center' });
      await sleep(700 + Math.random() * 900);
      btn.click();
      w.follows++;
      await sleep(900);
      return true;
    } catch { return false; }
  }

  // View a relevant profile — a real LinkedIn "profile view" requires
  // navigation, which ends this content-script session, so it is treated as a
  // terminal action (the patrol breaks after it). The dwell happens naturally
  // as the page sits open until the next patrol.
  async function warmViewProfile(w) {
    if (w.profileViews >= WARMUP.caps.profileViews) return false;
    const links = deepQuerySelectorAll('a[href*="/in/"]')
      .map((a) => (a.getAttribute('href') || '').match(/\/in\/[^/?#]+/)?.[0])
      .filter(Boolean);
    if (!links.length) return false;
    const path = links[Math.floor(Math.random() * Math.min(links.length, 12))];
    w.profileViews++;
    await saveWarmup(w);                       // persist BEFORE we navigate away
    LOG('patrol: viewing profile ' + path);
    location.assign('https://www.linkedin.com' + path + '/');
    return true;
  }

  async function isCommandRunning() {
    const { cmdBusyUntil = 0 } = await chrome.storage.local.get(['cmdBusyUntil']);
    return Date.now() < cmdBusyUntil;
  }
  function shuffle(arr) {
    for (let i = arr.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [arr[i], arr[j]] = [arr[j], arr[i]];
    }
    return arr;
  }
  const patrolGap = (min, max) => sleep(min + Math.random() * (max - min));

  // ── Human-mimicry patrol session ────────────────────────────────────────
  // Triggered by the background's jittered patrol (RUN_PATROL_SESSION). Lives
  // in the content script because it can take minutes. ALWAYS does the cheap
  // scans; the RISKY actions are each independently gated by a probability AND
  // a daily cap, so action frequency NEVER scales with patrol frequency (≈50
  // patrols/day must stay on the same conservative caps).
  async function runPatrolSession() {
    if (await isCommandRunning()) { LOG('patrol session skipped — a command is running'); return; }
    LOG('patrol session start');

    // 1) Cheap/safe scans (each self-gates by page). Randomized human gaps.
    try { await scanConnections(); } catch (e) { LOG('patrol scanConnections err', e); }
    await patrolGap(2000, 8000);
    try { await scanInbox(); } catch (e) { LOG('patrol scanInbox err', e); }
    await patrolGap(1000, 3000);
    try { await scanOpenThread(); } catch (e) { LOG('patrol scanOpenThread err', e); }
    await patrolGap(2000, 8000);

    // 2) Risky warm-up actions — feed only, prob AND cap gated, shuffled.
    const { w, enabled } = await loadWarmup();
    if (!enabled) { LOG('patrol: warm-up disabled'); return; }
    if (!location.pathname.startsWith('/feed')) {
      LOG('patrol: not on /feed — scans only this session'); return;
    }

    await warmScrollFeed(w);                    // gentle, always-safe browse

    const actions = shuffle([
      { name: 'react',  prob: 0.30, cap: WARMUP.caps.likes,        used: () => w.likes,        run: () => warmReact(w),       terminal: false },
      { name: 'follow', prob: 0.10, cap: WARMUP.caps.follows,      used: () => w.follows,      run: () => warmFollow(w),      terminal: false },
      { name: 'view',   prob: 0.15, cap: WARMUP.caps.profileViews, used: () => w.profileViews, run: () => warmViewProfile(w), terminal: true },
    ]);

    for (const a of actions) {
      const capped = a.used() >= a.cap;
      const roll = Math.random();
      const fire = !capped && roll < a.prob;
      LOG(`patrol action ${a.name}: ${a.used()}/${a.cap} cap, p=${a.prob}, roll=${roll.toFixed(2)} → ${capped ? 'CAPPED' : fire ? 'RUN' : 'skip'}`);
      if (!fire) continue;
      try { await a.run(); } catch (e) { LOG(`patrol ${a.name} err`, e); }
      if (a.terminal) return;                   // navigation ends the session
      await patrolGap(5000, 15000);             // human-length gap between actions
    }

    await saveWarmup(w);
    LOG(`patrol session done: feed=${w.feedViews} reactions=${w.likes} follows=${w.follows} views=${w.profileViews}`);
  }

  LOG(`Content script ready on ${pageType()} page (${location.href})`);
})();

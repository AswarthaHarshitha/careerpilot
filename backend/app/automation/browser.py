"""Playwright helpers for reading and filling application forms.

Nothing here bypasses site protections. Login pages, CAPTCHAs and bot challenges are detected and handed to
the user, who completes them in the visible browser window. Password fields are never read or filled.
"""

import re
from dataclasses import dataclass

from playwright.sync_api import Frame, Page
from playwright.sync_api import TimeoutError as PWTimeout

CAPTCHA_FRAME = re.compile(r"recaptcha|hcaptcha|turnstile|challenges\.cloudflare|arkoselabs|funcaptcha|captcha", re.I)
CAPTCHA_TEXT = re.compile(
    r"verify (that )?you are (a )?human|are you a robot|complete the security check|"
    r"press (and|&) hold|checking your browser",
    re.I,
)
APPLY_TEXT = re.compile(
    r"^\s*(apply|apply now|apply for (this )?(job|position|role)|apply to (this )?job|"
    r"start (your )?application|i'?m interested|apply manually)\s*$",
    re.I,
)
NEXT_TEXT = re.compile(r"^\s*(next|continue|save (and|&) continue|proceed|next step|review)\s*[›>→]?\s*$", re.I)
SUBMIT_TEXT = re.compile(
    r"^\s*(submit|submit (my |your )?application|send application|apply|finish|"
    r"complete application|submit and finish)\s*$",
    re.I,
)
CONFIRM_TEXT = re.compile(
    r"thank you for (applying|your application|your interest)|application (has been |was )?"
    r"(received|submitted)|we('ve| have) received your application|successfully submitted|"
    r"your application is complete",
    re.I,
)
CONFIRM_URL = re.compile(r"thank|confirm|success|submitted|complete", re.I)

INSPECT_JS = r"""
() => {
  const out = [];
  const visible = (el) => {
    const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim();
  const textOf = (id) => { const n = document.getElementById(id); return n ? n.innerText : ''; };
  const labelOf = (el) => {
    if (el.labels && el.labels.length) return clean(Array.from(el.labels).map(l => l.innerText).join(' '));
    const aria = el.getAttribute('aria-label'); if (aria) return clean(aria);
    const lb = el.getAttribute('aria-labelledby');
    if (lb) return clean(lb.split(/\s+/).map(textOf).join(' '));
    const wrap = el.closest('label'); if (wrap) return clean(wrap.innerText);
    const box = el.closest('[class*="field"], [class*="question"], .form-group, li, fieldset');
    if (box) {
      const legend = box.querySelector('legend, label, .label, [class*="label"]');
      if (legend && clean(legend.innerText)) return clean(legend.innerText);
    }
    return clean(el.getAttribute('placeholder') || el.getAttribute('name') || el.id || '');
  };
  let n = 0;
  const nextRef = (el) => {
    let r = el.getAttribute('data-cp-ref');
    if (!r) { r = 'cp' + Date.now().toString(36) + '-' + (n++); el.setAttribute('data-cp-ref', r); }
    return r;
  };
  const groups = {};
  for (const el of document.querySelectorAll('input, select, textarea, [role="combobox"]')) {
    const tag = el.tagName.toLowerCase();
    let type = tag === 'input' ? (el.getAttribute('type') || 'text').toLowerCase() : tag;
    if (el.getAttribute('role') === 'combobox' && tag !== 'select') type = 'combobox';
    if (['hidden', 'submit', 'button', 'reset', 'image', 'password', 'search'].includes(type)) continue;
    if (el.disabled) continue;
    if (type !== 'file' && !visible(el)) continue;
    const required = !!(el.required || el.getAttribute('aria-required') === 'true');
    if (type === 'radio' || (type === 'checkbox' && el.name && document.querySelectorAll(
          `input[type=checkbox][name="${CSS.escape(el.name)}"]`).length > 1)) {
      const key = type + ':' + (el.name || nextRef(el));
      const fs = el.closest('fieldset');
      const question = fs && fs.querySelector('legend') ? clean(fs.querySelector('legend').innerText) : '';
      const optLabel = labelOf(el);
      nextRef(el);
      if (!groups[key]) {
        groups[key] = {field_ref: key, label: question || optLabel, input_type: type === 'radio' ? 'radio' : 'checkbox',
                       required, options: [], option_refs: []};
        out.push(groups[key]);
      }
      groups[key].options.push(optLabel || el.value);
      groups[key].option_refs.push(el.getAttribute('data-cp-ref'));
      groups[key].required = groups[key].required || required;
      continue;
    }
    let options = [];
    if (tag === 'select') {
      options = Array.from(el.options).map(o => clean(o.text)).filter(t => t && !/^(select|choose|please select|--)/i.test(t));
    }
    let label = labelOf(el);
    if (/\*\s*$/.test(label)) { label = label.replace(/\*\s*$/, '').trim(); }
    out.push({field_ref: nextRef(el), label, input_type: type === 'select-one' ? 'select' : type,
              required: required || /\*/.test(labelOf(el)), options,
              has_value: type === 'file' ? (el.files && el.files.length > 0) : !!el.value});
  }
  return out;
}
"""

BUTTONS_JS = r"""
() => Array.from(document.querySelectorAll('button, input[type=submit], input[type=button], a[role=button], a'))
  .filter(b => { const r = b.getBoundingClientRect(); return r.width > 0 && r.height > 0 && !b.disabled; })
  .map(b => {
    let r = b.getAttribute('data-cp-btn');
    if (!r) { r = 'b' + Math.random().toString(36).slice(2, 9); b.setAttribute('data-cp-btn', r); }
    return {ref: r, text: (b.innerText || b.value || b.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim(),
            type: (b.getAttribute('type') || '').toLowerCase(), tag: b.tagName.toLowerCase()};
  })
"""

ERRORS_JS = r"""
() => {
  const msgs = [];
  for (const el of document.querySelectorAll('input, select, textarea')) {
    // empty required fields are not errors yet; only values the site rejected, or fields it flagged
    const hasValue = el.type === 'checkbox' || el.type === 'radio' ? el.checked : !!el.value;
    if (el.getAttribute('aria-invalid') === 'true' || (hasValue && el.willValidate && !el.checkValidity())) {
      msgs.push({ref: el.getAttribute('data-cp-ref') || '', message: el.validationMessage ||
                 (document.getElementById(el.getAttribute('aria-describedby') || '') || {}).innerText || 'Invalid'});
    }
  }
  for (const el of document.querySelectorAll('[role=alert], .error, .field-error, .invalid-feedback, [class*="error-message"]')) {
    const t = (el.innerText || '').trim(); const r = el.getBoundingClientRect();
    if (t && r.width > 0 && r.height > 0) msgs.push({ref: '', message: t.slice(0, 200)});
  }
  return msgs;
}
"""


@dataclass
class FoundField:
    frame_index: int
    data: dict

    @property
    def ref(self) -> str:
        return f"{self.frame_index}|{self.data['field_ref']}"


def _frames(page: Page) -> list[Frame]:
    return [f for f in page.frames if not f.is_detached()]


def detect_obstacle(page: Page) -> str | None:
    """Returns a message for the user if the page needs a human (sign-in, CAPTCHA), else None."""
    for f in _frames(page):
        if f != page.main_frame and CAPTCHA_FRAME.search(f.url or ""):
            try:
                el = f.frame_element()
                if el.is_visible():
                    return "Complete the CAPTCHA / security check in the browser window. CareerPilot will continue."
            except Exception:  # noqa: BLE001
                continue
    try:
        body = page.inner_text("body", timeout=3000)[:5000]
    except PWTimeout:
        body = ""
    if CAPTCHA_TEXT.search(body):
        return "The site is checking that you are human. Complete it in the browser window; CareerPilot will continue."
    for f in _frames(page):
        try:
            pw = f.locator("input[type=password]")
            if pw.count() and pw.first.is_visible():
                return (
                    "Sign in (or create your account) on the employer's site in the browser window. "
                    "CareerPilot never sees or stores your password and will continue once you are in."
                )
        except Exception:  # noqa: BLE001
            continue
    return None


def inspect_form(page: Page) -> list[FoundField]:
    fields: list[FoundField] = []
    for i, f in enumerate(_frames(page)):
        try:
            found = f.evaluate(INSPECT_JS)
        except Exception:  # noqa: BLE001 - cross-origin frames that refuse evaluation are skipped
            continue
        for d in found:
            if not d.get("label") and d.get("input_type") != "file":
                d["label"] = "(unlabelled field)"
            fields.append(FoundField(i, d))
    return fields


def buttons(page: Page) -> list[tuple[Frame, dict]]:
    out = []
    for f in _frames(page):
        try:
            for b in f.evaluate(BUTTONS_JS):
                out.append((f, b))
        except Exception:  # noqa: BLE001
            continue
    return out


def find_button(page: Page, rx: re.Pattern, prefer_submit_type: bool = False):
    matches = [(f, b) for f, b in buttons(page) if b["text"] and rx.search(b["text"]) and len(b["text"]) < 40]
    if prefer_submit_type:
        matches.sort(key=lambda fb: fb[1]["type"] != "submit")
    return matches[0] if matches else None


def click_apply(page: Page) -> bool:
    hit = find_button(page, APPLY_TEXT)
    if not hit:
        return False
    f, b = hit
    try:
        f.locator(f'[data-cp-btn="{b["ref"]}"]').first.click(timeout=5000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        page.wait_for_timeout(1500)
        return True
    except PWTimeout:
        return False


def _frame_for(page: Page, ref: str) -> tuple[Frame | None, str]:
    idx, local = ref.split("|", 1)
    frames = _frames(page)
    i = int(idx)
    return (frames[i] if i < len(frames) else None), local


def fill_field(
    page: Page,
    found: dict[str, FoundField],
    ref: str,
    action: str,
    value: str,
    input_type: str,
    resume_path: str | None,
) -> tuple[bool, str]:
    """Fills one field. Returns (ok, validation message)."""
    ff = found.get(ref)
    frame, local = _frame_for(page, ref)
    if ff is None or frame is None:
        return False, "Field is no longer on the page."
    try:
        if input_type in ("radio", "checkbox") and ff.data.get("option_refs"):
            options = ff.data["options"]
            if input_type == "radio":
                idx = _best_option(options, value)
                if idx is None:
                    return False, "No option matches."
                frame.locator(f'[data-cp-ref="{ff.data["option_refs"][idx]}"]').check(timeout=5000)
            else:
                wanted = [v.strip() for v in value.split(";") if v.strip()]
                for v in wanted:
                    idx = _best_option(options, v)
                    if idx is not None:
                        frame.locator(f'[data-cp-ref="{ff.data["option_refs"][idx]}"]').check(timeout=5000)
            return True, ""
        loc = frame.locator(f'[data-cp-ref="{local}"]').first
        if action == "upload":
            if not resume_path:
                return False, "Resume file unavailable."
            loc.set_input_files(resume_path, timeout=10000)
        elif action == "check":
            if value.lower() in ("true", "yes", "1", "on"):
                loc.check(timeout=5000)
            else:
                loc.uncheck(timeout=5000)
        elif input_type == "select":
            try:
                loc.select_option(label=value, timeout=5000)
            except PWTimeout:
                loc.select_option(value=value, timeout=5000)
        elif input_type == "combobox":
            loc.click(timeout=5000)
            loc.fill(value, timeout=5000)
            page.wait_for_timeout(600)
            loc.press("Enter")
        else:
            loc.fill(value, timeout=5000)
            loc.dispatch_event("change")
            loc.dispatch_event("blur")
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"Could not fill automatically ({type(exc).__name__})."


def _best_option(options: list[str], value: str) -> int | None:
    v = value.strip().lower()
    for i, o in enumerate(options):
        if o.strip().lower() == v:
            return i
    for i, o in enumerate(options):
        if o.strip().lower().startswith(v) or (len(v) > 3 and v in o.strip().lower()):
            return i
    return None


def validation_errors(page: Page) -> list[dict]:
    out = []
    for i, f in enumerate(_frames(page)):
        try:
            for m in f.evaluate(ERRORS_JS):
                out.append({"ref": f"{i}|{m['ref']}" if m["ref"] else "", "message": m["message"]})
        except Exception:  # noqa: BLE001
            continue
    return out


def detect_confirmation(page: Page) -> tuple[bool, str]:
    try:
        body = page.inner_text("body", timeout=5000)
    except PWTimeout:
        body = ""
    m = CONFIRM_TEXT.search(body)
    if m:
        start = max(0, m.start() - 80)
        return True, re.sub(r"\s+", " ", body[start : m.end() + 200]).strip()
    for f in _frames(page)[1:]:
        try:
            t = f.inner_text("body", timeout=2000)
            m = CONFIRM_TEXT.search(t)
            if m:
                return True, re.sub(r"\s+", " ", t[max(0, m.start() - 80) : m.end() + 200]).strip()
        except Exception:  # noqa: BLE001
            continue
    if CONFIRM_URL.search(page.url) and not validation_errors(page):
        return (
            False,
            f"The page moved to {page.url}, which looks like a confirmation, but no confirmation text was found.",
        )
    return False, ""

// Shared helpers for the tiktok-symphony adapters (Symphony Creative Studio).
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { basename, dirname, extname } from 'node:path';
import { ArgumentError, CommandExecutionError, TimeoutError } from '@jackwener/opencli/errors';

export const HOST = 'ads.tiktok.com';

/** Every composer sub-app hangs off this page; `subApp` picks the tool. */
export const COMPOSER_URL = 'https://ads.tiktok.com/creative/creativestudio/image-to-video';

export const CREATE_URL = `${COMPOSER_URL}?subApp=CreativeStudio/ImageGeneration/I2VImageGeneration`;

export const LIBRARY_URL = 'https://ads.tiktok.com/creative/creativestudio/create/history';

/** The two image models the Image tab exposes. Order matters for help text. */
export const IMAGE_MODELS = ['Nano Banana', 'Flux Kontext Max'];

/** Number of output images one Image generation produces. */
export const OUTPUTS_PER_GENERATION = 4;

/** Reference images the composer accepts. Enforced by the site, mirrored here. */
export const MAX_REFERENCE_IMAGES = 4;

/**
 * The three Video sub-apps.
 *
 * Each is a distinct `subApp` URL, so we navigate straight to the mode instead
 * of clicking through the footer dropdown — one less fragile interaction, and
 * it makes the composer state unambiguous from the first paint.
 */
export const VIDEO_MODES = [
    {
        key: 'text',
        label: 'Text to video',
        subApp: 'CreativeStudio/MiniApp/TextToVideo',
        minRefs: 0,
        maxRefs: 0,
    },
    {
        key: 'image',
        label: 'Image to video',
        subApp: 'CreativeStudio/MiniApp/ImageToVideo',
        minRefs: 1,
        maxRefs: 2, // second image is the last frame, and only with --frames first-last
    },
    {
        key: 'reference',
        label: 'Reference to video',
        subApp: 'CreativeStudio/ReferenceToVideo/ReferenceToVideo',
        minRefs: 1,
        // The composer takes 9 images (plus 3 audio and 3 video slots, which
        // this adapter does not drive yet). The post-attach tally is still the
        // authority — it refuses to generate if fewer land than asked.
        maxRefs: 9,
    },
];

/** The only model the Video tab exposes today. */
// Order matters: the first entry is the default. Seedance 2.0 is the one the
// model menu tags "Multiple scenes", i.e. the only one that renders a
// multi-shot script as more than a single continuous take.
export const VIDEO_MODELS = ['Dreamina Seedance 2.0', 'Video 1.5 Pro'];

/**
 * Clip lengths the duration dropdown offers, in seconds.
 *
 * The list is per-model — the dropdown is repopulated when the model changes —
 * so this is the union. Asking for a length the current model does not offer
 * fails loudly in `selectFooterOption` rather than silently generating a
 * different length, which credits are charged for.
 */
export const VIDEO_DURATIONS = [5, 10, 12, 15];

/** Frame modes offered by the Image-to-video sub-app. */
export const VIDEO_FRAME_MODES = [
    { key: 'first', label: 'First frame only', refs: 1 },
    { key: 'first-last', label: 'First and last frame', refs: 2 },
];

/** One Video generation renders a single clip. */
export const VIDEO_OUTPUTS_PER_GENERATION = 1;

/**
 * Validate a positive integer arg without silently flooring or clamping it.
 * Out-of-range input is a caller mistake, not something to quietly fix up.
 */
export function normalizePositiveInteger(value, defaultValue, label = 'value', { min = 1 } = {}) {
    const raw = value ?? defaultValue;
    const n = Number(raw);
    if (!Number.isInteger(n) || n <= 0) throw new ArgumentError(`${label} must be a positive integer`);
    if (n < min) throw new ArgumentError(`${label} must be >= ${min}`);
    return n;
}

/** Positive integer with an explicit ceiling. */
export function normalizeLimit(value, defaultValue, maxValue, label = 'limit') {
    const n = normalizePositiveInteger(value, defaultValue, label);
    if (n > maxValue) throw new ArgumentError(`${label} must be <= ${maxValue}`);
    return n;
}

/**
 * Map user input onto a canonical model name, case- and spacing-insensitively.
 * Rejects anything else rather than falling back to the default, so a typo
 * cannot silently bill a generation to the wrong model.
 */
export function resolveModel(value, defaultValue = IMAGE_MODELS[0]) {
    const raw = String(value ?? defaultValue).trim();
    const key = raw.toLowerCase().replace(/[\s_-]+/g, '');
    const hit = IMAGE_MODELS.find((m) => m.toLowerCase().replace(/[\s_-]+/g, '') === key);
    if (!hit) throw new ArgumentError(`Unknown model "${raw}". Valid: ${IMAGE_MODELS.join(' / ')}`);
    return hit;
}

/** Case- and spacing-insensitive key used by every "resolve a label" helper. */
const foldKey = (value) => String(value ?? '').trim().toLowerCase().replace(/[\s_-]+/g, '');

/** Resolve `--mode` onto one of the three VIDEO_MODES entries. */
export function resolveVideoMode(value, defaultValue = 'text') {
    const key = foldKey(value ?? defaultValue);
    const hit = VIDEO_MODES.find((m) => foldKey(m.key) === key || foldKey(m.label) === key);
    if (!hit) {
        throw new ArgumentError(`Unknown mode "${value}". Valid: ${VIDEO_MODES.map((m) => m.key).join(' / ')}`);
    }
    return hit;
}

/** Resolve `--model` for the Video tab. Rejects unknown names rather than defaulting. */
export function resolveVideoModel(value, defaultValue = VIDEO_MODELS[0]) {
    const key = foldKey(value ?? defaultValue);
    const hit = VIDEO_MODELS.find((m) => foldKey(m) === key);
    if (!hit) throw new ArgumentError(`Unknown model "${value}". Valid: ${VIDEO_MODELS.join(' / ')}`);
    return hit;
}

/** Resolve `--duration`; accepts `10` or `10s`. Returns the dropdown's label. */
export function resolveVideoDuration(value, defaultValue = VIDEO_DURATIONS[0]) {
    const raw = String(value ?? defaultValue).trim().replace(/s$/i, '');
    const n = Number(raw);
    if (!VIDEO_DURATIONS.includes(n)) {
        throw new ArgumentError(`Unknown duration "${value}". Valid: ${VIDEO_DURATIONS.map((d) => `${d}s`).join(' / ')}`);
    }
    return { seconds: n, label: `${n}s` };
}

/** Resolve `--frames` for the Image-to-video sub-app. */
export function resolveVideoFrameMode(value, defaultValue = 'first') {
    const key = foldKey(value ?? defaultValue);
    const hit = VIDEO_FRAME_MODES.find((f) => foldKey(f.key) === key || foldKey(f.label) === key);
    if (!hit) {
        throw new ArgumentError(`Unknown frames "${value}". Valid: ${VIDEO_FRAME_MODES.map((f) => f.key).join(' / ')}`);
    }
    return hit;
}

/**
 * Generated assets live at .../ad-creative-sg/<assetId>~tplv-<template>.<ext>.
 * The DOM carries no real generation id, so this path segment is the only
 * stable handle we can hand back to the user for `download`.
 */
export function assetIdFromUrl(url) {
    const m = /\/ad-creative-sg\/([A-Za-z0-9]+)~/.exec(String(url || ''));
    return m ? m[1] : null;
}

/** True for a CDN URL that is a finished generation output (not a reference thumb). */
export function isOutputAssetUrl(url) {
    return /ad-site-sign-sg\.tiktokcdn\.com\/ad-creative-sg\//.test(String(url || ''));
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Poll a page-context expression until it returns something non-null.
 *
 * The Symphony UI is a client-rendered SPA: `navigateBefore` resolves as soon
 * as the document loads, well before the `ks-*` header and composer hydrate.
 * Every read here has to tolerate that gap rather than assume the DOM is ready.
 *
 * @returns the first non-null value the expression produced
 * @throws {TimeoutError} when the deadline passes without one
 */
export async function waitForValue(page, js, { timeoutMs = 20000, intervalMs = 500, label = 'page state' } = {}) {
    const deadline = Date.now() + timeoutMs;
    let last = null;
    for (;;) {
        last = await page.evaluate(js);
        if (last !== null && last !== undefined && last !== false) return last;
        if (Date.now() >= deadline) throw new TimeoutError(label, Math.round(timeoutMs / 1000));
        await sleep(intervalMs);
    }
}

/**
 * Page-context source for a deep querySelectorAll that descends into shadow
 * roots. The Symphony UI is built from `ks-*` web components, so nearly every
 * control we need is behind at least one shadow boundary.
 *
 * Shipped as a string because `page.evaluate` callbacks cannot close over
 * Node-side scope; each evaluate injects this and calls it.
 */
export const DEEP_QUERY_SRC = `
function __deepAll(root, test) {
    const out = [];
    const walk = (node) => {
        for (const el of node.querySelectorAll('*')) {
            if (test(el)) out.push(el);
            if (el.shadowRoot) walk(el.shadowRoot);
        }
    };
    const start = root || document;
    walk(start);
    // The root's OWN shadow root has to be walked explicitly. A ks-modal keeps
    // its title and buttons there while its light DOM holds only the body text,
    // so skipping this makes the dialog look like it has no controls at all.
    if (start.shadowRoot) walk(start.shadowRoot);
    return out;
}
function __ksTag(el, prefix) {
    // ks-* elements carry a version suffix (ks-button-1-1-1m). Match the
    // prefix only, or a version bump silently breaks every selector.
    return el.tagName.toLowerCase().startsWith(prefix);
}
function __ownText(el) {
    return [...el.childNodes]
        .filter((n) => n.nodeType === 3)
        .map((n) => n.textContent.trim())
        .join('');
}
`;

/**
 * Scroll the Library grid one screen further; reports whether it moved.
 *
 * The window itself never scrolls on that page — the grid lives in its own
 * `overflow-auto` container, so `page.scroll` leaves it exactly where it was
 * and every tile below the fold stays a placeholder. The container is found by
 * shape rather than class name, which survives a restyle.
 */
export const LIBRARY_SCROLL_SRC = `(() => {
    let best = null;
    for (const el of document.querySelectorAll('*')) {
        if (el.clientHeight < 200 || el.scrollHeight - el.clientHeight < 100) continue;
        if (!best || el.scrollHeight > best.scrollHeight) best = el;
    }
    const target = best || document.scrollingElement;
    if (!target) return { moved: false, atEnd: true };
    const before = target.scrollTop;
    target.scrollTop = before + Math.max(200, target.clientHeight * 0.9);
    return {
        moved: target.scrollTop > before,
        atEnd: target.scrollTop >= target.scrollHeight - target.clientHeight - 4,
    };
})()`;

/**
 * Read a page-context expression until two consecutive reads agree.
 *
 * The Create feed lazy-loads and recycles <img> nodes, so a single read can
 * catch a card mid-swap and report a neighbour's asset as one of ours. Waiting
 * for the value to stop moving is what makes the reading trustworthy.
 */
export async function readStable(page, js, { tries = 10, intervalMs = 1500 } = {}) {
    let previous = null;
    let value = null;
    for (let i = 0; i < tries; i++) {
        value = await page.evaluate(js);
        const key = JSON.stringify(value);
        if (key === previous) return value;
        previous = key;
        await sleep(intervalMs);
    }
    return value;
}

/**
 * Page-context source for locating one generation card in the Create feed.
 *
 * Diffing "all assets on the page" against a pre-submit snapshot is not safe:
 * the feed lazy-loads, so assets from an *earlier* generation can stream in
 * after the snapshot and look brand new. Scoping to the card that leads with
 * our own prompt is the only reading that cannot pick up a neighbour's output.
 */
export const CARD_QUERY_SRC = `
${DEEP_QUERY_SRC}
function __cards() {
    // Cards are the rounded feed containers; the CSSTransition class marks them
    // once mounted, with the class-name match as a fallback if that changes.
    const mounted = [...document.querySelectorAll('div.gen-item-enter-done')];
    if (mounted.length) return mounted;
    return [...document.querySelectorAll('div')].filter((d) =>
        /rounded-\\[var\\(--ks-border-radius-container\\)\\]/.test(d.className || ''));
}
function __cardFor(prompt) {
    // Newest card is first. Compare on a prefix: long prompts render truncated.
    const head = String(prompt).slice(0, 40);
    return __cards().find((c) => (c.innerText || '').trim().startsWith(head)) || null;
}
function __cardState(card) {
    // A card ends one of three ways: still counting up, rejected with an error
    // code, or holding its outputs. Rejection is the one that must never be
    // mistaken for success — moderation refusals land here, not in a dialog.
    const text = (card.innerText || '').trim();
    const lines = text.split('\\n').map((s) => s.trim()).filter(Boolean);
    const pct = /(\\d+)%/.exec(text);
    const code = /Error code:\\s*([0-9]+)/.exec(text);
    const task = /Task ID:\\s*([0-9]+)/.exec(text);
    // A mounted output is the only positive proof of completion: the site drops
    // the percentage before the clip appears ("Almost there…"), so "no %" on its
    // own means nothing.
    const clip = __deepAll(card, (el) => el.tagName === 'VIDEO')
        .map((v) => v.src || v.currentSrc || '')
        .find((src) => src && !/lf-creative-factory/.test(src)) || null;
    const stills = __deepAll(card, (el) => el.tagName === 'IMG')
        .filter((img) => /ad-creative-sg/.test(img.src || '')).length;
    // Keys are card-prefixed so they never collide with an adapter's output
    // columns — a same-named intermediate is what makes a dropped column silent.
    return {
        cardPrompt: lines[0] || null,
        cardModel: lines[1] || null,
        cardProgress: pct ? Number(pct[1]) : null,
        cardErrorCode: code ? code[1] : null,
        // The message sits between the model tag and the Task ID line.
        cardErrorText: code ? (lines.find((l) => /violat|error|fail|try again/i.test(l)) || null) : null,
        cardTaskId: task ? task[1] : null,
        cardClip: clip,
        cardStills: stills,
    };
}
`;

// ---------------------------------------------------------------------------
// Composer (the chat-style box shared by the Image and Video sub-apps)
// ---------------------------------------------------------------------------

/** The prompt editor is TipTap/ProseMirror, not a <textarea>. */
export const PROMPT_SELECTOR = '[data-chatbox-part=textarea] .ProseMirror';

/** The composer has no file input; references go in through this drop zone. */
export const DROP_ZONE_SELECTOR = 'fieldset[aria-label="File drop zone"]';
const DEFAULT_DIRECT_ASSET_CACHE = '<本机路径>';

const MAX_REFERENCE_BYTES = 10 * 1024 * 1024;

/** Base64 characters per bridge round-trip. Kept well under the message cap. */
const B64_CHUNK_CHARS = 128 * 1024;

const MIME_BY_EXT = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.webp': 'image/webp',
    '.gif': 'image/gif',
    '.avif': 'image/avif',
};

/** Count reference thumbnails currently attached to the composer. */
export const REF_COUNT_SRC = `(() => {
    ${DEEP_QUERY_SRC}
    const header = document.querySelector('[data-chatbox-part=header]');
    if (!header) return 0;
    // Static chrome (logo, page art) also lives here; uploaded references are
    // object URLs or land on the creative-tool upload host.
    //
    // Count DISTINCT sources, not <img> elements: each attached reference
    // renders twice (a visible thumbnail plus a zero-sized copy), so counting
    // elements reported double. That made the ">= want" upload check vacuous
    // from the second image onward — a reference that never attached still
    // satisfied it, and the composer generated with fewer refs than requested.
    const refs = [...header.querySelectorAll('img')].filter((img) => {
        const s = img.src || '';
        return s.startsWith('blob:') || s.startsWith('data:')
            || /ibyteimg\\.com|creative-tool/.test(s);
    });

    // Prefer a laid-out thumbnail over a bare src: the site writes the src as
    // soon as an upload is accepted but mounts the tile only once the reference
    // is usable. Some composer layouts measure the whole header at 0x0 though —
    // including their own demo art — and there a size check rejects every
    // reference that did upload, so fall back to counting sources.
    const laidOut = refs.filter((img) => (img.parentElement || img).getBoundingClientRect().width > 0);
    const usable = laidOut.length ? laidOut : refs;
    return new Set(usable.map((img) => img.src)).size;
})()`;

/**
 * Read every reference image up front so a bad path fails before we touch the
 * UI — half-filling the composer and then throwing would leave the page dirty.
 */
export async function readReferenceImages(raw, max) {
    const paths = String(raw ?? '').split(',').map((s) => s.trim()).filter(Boolean);
    if (paths.length > max) {
        throw new ArgumentError(`refs accepts at most ${max} image(s) here, got ${paths.length}`);
    }

    const out = [];
    for (const path of paths) {
        let buf;
        try {
            buf = await readFile(path);
        } catch (error) {
            throw new ArgumentError(`cannot read reference image "${path}": ${error?.message || error}`);
        }
        if (buf.length === 0) throw new ArgumentError(`reference image "${path}" is empty`);
        if (buf.length > MAX_REFERENCE_BYTES) {
            throw new ArgumentError(`reference image "${path}" is larger than ${MAX_REFERENCE_BYTES} bytes`);
        }
        const ext = extname(path).toLowerCase();
        const type = MIME_BY_EXT[ext];
        if (!type) {
            throw new ArgumentError(`unsupported reference image type "${ext || path}" (use ${Object.keys(MIME_BY_EXT).join(' / ')})`);
        }
        out.push({
            name: basename(path),
            type,
            b64: buf.toString('base64'),
            sha256: createHash('sha256').update(buf).digest('hex'),
        });
    }
    return out;
}

/** Persist local-content hashes to the stable URLs accepted by the direct API. */
export async function persistReferenceCache(page, references) {
    if (!references.length) return 0;
    const urls = await page.evaluate(`(() => {
        ${DEEP_QUERY_SRC}
        const header = document.querySelector('[data-chatbox-part=header]');
        if (!header) return [];
        const seen = new Set();
        const out = [];
        for (const img of __deepAll(header, (el) => el.tagName === 'IMG')) {
            const src = img.src || '';
            if (!/creative-tool-sg\\.ibyteimg\\.com/.test(src) || seen.has(src)) continue;
            seen.add(src);
            out.push(src);
        }
        return out;
    })()`);
    if (!Array.isArray(urls) || urls.length !== references.length) {
        throw new CommandExecutionError(
            `cannot update direct asset cache: saw ${Array.isArray(urls) ? urls.length : 0} stable URL(s) for ${references.length} reference(s)`,
        );
    }

    const cachePath = process.env.SYMPHONY_ASSET_CACHE || DEFAULT_DIRECT_ASSET_CACHE;
    let cache = {};
    try {
        cache = JSON.parse(await readFile(cachePath, 'utf8'));
    } catch (error) {
        if (error?.code !== 'ENOENT') throw error;
    }
    for (const [index, ref] of references.entries()) {
        cache[ref.sha256] = { name: ref.name, url: urls[index] };
    }
    await mkdir(dirname(cachePath), { recursive: true });
    await writeFile(cachePath, `${JSON.stringify(cache, null, 2)}\n`, 'utf8');
    return references.length;
}

/**
 * Attach one reference image.
 *
 * The page exposes no file input and CDP file injection is refused, so the
 * supported path is the composer's own drop zone: build the File inside the
 * page and dispatch a real drop.
 */
export async function attachReference(page, ref, index) {
    const want = index + 1;

    // The drop zone occasionally swallows the very first drop — the composer
    // takes the event before it is ready for it — and the failure is silent:
    // no error, the count simply never moves. One retry clears it.
    for (const attempt of [1, 2]) {
        await dispatchReferenceDrop(page, ref);
        const attached = await waitForValue(page, `(() => {
            const n = ${REF_COUNT_SRC};
            return n >= ${want} ? n : null;
        })()`, {
            label: `reference upload "${ref.name}"`,
            // Generous even on the first try: these are multi-megabyte PNGs and
            // a short first window turns a merely slow upload into a re-drop,
            // which piles a second transfer on top of the one still running.
            timeoutMs: attempt === 1 ? 45000 : 90000,
            intervalMs: 700,
        }).catch((error) => {
            if (error instanceof TimeoutError) return null;
            throw error;
        });
        if (attached) return;
    }
    // Say what the page actually showed. "Did not attach" alone cannot
    // distinguish a rejected upload from a counter looking in the wrong place,
    // and those need opposite fixes.
    const seen = await page.evaluate(`(() => {
        ${DEEP_QUERY_SRC}
        const header = document.querySelector('[data-chatbox-part=header]');
        const shot = (root, tag) => __deepAll(root, (el) => el.tagName === 'IMG').map((img) => {
            const host = img.parentElement || img;
            const r = host.getBoundingClientRect();
            return tag + ':' + (img.src || '').slice(-28) + '@' + Math.round(r.width) + 'x' + Math.round(r.height);
        });
        return {
            header: header ? shot(header, 'hdr') : null,
            document: shot(document, 'doc').filter((s) => /ibyteimg|creative-tool|blob:|data:/.test(s)),
        };
    })()`);
    throw new CommandExecutionError(
        `reference image "${ref.name}" did not attach to the composer`
        + ` — header imgs: ${seen.header ? JSON.stringify(seen.header) : 'NO [data-chatbox-part=header]'}`
        + `; page refs: ${JSON.stringify(seen.document)}`,
    );
}

/** Build the File inside the page and drop it on the composer. */
async function dispatchReferenceDrop(page, ref) {
    // The base64 goes over in slices: a single ~1 MB evaluate argument exceeds
    // what the browser bridge will carry and fails with "max attempts exhausted".
    await page.evaluate(() => { window.__ocRefChunks = []; return true; });
    for (let at = 0; at < ref.b64.length; at += B64_CHUNK_CHARS) {
        const chunk = ref.b64.slice(at, at + B64_CHUNK_CHARS);
        const ok = await page.evaluate((piece) => {
            if (!window.__ocRefChunks) return false;
            window.__ocRefChunks.push(piece);
            return true;
        }, chunk);
        if (!ok) throw new CommandExecutionError(`reference image "${ref.name}" was dropped while being transferred to the page`);
    }

    const dropped = await page.evaluate((name, type, selector) => {
        const b64 = (window.__ocRefChunks || []).join('');
        delete window.__ocRefChunks;
        if (!b64) return false;
        const bin = atob(b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const file = new File([bytes], name, { type });
        const dt = new DataTransfer();
        dt.items.add(file);
        const zone = document.querySelector(selector);
        if (!zone) return false;
        for (const kind of ['dragenter', 'dragover', 'drop']) {
            zone.dispatchEvent(new DragEvent(kind, { bubbles: true, cancelable: true, dataTransfer: dt }));
        }
        return true;
    }, ref.name, ref.type, DROP_ZONE_SELECTOR);

    if (!dropped) throw new CommandExecutionError(`could not find the composer drop zone for "${ref.name}"`);
}

/** Type the prompt and prove it registered in the ProseMirror editor. */
export async function typePrompt(page, prompt) {
    await page.typeText(PROMPT_SELECTOR, prompt);
    const typed = await page.evaluate(`(() => {
        const pm = document.querySelector('${PROMPT_SELECTOR}');
        return pm ? pm.textContent : null;
    })()`);
    if (!typed || !typed.includes(prompt.slice(0, 20))) {
        throw new CommandExecutionError('prompt did not register in the composer editor');
    }
}

/**
 * Page-context source reading the current label of the footer dropdown whose
 * value is one of `allowed`.
 *
 * The footer holds several dropdowns and their number changes per sub-app, so
 * indexing into them is fragile. Each control has a disjoint option set, which
 * makes "the dropdown currently showing one of these values" a stable handle.
 * Nested `ks-dropdown-menu` elements exist too — hence the own-text filter.
 */
export function footerDropdownLabelSrc(allowed) {
    return `(() => {
        ${DEEP_QUERY_SRC}
        const allowed = ${JSON.stringify(allowed)};
        const footer = document.querySelector('[data-chatbox-part=footer]');
        if (!footer) return null;
        for (const dd of __deepAll(footer, (el) => __ksTag(el, 'ks-dropdown-menu'))) {
            const btn = __deepAll(dd, (el) => __ksTag(el, 'ks-button'))[0];
            // Read the button's own text: the menu stays in the DOM, so the
            // dropdown's textContent would contain every option at once.
            const label = btn ? __ownText(btn) : '';
            if (label && allowed.includes(label)) return label;
        }
        return null;
    })()`;
}

/**
 * Pick `target` from the footer dropdown identified by `allowed`, then confirm
 * the trigger label actually changed.
 */
export async function selectFooterOption(page, { allowed, target, label }) {
    const current = await waitForValue(page, footerDropdownLabelSrc(allowed), {
        label: `${label} dropdown`,
        timeoutMs: 20000,
    });
    if (current === target) return current;

    const opened = await page.evaluate(`(() => {
        ${DEEP_QUERY_SRC}
        const allowed = ${JSON.stringify(allowed)};
        const footer = document.querySelector('[data-chatbox-part=footer]');
        for (const dd of __deepAll(footer, (el) => __ksTag(el, 'ks-dropdown-menu'))) {
            const btn = __deepAll(dd, (el) => __ksTag(el, 'ks-button'))[0];
            if (!btn || !allowed.includes(__ownText(btn))) continue;
            const real = (btn.shadowRoot && btn.shadowRoot.querySelector('button')) || btn;
            real.click();
            return true;
        }
        return null;
    })()`);
    if (!opened) throw new CommandExecutionError(`could not open the ${label} dropdown`);

    const picked = await waitForValue(page, `(() => {
        ${DEEP_QUERY_SRC}
        const want = ${JSON.stringify(target)};
        const footer = document.querySelector('[data-chatbox-part=footer]');
        // Match on own text so an option's description paragraph, which repeats
        // the name, cannot be clicked instead of the option row itself.
        const opt = __deepAll(footer, (el) => !__ksTag(el, 'ks-button') && __ownText(el) === want)[0];
        if (!opt) return null;
        opt.click();
        return true;
    })()`, { label: `${label} option "${target}"`, timeoutMs: 10000 }).catch(() => null);
    if (!picked) throw new CommandExecutionError(`the ${label} dropdown has no option "${target}"`);

    const now = await waitForValue(page, `(() => {
        const v = ${footerDropdownLabelSrc(allowed)};
        return v === ${JSON.stringify(target)} ? v : null;
    })()`, { label: `${label} switch to ${target}`, timeoutMs: 15000 }).catch(() => null);
    if (now !== target) throw new CommandExecutionError(`could not switch ${label} to "${target}"`);
    return now;
}

/** Page-context source reading the duration control's current "Ns" label. */
export const DURATION_LABEL_SRC = `(() => {
    ${DEEP_QUERY_SRC}
    const footer = document.querySelector('[data-chatbox-part=footer]');
    if (!footer) return null;
    for (const btn of __deepAll(footer, (el) => __ksTag(el, 'ks-button'))) {
        const t = __ownText(btn);
        if (/^\\d+s$/.test(t)) return t;
    }
    return null;
})()`;

/**
 * Set the clip length.
 *
 * Duration is NOT a dropdown: it is a `ks-slider` in a popover, so picking it
 * by option text (the way model and mode are picked) fails with "no option
 * 15s" no matter which length is asked for. Driving it by arrow key rather
 * than by writing `value` keeps React's own state in the loop, and reading the
 * footer label back after each press is what proves the change took.
 */
export async function setVideoDuration(page, seconds) {
    const want = `${seconds}s`;
    let current = await waitForValue(page, DURATION_LABEL_SRC, { label: 'duration control', timeoutMs: 20000 });
    if (current === want) return current;

    const opened = await page.evaluate(`(() => {
        ${DEEP_QUERY_SRC}
        const footer = document.querySelector('[data-chatbox-part=footer]');
        const btn = __deepAll(footer, (el) => __ksTag(el, 'ks-button') && /^\\d+s$/.test(__ownText(el)))[0];
        if (!btn) return false;
        const real = (btn.shadowRoot && btn.shadowRoot.querySelector('button')) || btn;
        real.click();
        return true;
    })()`);
    if (!opened) throw new CommandExecutionError('could not open the duration control');
    await page.wait(0.6);

    // Type into the number box beside the slider rather than driving the slider
    // itself: the ks-slider ignores arrow keys sent through CDP, so a key-based
    // approach reports every length as unavailable.
    const focused = await page.evaluate(`(() => {
        ${DEEP_QUERY_SRC}
        const box = __deepAll(document, (el) => el.tagName === 'INPUT'
            && el.getBoundingClientRect().width > 0
            && !!el.closest === true)[0];
        if (!box) return false;
        box.focus();
        box.select && box.select();
        return true;
    })()`);
    if (!focused) throw new CommandExecutionError('the duration input did not appear');

    await page.nativeKeyPress('a', ['Ctrl']);
    await page.nativeType(String(seconds));
    await page.nativeKeyPress('Enter');
    await page.wait(0.8);
    await page.nativeKeyPress('Escape');
    await page.wait(0.4);
    current = await page.evaluate(DURATION_LABEL_SRC);

    const settled = await page.evaluate(DURATION_LABEL_SRC);
    if (settled !== want) throw new CommandExecutionError(`could not set duration to ${want} (stuck at ${settled})`);
    return settled;
}

/** Click the composer's send button once it is enabled. */
export async function submitComposer(page) {
    const clicked = await waitForValue(page, `(() => {
        ${DEEP_QUERY_SRC}
        const footer = document.querySelector('[data-chatbox-part=footer]');
        if (!footer) return null;
        const btn = __deepAll(footer, (el) => __ksTag(el, 'ks-icon-button')
            && __deepAll(el, (x) => __ksTag(x, 'ks-icon-arrow-up')).length > 0)[0];
        if (!btn) return null;
        const real = (btn.shadowRoot && btn.shadowRoot.querySelector('button')) || btn;
        if (real.disabled || btn.hasAttribute('disabled')) return null;
        real.click();
        return true;
    })()`, { label: 'send button', timeoutMs: 20000 }).catch(() => null);

    if (!clicked) {
        throw new CommandExecutionError('the send button never became enabled — the composer may have rejected the input');
    }
}

import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { basename, dirname } from 'node:path';
import { AuthRequiredError, CommandExecutionError, TimeoutError } from '@jackwener/opencli/errors';

export const HOST = 'higgsfield.ai';
export const VIDEO_URL = `https://${HOST}/ai/video`;

/**
 * Higgsfield collapses into a mobile layout on narrow viewports, which hides the
 * composer behind a modal and makes the option pickers unreachable. Every command
 * that touches the composer forces a desktop viewport first.
 */
export const DESKTOP_VIEWPORT = { width: 1600, height: 1000 };

/** Option pickers in the composer, keyed by their aria-label. */
export const CONTROL_LABELS = {
    model: 'Model',
    duration: 'Duration',
    ratio: 'Ratio',
    resolution: 'Resolution',
    bitrate: 'Bitrate',
};

export const UNLIMITED_LABEL = 'Unlimited mode';

export async function waitForValue(page, source, {
    label,
    timeoutMs = 30000,
    intervalMs = 500,
} = {}) {
    const deadline = Date.now() + timeoutMs;
    let lastError = null;
    while (Date.now() < deadline) {
        try {
            const value = await page.evaluate(source);
            if (value) return value;
            lastError = null;
        } catch (error) {
            // A composer re-render can tear down nodes mid-evaluate; keep polling.
            lastError = error;
        }
        await page.wait(Math.min(intervalMs / 1000, Math.max(0.05, (deadline - Date.now()) / 1000)));
    }
    const suffix = lastError ? ` (last probe error: ${lastError.message || lastError})` : '';
    throw new TimeoutError(`Timed out waiting for ${label || 'Higgsfield page state'}${suffix}`, timeoutMs);
}

/**
 * Shared browser-side helpers. Injected into every evaluate() that needs to find
 * composer controls so the matching rules stay in exactly one place.
 */
const HELPERS = `
    const visible = (el) => {
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return false;
        const style = getComputedStyle(el);
        return style.visibility !== 'hidden' && style.display !== 'none';
    };
    const label = (el) => (el.getAttribute('aria-label') || '').trim();
    const text = (el) => String(el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
    const controlByLabel = (want) => Array.from(document.querySelectorAll('button,[role="button"],[role="switch"]'))
        .filter(visible).find((el) => label(el) === want) || null;
    const rectOf = (el) => {
        const rect = el.getBoundingClientRect();
        return {
            x: Math.round(rect.x + rect.width / 2),
            y: Math.round(rect.y + rect.height / 2),
            w: Math.round(rect.width),
            h: Math.round(rect.height),
        };
    };
`;

/** Build an evaluate() source string with the shared helpers in scope. */
export function browserSource(body) {
    return `(() => {${HELPERS}\n${body}\n})()`;
}

export async function readIdentity(page) {
    const probe = await page.evaluate(browserSource(`
        const clerk = window.Clerk;
        if (clerk && clerk.loaded === false) return null;
        if (clerk && clerk.user) {
            const user = clerk.user;
            return {
                state: 'authenticated',
                id: user.id || '',
                email: (user.primaryEmailAddress && user.primaryEmailAddress.emailAddress) || '',
                name: user.fullName || '',
            };
        }
        const signIn = Array.from(document.querySelectorAll('a,button')).filter(visible)
            .some((el) => /^(sign up|sign in|log in|login)$/i.test(text(el)));
        if (clerk && clerk.loaded && !clerk.user) return { state: 'anonymous' };
        if (signIn) return { state: 'anonymous' };
        return null;
    `));
    return probe;
}

/**
 * Best-effort read of the signed-out marker on the current page.
 *
 * window.Clerk is not reliably exposed on higgsfield.ai (verified 2026-07-31 —
 * it was absent on the landing page), so the signed-out signal is the visible
 * Sign up / Log in control instead. `auth.js` owns the authoritative check via
 * the Clerk session cookie and API.
 */
export async function looksSignedOut(page) {
    return page.evaluate(browserSource(`
        return Array.from(document.querySelectorAll('a,button')).filter(visible)
            .some((el) => /^(sign up|sign in|log in|login)$/i.test(text(el)));
    `));
}

/**
 * Navigate to the video composer and wait until its controls are mounted.
 *
 * A mounted composer is itself the proof of an authenticated session — the
 * signed-out page renders a marketing landing page instead. Only when the
 * composer never appears do we look at why, so a slow render is not mistaken
 * for a signed-out session.
 */
export async function openComposer(page, { timeoutMs = 60000, url = VIDEO_URL, navigate = true } = {}) {
    await page.resizeWindow?.(DESKTOP_VIEWPORT.width, DESKTOP_VIEWPORT.height).catch(() => {});
    // navigate:false reuses whatever the persistent session already has open, so
    // references the user selected by hand are not wiped by a reload.
    if (navigate) await page.goto(url, { waitUntil: 'load' });
    try {
        await waitForValue(page, browserSource(`
            const submit = Array.from(document.querySelectorAll('button[type="submit"]')).filter(visible)[0];
            const anyOption = ['Model', 'Duration', 'Ratio', 'Resolution'].some((name) => controlByLabel(name));
            return submit || anyOption ? true : null;
        `), { label: 'Higgsfield composer', timeoutMs, intervalMs: 1000 });
    } catch (error) {
        if (error instanceof TimeoutError && await looksSignedOut(page)) {
            throw new AuthRequiredError(HOST, 'Higgsfield session is signed out; run `opencli higgsfield login`');
        }
        throw error;
    }
    await dismissAnnouncements(page);
}

/**
 * Close announcement banners (e.g. "Dismiss Seedance 2.5 announcement").
 *
 * These overlay the composer and can swallow a native click aimed at a control
 * underneath, which would otherwise surface as an unexplained "option did not
 * change" failure.
 */
export async function dismissAnnouncements(page) {
    const dismissed = await page.evaluate(browserSource(`
        const buttons = Array.from(document.querySelectorAll('button,[role="button"]')).filter(visible)
            .filter((el) => /^dismiss\\b/i.test(label(el)) || /^(dismiss|close)$/i.test(text(el)));
        buttons.forEach((el) => el.click());
        return buttons.length;
    `));
    if (dismissed) await page.wait(0.4);
    return dismissed;
}

/** Snapshot every composer control we care about, for verification and probing. */
export async function readControls(page) {
    return page.evaluate(browserSource(`
        const pick = (name) => {
            const el = controlByLabel(name);
            return el ? { value: text(el), disabled: !!(el.disabled || el.hasAttribute('disabled')) } : null;
        };
        const toggle = controlByLabel(${JSON.stringify(UNLIMITED_LABEL)});
        const submit = Array.from(document.querySelectorAll('button[type="submit"]')).filter(visible)[0] || null;
        const editor = Array.from(document.querySelectorAll('[contenteditable="true"],textarea')).filter(visible)[0] || null;
        return {
            model: pick('Model'),
            duration: pick('Duration'),
            ratio: pick('Ratio'),
            resolution: pick('Resolution'),
            bitrate: pick('Bitrate'),
            unlimited: toggle ? {
                checked: toggle.getAttribute('aria-checked'),
                value: text(toggle),
            } : null,
            prompt: editor ? {
                tag: editor.tagName,
                length: String(editor.value !== undefined ? editor.value : (editor.innerText || '')).trim().length,
            } : null,
            submit: submit ? {
                text: text(submit),
                disabled: !!(submit.disabled || submit.hasAttribute('disabled')),
            } : null,
        };
    `));
}

/**
 * Locate a visible element and click it with a real mouse event.
 *
 * Higgsfield's composer is React-controlled: dispatching a synthetic click via
 * element.click() updates nothing (verified 2026-07-31 — the Unlimited switch's
 * aria-checked never flipped). Everything therefore goes through nativeClick at
 * the element's viewport centre.
 */
export async function nativeClickBy(page, finderBody, { label, timeoutMs = 15000 } = {}) {
    const spot = await waitForValue(page, browserSource(`
        ${finderBody}
        if (!target || !visible(target)) return null;
        target.scrollIntoView({ block: 'center', inline: 'center' });
        const rect = rectOf(target);
        return rect.w > 0 && rect.h > 0 ? rect : null;
    `), { label: label || 'clickable element', timeoutMs });
    await page.nativeClick(Number(spot.x), Number(spot.y));
    return spot;
}

/** Open an option picker (Model / Duration / Ratio / Resolution) by aria-label. */
export async function openPicker(page, controlLabel) {
    await nativeClickBy(page, `const target = controlByLabel(${JSON.stringify(controlLabel)});`, {
        label: `${controlLabel} picker`,
    });
    await page.wait(0.6);
}

/**
 * Pick an option from an open picker by matching its visible text.
 *
 * `matcher` is a JS expression over `value` (the option's normalized text) that
 * must evaluate truthy for the wanted option.
 */
export async function chooseOption(page, matcher, { label } = {}) {
    await nativeClickBy(page, `
        const options = Array.from(document.querySelectorAll('[role="option"],[role="menuitem"],[role="menuitemradio"],button,li'))
            .filter(visible)
            .filter((el) => !el.disabled && !el.hasAttribute('disabled'));
        const target = options.find((el) => { const value = text(el); return ${matcher}; });
    `, { label: label || 'option' });
    await page.wait(0.6);
}

/**
 * Set an option picker and verify it took. Higgsfield silently keeps the old
 * value when a click lands on the wrong node, so never trust the click alone.
 */
export async function setOption(page, key, matcher, { expect, label } = {}) {
    const controlLabel = CONTROL_LABELS[key];
    if (!controlLabel) throw new CommandExecutionError(`unknown composer control: ${key}`);
    const before = await readControls(page);
    if (expect && before[key] && expect(before[key].value)) return before[key].value;

    await openPicker(page, controlLabel);
    await chooseOption(page, matcher, { label: label || `${controlLabel} option` });

    const after = await readControls(page);
    const value = after[key] ? after[key].value : null;
    if (expect && !(value && expect(value))) {
        throw new CommandExecutionError(
            `Higgsfield ${controlLabel} did not change to the requested value (still "${value ?? 'unknown'}")`,
        );
    }
    return value;
}

/**
 * Wait for the Unlimited-mode switch to mount and report its state.
 *
 * The switch renders noticeably later than the option pickers, and only for
 * models that offer unlimited generation — reading it eagerly returns null even
 * on a Seedance 2.0 composer that will grow one a moment later.
 */
export async function waitForUnlimitedSwitch(page, { timeoutMs = 20000 } = {}) {
    return waitForValue(page, browserSource(`
        const toggle = controlByLabel(${JSON.stringify(UNLIMITED_LABEL)});
        if (!toggle) return null;
        return { checked: toggle.getAttribute('aria-checked') === 'true' };
    `), { label: 'Unlimited mode switch', timeoutMs, intervalMs: 500 });
}

/** Turn the Unlimited-mode switch on or off, verifying aria-checked afterwards. */
export async function setUnlimited(page, wanted) {
    const want = wanted !== false;
    const read = async () => {
        const controls = await readControls(page);
        if (!controls.unlimited) return null;
        return controls.unlimited.checked === 'true';
    };
    let before;
    try {
        before = (await waitForUnlimitedSwitch(page)).checked;
    } catch (error) {
        if (error instanceof TimeoutError) {
            throw new CommandExecutionError(
                'Higgsfield Unlimited mode switch never appeared; the selected model may not support unlimited generation',
            );
        }
        throw error;
    }
    if (before === want) return want;

    await nativeClickBy(page, `const target = controlByLabel(${JSON.stringify(UNLIMITED_LABEL)});`, {
        label: 'Unlimited mode switch',
    });
    await page.wait(0.6);

    const after = await read();
    if (after !== want) {
        throw new CommandExecutionError(
            `Higgsfield Unlimited mode stayed ${after ? 'on' : 'off'} after the toggle; refusing to continue`,
        );
    }
    return after;
}

/**
 * Type the prompt with real keyboard input and verify it landed exactly.
 *
 * The prompt box is a React-controlled rich-text field; setting .value or
 * dispatching synthetic input events leaves the submit button disabled.
 */
export async function fillPrompt(page, prompt) {
    const value = String(prompt ?? '').trim();
    if (!value) throw new CommandExecutionError('prompt is required and cannot be blank');

    await nativeClickBy(page, `
        const target = Array.from(document.querySelectorAll('[contenteditable="true"],textarea'))
            .filter(visible)[0];
    `, { label: 'prompt editor' });
    await page.wait(0.3);

    // Clear whatever the composer restored from its previous session.
    await page.evaluate(browserSource(`
        const editor = Array.from(document.querySelectorAll('[contenteditable="true"],textarea')).filter(visible)[0];
        if (editor) editor.focus();
        return true;
    `));
    await page.pressKey?.('Control+a').catch(() => {});
    await page.pressKey?.('Delete').catch(() => {});
    await page.wait(0.2);

    // Storyboard prompts run to thousands of characters; typing them key by key
    // takes minutes and drops characters, so insert in one shot where possible.
    let inserted = false;
    if (typeof page.insertText === 'function') {
        try {
            await page.insertText(value);
            inserted = true;
        } catch (error) {
            inserted = false;
        }
    }
    if (!inserted) await page.nativeType(value);
    await page.wait(1);

    const actual = await page.evaluate(browserSource(`
        const editor = Array.from(document.querySelectorAll('[contenteditable="true"],textarea')).filter(visible)[0];
        if (!editor) return null;
        return String(editor.value !== undefined ? editor.value : (editor.innerText || editor.textContent || '')).trim();
    `));
    // A rich-text editor re-flows newlines into block elements, so compare the
    // content with whitespace removed: the characters must match exactly, the
    // line breaks need not.
    const squash = (text) => String(text ?? '').replace(/\s+/g, '');
    if (squash(actual) !== squash(value)) {
        throw new CommandExecutionError(
            `Higgsfield prompt did not register exactly (expected ${value.length} chars, editor holds ${actual ? actual.length : 0})`,
        );
    }
    return actual;
}

/**
 * Count reference thumbnails currently attached to the composer.
 *
 * Scoped to the composer column above the prompt editor: the results panel on
 * the right renders the *input* images of past generations, and those must not
 * be touched.
 */
const REFERENCE_THUMBS = `
    const editor = Array.from(document.querySelectorAll('[contenteditable="true"],textarea')).filter(visible)[0];
    const cutoff = editor ? editor.getBoundingClientRect().top : 0;
    // A selected reference renders as an ~48px object-cover <img> inside a
    // rounded <figure> in the left column, above the prompt. The model preview
    // card above it is much wider (~286x124), so bound by size; the "+" add
    // button has no <img>, so it is naturally excluded.
    const thumbs = Array.from(document.querySelectorAll('figure img, img.object-cover, img[class*="object-cover"]'))
        .filter(visible)
        .filter((img) => {
            const rect = img.getBoundingClientRect();
            return rect.top < cutoff && rect.left < 340
                && rect.width >= 32 && rect.width <= 96 && rect.height >= 32 && rect.height <= 96;
        });
`;

export async function countReferences(page) {
    return page.evaluate(browserSource(`${REFERENCE_THUMBS}\nreturn thumbs.length;`));
}

/**
 * Remove every reference image left in the composer.
 *
 * Higgsfield restores the previous session's references (and prompt), so a run
 * that passes no --ref would otherwise silently generate from stale images —
 * confirmed 2026-07-31, when a leftover media id rode along in the job request.
 */
export async function clearReferences(page, { max = 12 } = {}) {
    let removed = 0;
    for (let attempt = 0; attempt < max; attempt += 1) {
        const remaining = await countReferences(page);
        if (!remaining) break;
        const clicked = await page.evaluate(browserSource(`
            ${REFERENCE_THUMBS}
            const thumb = thumbs[0];
            if (!thumb) return null;
            const box = thumb.getBoundingClientRect();
            // The remove control is the tiny "button-xxs" pinned to the
            // thumbnail's top-right corner. Prefer that class; fall back to any
            // small nearby button.
            let card = thumb;
            const near = (el) => {
                const rect = el.getBoundingClientRect();
                if (rect.width > 26 || rect.height > 26) return false;
                return Math.abs(rect.left - box.right) < 72 && Math.abs(rect.top - box.top) < 72;
            };
            for (let i = 0; i < 6 && card.parentElement; i += 1) {
                card = card.parentElement;
                const buttons = Array.from(card.querySelectorAll('button')).filter(visible);
                const remove = buttons.find((el) => /button-xxs/.test(String(el.className || '')) && near(el))
                    || buttons.find((el) => near(el));
                if (remove) {
                    remove.scrollIntoView({ block: 'center' });
                    return rectOf(remove);
                }
            }
            return { missing: true, src: String(thumb.getAttribute('src') || '').slice(0, 120) };
        `));
        if (!clicked || clicked.missing) {
            // Reference tiles hydrate asynchronously; a tile whose remove button
            // has not mounted yet is worth one re-read before giving up.
            await page.wait(1.5);
            if (!await countReferences(page)) break;
            if (attempt > 0) {
                throw new CommandExecutionError(
                    `could not find the remove control for ${remaining} leftover reference image(s) in the composer${clicked?.src ? ` (src: ${clicked.src})` : ''}`,
                );
            }
            continue;
        }
        await page.nativeClick(Number(clicked.x), Number(clicked.y));
        await page.wait(0.6);
        removed += 1;
    }
    const left = await countReferences(page);
    if (left) {
        throw new CommandExecutionError(`composer still holds ${left} reference image(s) after clearing`);
    }
    return removed;
}

/**
 * Asset tiles inside the Elements/Uploads panel, which opens to the right of
 * the composer column. Scoped by x so composer-side thumbnails never match.
 */
const PANEL_TILES = `
    // Anchor on the Uploads tab rather than fixed coordinates: the panel docks
    // on either side depending on viewport, and hard-coded x ranges silently
    // matched the results panel instead.
    const uploadsTab = Array.from(document.querySelectorAll('button')).filter(visible)
        .find((el) => text(el).toLowerCase() === 'uploads');
    const anchor = uploadsTab ? uploadsTab.getBoundingClientRect() : null;
    const tiles = !anchor ? [] : Array.from(document.querySelectorAll('img')).filter(visible).filter((img) => {
        const rect = img.getBoundingClientRect();
        return rect.top >= anchor.bottom - 4 && rect.width >= 40 && rect.height >= 40;
    });
`;

/** Click a visible control by its exact (case-insensitive) text. */
export async function clickByText(page, wanted, { label: what, timeoutMs = 15000 } = {}) {
    return nativeClickBy(page, `
        const target = Array.from(document.querySelectorAll('button,[role="button"],label'))
            .filter(visible)
            .find((el) => text(el).toLowerCase() === ${JSON.stringify(String(wanted).toLowerCase())});
    `, { label: what || `"${wanted}" control`, timeoutMs });
}

/**
 * Open the asset panel on its Uploads tab, where the file input lives.
 *
 * Two controls read "Elements": the composer's own toggle (left column) and the
 * panel's tab (x >= 340). Clicking by text alone hits whichever comes first in
 * the DOM and can close a panel that was already open, so the composer toggle
 * is addressed by position and the whole thing is driven by observed state.
 */
export async function openUploadsTab(page, { attempts = 4 } = {}) {
    const state = () => page.evaluate(browserSource(`
        const buttons = Array.from(document.querySelectorAll('button')).filter(visible);
        const box = Array.from(document.querySelectorAll('input[type="checkbox"]'))
            .find((el) => el.getBoundingClientRect().left < 340) || null;
        return {
            fileInput: document.querySelectorAll('input[type="file"]').length > 0,
            uploadsTab: buttons.some((el) => text(el).toLowerCase() === 'uploads'),
            elementsOn: box ? !!box.checked : null,
            leftButtons: buttons.filter((el) => el.getBoundingClientRect().left < 340)
                .map((el) => text(el).slice(0, 18)).filter(Boolean).slice(0, 10),
        };
    `));

    for (let attempt = 0; attempt < attempts; attempt += 1) {
        const current = await state();
        if (current.fileInput) return true;

        // The Elements feature has its own on/off switch; the panel button does
        // nothing while it is off.
        if (current.elementsOn === false) {
            await nativeClickBy(page, `
                const box = Array.from(document.querySelectorAll('input[type="checkbox"]'))
                    .find((el) => el.getBoundingClientRect().left < 340);
                const target = box ? (box.closest('label') || box.parentElement) : null;
            `, { label: 'Elements switch' }).catch(() => {});
            await page.wait(1.5);
            continue;
        }

        if (current.uploadsTab) {
            await nativeClickBy(page, `
                const target = Array.from(document.querySelectorAll('button')).filter(visible)
                    .find((el) => text(el).toLowerCase() === 'uploads');
            `, { label: 'Uploads tab' });
        } else {
            // Prefer the composer's own Elements control (the one paired with
            // the on/off switch) over the panel tab of the same name.
            await nativeClickBy(page, `
                const buttons = Array.from(document.querySelectorAll('button')).filter(visible)
                    .filter((el) => text(el).toLowerCase() === 'elements');
                const box = Array.from(document.querySelectorAll('input[type="checkbox"]')).filter((el) => true)[0];
                const anchor = box ? box.getBoundingClientRect() : null;
                const target = (anchor && buttons.find((el) => {
                    const rect = el.getBoundingClientRect();
                    return Math.abs(rect.top - anchor.top) < 40;
                })) || buttons[0];
            `, { label: 'Elements toggle' });
        }
        await page.wait(2);
    }

    const final = await state();
    if (final.fileInput) return true;
    throw new CommandExecutionError(
        `could not open the Higgsfield asset upload panel: ${JSON.stringify(final)}`,
    );
}

/**
 * Upload local images into the composer's media library.
 *
 * Uploading does NOT attach the image to the prompt — it only registers it so
 * it can be @-mentioned afterwards. Returns the filenames as the library shows
 * them, in upload order, for the mention step to reference.
 */
export async function uploadToLibrary(page, paths) {
    if (!paths.length) return [];

    // Stream each file into the page in chunks. A single evaluate() carrying a
    // multi-megabyte base64 string overruns the extension's command channel
    // ("max attempts exhausted"), so assemble the File incrementally in-page.
    await page.evaluate('window.__hfFiles = [];');
    const CHUNK = 256 * 1024;
    for (const path of paths) {
        const name = basename(path);
        const extension = (name.split('.').pop() || '').toLowerCase();
        const mime = extension === 'png' ? 'image/png'
            : extension === 'webp' ? 'image/webp'
            : 'image/jpeg';
        const base64 = (await readFile(path)).toString('base64');
        await page.evaluate('window.__hfChunks = [];');
        for (let index = 0; index < base64.length; index += CHUNK) {
            const part = base64.slice(index, index + CHUNK);
            await page.evaluate(`window.__hfChunks.push(${JSON.stringify(part)});`);
        }
        const count = await page.evaluate(`(() => {
            const b64 = window.__hfChunks.join('');
            window.__hfChunks = [];
            const binary = atob(b64);
            const data = new Uint8Array(binary.length);
            for (let i = 0; i < binary.length; i += 1) data[i] = binary.charCodeAt(i);
            window.__hfFiles.push(new File([data], ${JSON.stringify(name)}, { type: ${JSON.stringify(mime)}, lastModified: Date.now() }));
            return window.__hfFiles.length;
        })()`);
        if (!count) throw new CommandExecutionError(`could not assemble "${name}" in the page`);
    }

    // The "Upload media" area is a react-dropzone dashed box with no file input
    // of its own; a real click opens the OS dialog (which hangs the command).
    // Synthesize a drag-and-drop onto the zone with the files attached to the
    // event's DataTransfer — exactly what react-dropzone reads.
    const dropped = await page.evaluate(browserSource(`
        const zone = Array.from(document.querySelectorAll('div,section')).filter((el) => {
            const rect = el.getBoundingClientRect();
            if (rect.width <= 0 || rect.height <= 0) return false;
            const cls = String(el.className || '');
            return /border-dashed/.test(cls) || /upload media/i.test(el.textContent || '');
        }).sort((a, b) => (a.textContent || '').length - (b.textContent || '').length)[0];
        if (!zone) return { ok: false, stage: 'no-zone' };

        const dataTransfer = new DataTransfer();
        (window.__hfFiles || []).forEach((file) => dataTransfer.items.add(file));

        const fire = (type, target) => {
            let event;
            try {
                event = new DragEvent(type, { bubbles: true, cancelable: true, composed: true, dataTransfer });
            } catch (error) {
                event = new Event(type, { bubbles: true, cancelable: true, composed: true });
            }
            if (!event.dataTransfer) {
                Object.defineProperty(event, 'dataTransfer', { value: dataTransfer, configurable: true });
            }
            target.dispatchEvent(event);
        };
        const inner = zone.querySelector('div') || zone;
        for (const target of new Set([zone, inner])) {
            fire('dragenter', target);
            fire('dragover', target);
            fire('drop', target);
        }
        const wanted = (window.__hfFiles || []).length;
        return { ok: dataTransfer.files.length === wanted, files: dataTransfer.files.length };
    `));

    if (!dropped || !dropped.ok) {
        throw new CommandExecutionError(`could not drop files onto the Higgsfield upload zone (${JSON.stringify(dropped)})`);
    }
    return paths.map((path) => basename(path));
}

/**
 * Poll the result panel until the newest generation produces a downloadable
 * video, then return its URL. Throws on an explicit failure.
 *
 * The model preview loops a placeholder (…/seedance_2_video/seedance_2.mov), so
 * that URL is excluded; a real result lives on the user-media CDN.
 */
export async function waitForGeneration(page, { timeoutMs = 1200000, intervalMs = 8000 } = {}) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        const state = await page.evaluate(browserSource(`
            const main = (el) => el.getBoundingClientRect().left > 320;
            // A real result lives on the media CDN; exclude the looping model
            // preview and feed samples.
            const bad = /seedance_2\\.mov|\\/feed\\//;
            const vids = Array.from(document.querySelectorAll('video')).filter(visible).filter(main)
                .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
            for (const v of vids) {
                const src = v.getAttribute('src') || v.currentSrc || '';
                const source = v.querySelector('source');
                const ssrc = source ? (source.getAttribute('src') || '') : '';
                const url = /^https?:/.test(src) && !bad.test(src) ? src
                    : (/^https?:/.test(ssrc) && !bad.test(ssrc) ? ssrc : '');
                if (url) return { status: 'ready', url };
            }
            // A running job keeps a Generating/Queued indicator on screen. Its
            // presence outranks any older Failed card lower in the history, so
            // the poll does not mistake a past failure for this run's outcome.
            const running = Array.from(document.querySelectorAll('*')).filter(visible).filter(main)
                .some((el) => /^(generating|queued|in queue|processing|starting)\\b/i.test(text(el)) && text(el).length < 60);
            if (running) return { status: 'running' };
            // Only conclude failure once nothing is generating and no video
            // exists — i.e. this run's own card resolved to Failed.
            const failed = Array.from(document.querySelectorAll('*')).filter(visible).filter(main)
                .some((el) => /^failed\\b/i.test(text(el)) && text(el).length < 60);
            if (failed) return { status: 'failed' };
            return { status: 'pending' };
        `));
        if (state?.status === 'ready') return state;
        if (state?.status === 'failed') {
            throw new CommandExecutionError('Higgsfield reported the generation as Failed (credits are refunded)');
        }
        await page.wait(Math.min(intervalMs / 1000, Math.max(0.2, (deadline - Date.now()) / 1000)));
    }
    throw new CommandExecutionError('timed out waiting for the generated video');
}

/**
 * Download a generated video to `out`.
 *
 * Tries a direct server-side fetch first; if the CDN refuses it (hotlink
 * protection), falls back to fetching inside the authenticated page and
 * streaming the bytes back in chunks.
 */
export async function downloadVideo(page, url, out) {
    await mkdir(dirname(out), { recursive: true }).catch(() => {});

    try {
        const response = await fetch(url, { headers: { referer: `${BASE_URL}/` } });
        if (response.ok) {
            const buffer = Buffer.from(await response.arrayBuffer());
            if (buffer.length > 1024) {
                await writeFile(out, buffer);
                return { path: out, bytes: buffer.length, via: 'direct' };
            }
        }
    } catch (error) {
        // fall through to the in-page path
    }

    const total = await page.evaluate(`(async () => {
        const response = await fetch(${JSON.stringify(url)}, { credentials: 'include' });
        if (!response.ok) return { ok: false, status: response.status };
        const bytes = new Uint8Array(await response.arrayBuffer());
        let binary = '';
        for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
        window.__hfVideo = btoa(binary);
        return { ok: true, length: window.__hfVideo.length };
    })()`);
    if (!total || !total.ok) {
        throw new CommandExecutionError(`could not download the video (${JSON.stringify(total)})`);
    }
    const CHUNK = 256 * 1024;
    const parts = [];
    for (let offset = 0; offset < total.length; offset += CHUNK) {
        const part = await page.evaluate(`window.__hfVideo.slice(${offset}, ${offset + CHUNK})`);
        parts.push(part);
    }
    await page.evaluate('window.__hfVideo = undefined;').catch(() => {});
    const buffer = Buffer.from(parts.join(''), 'base64');
    await writeFile(out, buffer);
    return { path: out, bytes: buffer.length, via: 'page' };
}

/** CSS/URL fragments that identify a user-uploaded media asset. */
const USER_MEDIA_RE = "/images\\\\.higgs\\\\.ai|cloudfront\\\\.net|%2Fuser_|\\\\/user_/";

/**
 * Locate the media library's Uploads grid and its user-media tiles, newest
 * first. The grid opens beside/over the composer after a drop; anchor on the
 * "Uploads" tab so the results panel is never matched by mistake.
 */
const LIBRARY_UPLOAD_TILES = `
    const uploadsTab = Array.from(document.querySelectorAll('button')).filter(visible)
        .find((el) => text(el).toLowerCase() === 'uploads');
    const anchor = uploadsTab ? uploadsTab.getBoundingClientRect() : null;
    const userMedia = ${USER_MEDIA_RE};
    const tiles = !anchor ? [] : Array.from(document.querySelectorAll('img')).filter(visible)
        .filter((img) => {
            const rect = img.getBoundingClientRect();
            if (!(rect.top >= anchor.bottom - 4 && rect.width >= 60 && rect.height >= 60)) return false;
            return userMedia.test(img.getAttribute('src') || '');
        })
        // Newest upload sits at the top-left of the grid.
        .sort((a, b) => {
            const ra = a.getBoundingClientRect();
            const rb = b.getBoundingClientRect();
            return (ra.top - rb.top) || (ra.left - rb.left);
        });
`;

/** Whether the library still shows an in-progress upload. */
export async function isUploading(page) {
    return page.evaluate(browserSource(`
        return /uploading/i.test(document.body.innerText || '') ? true : null;
    `));
}

/**
 * Wait until the just-dropped upload finishes and its tile is ready in the
 * Uploads grid. `baseline` is the tile count before this upload.
 */
export async function waitUploadReady(page, baseline, { timeoutMs = 180000 } = {}) {
    const failed = await page.evaluate(browserSource(`
        return /failed to upload/i.test(document.body.innerText || '') ? true : null;
    `));
    if (failed) throw new CommandExecutionError('Higgsfield reported "Failed to upload media"');
    return waitForValue(page, browserSource(`
        if (/failed to upload/i.test(document.body.innerText || '')) return null;
        ${LIBRARY_UPLOAD_TILES}
        return tiles.length > ${baseline} ? tiles.length : null;
    `), { label: 'upload to finish', timeoutMs, intervalMs: 2000 });
}

/** Count user-media tiles currently in the Uploads grid. */
export async function countLibraryTiles(page) {
    return page.evaluate(browserSource(`${LIBRARY_UPLOAD_TILES}\nreturn tiles.length;`));
}

/**
 * Click the newest tile in the Uploads grid to select it into the current
 * generation. Per Higgsfield's UX, a single click adds it as a reference, and
 * the order of clicks becomes the "图片1/2/3/…" order in the prompt.
 */
export async function selectNewestUpload(page) {
    const spot = await page.evaluate(browserSource(`
        ${LIBRARY_UPLOAD_TILES}
        const tile = tiles[0];
        if (!tile) return null;
        const target = tile.closest('button,[role="button"],li,[data-testid],figure') || tile;
        target.scrollIntoView({ block: 'center' });
        return rectOf(target);
    `));
    if (!spot) throw new CommandExecutionError('no uploaded tile was available to select');
    await page.nativeClick(Number(spot.x), Number(spot.y));
    await page.wait(1.2);
    return spot;
}

/**
 * Upload each image and select it into the generation, one at a time so the
 * selection order matches the caller's order (which the prompt refers to as
 * 图片1/2/3/…). Returns the number attached.
 */
export async function uploadAndSelect(page, paths) {
    let attached = 0;
    for (const path of paths) {
        const baseline = await countLibraryTiles(page).catch(() => 0);
        await uploadToLibrary(page, [path]);
        await waitUploadReady(page, baseline);
        await selectNewestUpload(page);
        attached += 1;
    }
    return attached;
}

/**
 * Wrap window.fetch so the composer's own job request records its status and
 * response body.
 *
 * OpenCLI's network capture reports these requests with a null status, which
 * cannot distinguish "accepted" from "rejected" — and a submit that silently
 * fails looks identical to a successful one from the DOM. This observes the
 * page's own request; it does not replay or forge one.
 */
export async function installSubmitProbe(page) {
    return page.evaluate(`(() => {
        if (window.__hfSubmitProbe) { window.__hfSubmitProbe.length = 0; return 'reused'; }
        window.__hfSubmitProbe = [];

        // Higgsfield's client is axios-based, so the job request goes out over
        // XMLHttpRequest rather than fetch. Observe both transports.
        const XHR = window.XMLHttpRequest;
        if (XHR && XHR.prototype) {
            const originalOpen = XHR.prototype.open;
            const originalSend = XHR.prototype.send;
            XHR.prototype.open = function (method, url) {
                this.__hfMethod = String(method || '').toUpperCase();
                this.__hfUrl = String(url || '');
                return originalOpen.apply(this, arguments);
            };
            XHR.prototype.send = function (body) {
                const url = String(this.__hfUrl || '');
                if (/\\/jobs/.test(url)) {
                    let requestBody = null;
                    try { requestBody = typeof body === 'string' ? body.slice(0, 4000) : null; } catch (error) { requestBody = null; }
                    this.addEventListener('loadend', () => {
                        window.__hfSubmitProbe.push({
                            transport: 'xhr',
                            url,
                            method: this.__hfMethod || 'GET',
                            status: this.status,
                            requestBody,
                            response: String(this.responseText || '').slice(0, 2000),
                        });
                    });
                }
                return originalSend.apply(this, arguments);
            };
        }

        const original = window.fetch;
        window.fetch = async function (...args) {
            const input = args[0];
            const url = String((input && input.url) || input || '');
            const init = args[1] || {};
            const method = String(init.method || (input && input.method) || 'GET').toUpperCase();
            const interesting = /\\/jobs/.test(url);
            let requestBody = null;
            if (interesting) {
                try {
                    requestBody = typeof init.body === 'string' ? init.body.slice(0, 4000) : null;
                } catch (error) { requestBody = null; }
            }
            let response;
            try {
                response = await original.apply(this, args);
            } catch (error) {
                if (interesting) {
                    window.__hfSubmitProbe.push({ url, method, error: String((error && error.message) || error), requestBody });
                }
                throw error;
            }
            if (interesting) {
                let text = null;
                try { text = await response.clone().text(); } catch (error) { text = null; }
                window.__hfSubmitProbe.push({
                    url,
                    method,
                    status: response.status,
                    requestBody,
                    response: String(text || '').slice(0, 2000),
                });
            }
            return response;
        };
        return 'installed';
    })()`);
}

/** Read whatever the submit probe recorded. */
export async function readSubmitProbe(page) {
    return page.evaluate('(() => (window.__hfSubmitProbe || []).slice(0, 10))()');
}

/** Read the submit button without clicking it. */
export async function readSubmit(page) {
    const controls = await readControls(page);
    return controls.submit;
}

/**
 * Click Generate. This is the billable step — callers must have verified every
 * option first, because Higgsfield charges on submit and does not refund a
 * mis-configured run.
 */
export async function clickGenerate(page) {
    const submit = await readSubmit(page);
    if (!submit) throw new CommandExecutionError('Higgsfield Generate button was not found');
    if (submit.disabled) throw new CommandExecutionError(`Higgsfield Generate button is disabled ("${submit.text}")`);
    await nativeClickBy(page, `
        const target = Array.from(document.querySelectorAll('button[type="submit"]')).filter(visible)[0];
    `, { label: 'Generate button' });
    return submit.text;
}

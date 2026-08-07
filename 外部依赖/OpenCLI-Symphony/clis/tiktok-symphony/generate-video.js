// tiktok-symphony generate-video — render a clip with the Video composer.
import { cli, Strategy } from '@jackwener/opencli/registry';
import { ArgumentError, CommandExecutionError, TimeoutError } from '@jackwener/opencli/errors';
import {
    CARD_QUERY_SRC,
    COMPOSER_URL,
    DEEP_QUERY_SRC,
    DROP_ZONE_SELECTOR,
    HOST,
    PROMPT_SELECTOR,
    REF_COUNT_SRC,
    VIDEO_DURATIONS,
    VIDEO_FRAME_MODES,
    VIDEO_MODELS,
    VIDEO_MODES,
    attachReference,
    normalizePositiveInteger,
    readReferenceImages,
    persistReferenceCache,
    resolveVideoDuration,
    resolveVideoFrameMode,
    resolveVideoMode,
    resolveVideoModel,
    selectFooterOption,
    setVideoDuration,
    submitComposer,
    typePrompt,
    waitForValue,
} from './utils.js';

const DURATION_LABELS = VIDEO_DURATIONS.map((d) => `${d}s`);
const FRAME_LABELS = VIDEO_FRAME_MODES.map((f) => f.label);

/**
 * The clip rendered by one generation card.
 *
 * A finished card swaps its progress readout for a <video>. Note the playback
 * URL is NOT on the `ad-creative-sg` host that images use — it is served from
 * `v16-ad-creative.tiktokcdn-row.com/video/tos/...` and carries no assetId at
 * all. The only thing to exclude is the page's static demo clip, which sits
 * outside any card but is cheap to rule out by host.
 */
function cardStateSrc(prompt) {
    return `(() => {
        ${CARD_QUERY_SRC}
        const card = __cardFor(${JSON.stringify(prompt)});
        if (!card) return null;
        const state = __cardState(card);
        state.clip = null;
        for (const v of __deepAll(card, (el) => el.tagName === 'VIDEO')) {
            const src = v.src || v.currentSrc || '';
            if (src && !/lf-creative-factory/.test(src)) { state.clip = src; break; }
        }
        return state;
    })()`;
}

cli({
    site: 'tiktok-symphony',
    name: 'generate-video',
    aliases: ['video'],
    description: 'Generate a video clip from a prompt and optional reference images (spends Symphony credits)',
    access: 'write',
    example: 'opencli tiktok-symphony generate-video "a duck floating on a pool, slow zoom out" --duration 5',
    domain: HOST,
    strategy: Strategy.UI,
    browser: true,
    navigateBefore: false,
    // The Video composer hydrates and accepts page-context input in a
    // background automation window. Keep a stable site lease so repeated
    // storyboard submissions reuse one Chrome tab without stealing focus or
    // dropping back to about:blank after every command.
    defaultWindowMode: 'background',
    siteSession: 'persistent',
    args: [
        { name: 'prompt', type: 'string', required: true, positional: true, help: 'What to generate' },
        {
            name: 'mode',
            type: 'string',
            default: 'auto',
            help: `Sub-app: ${VIDEO_MODES.map((m) => m.key).join(' / ')}. "auto" picks text with no refs, image with 1, reference with 2+`,
        },
        { name: 'refs', type: 'string', default: '', help: 'Comma-separated reference image paths (image: 1-2, reference: 1-4)' },
        { name: 'frames', type: 'string', default: 'first', help: `Image mode only: ${VIDEO_FRAME_MODES.map((f) => f.key).join(' / ')}` },
        { name: 'model', type: 'string', default: VIDEO_MODELS[0], help: `Model: ${VIDEO_MODELS.join(' / ')}` },
        { name: 'duration', type: 'string', default: '5', help: `Clip length: ${DURATION_LABELS.join(' / ')}` },
        { name: 'timeout', type: 'int', default: 5400, help: 'Seconds to wait when --wait true (rendering has been observed to exceed an hour)' },
        {
            name: 'wait',
            type: 'boolean',
            default: false,
            help: 'Block until the clip renders. Off by default: rendering runs for tens of minutes, so the job is submitted and `generations` picks it up later',
        },
        {
            name: 'prepare-only',
            type: 'boolean',
            default: false,
            help: 'Attach references and type the prompt without submitting or spending credits',
        },
    ],
    columns: ['index', 'status', 'assetId', 'url', 'mode', 'model', 'duration', 'prompt'],
    func: async (page, args) => {
        const prompt = String(args.prompt ?? '').trim();
        if (!prompt) throw new ArgumentError('prompt is required and cannot be blank');

        const model = resolveVideoModel(args.model);
        const duration = resolveVideoDuration(args.duration);
        const timeoutSec = normalizePositiveInteger(args.timeout, 5400, 'timeout', { min: 60 });

        const refPaths = String(args.refs ?? '').split(',').map((s) => s.trim()).filter(Boolean);
        const mode = String(args.mode ?? 'auto').trim().toLowerCase() === 'auto'
            ? autoMode(refPaths.length)
            : resolveVideoMode(args.mode);

        const frameMode = resolveVideoFrameMode(args.frames);
        const maxRefs = mode.key === 'image' ? frameMode.refs : mode.maxRefs;

        if (refPaths.length < mode.minRefs) {
            throw new ArgumentError(`mode "${mode.key}" needs at least ${mode.minRefs} reference image(s) — pass --refs`);
        }
        if (mode.key === 'text' && refPaths.length > 0) {
            throw new ArgumentError('mode "text" takes no reference images — use --mode image or --mode reference');
        }
        if (mode.key !== 'image' && frameMode.key !== VIDEO_FRAME_MODES[0].key) {
            // Only the Image sub-app renders this dropdown; accepting the flag
            // elsewhere would silently drop it.
            throw new ArgumentError(`--frames only applies to --mode image, not "${mode.key}"`);
        }
        if (mode.key === 'image' && frameMode.key === 'first-last' && refPaths.length !== 2) {
            throw new ArgumentError('--frames first-last needs exactly 2 reference images (start and end frame)');
        }

        const references = await readReferenceImages(args.refs, maxRefs);

        // Land on the sub-app URL directly: each Video mode is its own `subApp`,
        // so navigating there beats clicking the mode dropdown. A same-URL
        // navigation can be absorbed by the SPA and preserve the last
        // submission's references. A throwaway query value forces a fresh
        // document while keeping the persistent browser lease and login
        // profile intact (the bridge intentionally blocks about:blank here).
        const freshUrl = new URL(COMPOSER_URL);
        freshUrl.searchParams.set('subApp', mode.subApp);
        freshUrl.searchParams.set('_ocFresh', `${Date.now()}`);
        await page.goto(freshUrl.toString(), { waitUntil: 'load' });
        await waitForValue(page, `(() => !!document.querySelector('${DROP_ZONE_SELECTOR}') || null)()`, {
            label: 'Symphony composer',
            timeoutMs: 30000,
        });

        // Opt-in protocol discovery for moving this adapter off UI automation.
        // The emitted summary deliberately excludes headers and redacts auth,
        // account and signature fields; raw captures must never reach logs.
        const protocolCapturing = process.env.OC_SYMPHONY_PROTOCOL_PROBE
            ? await page.startNetworkCapture('').catch(() => false)
            : false;

        // The model dropdown only carries these names on the Video tab, so its
        // presence doubles as proof the right sub-app hydrated.
        await selectFooterOption(page, { allowed: VIDEO_MODELS, target: model, label: 'model' });

        const staleRefs = await page.evaluate(REF_COUNT_SRC);
        if (staleRefs) {
            throw new CommandExecutionError(
                `composer still holds ${staleRefs} reference image(s) after reload — refusing to generate with unknown inputs`,
            );
        }

        if (mode.key === 'image') {
            await selectFooterOption(page, { allowed: FRAME_LABELS, target: frameMode.label, label: 'frames' });
        }

        for (const [i, ref] of references.entries()) {
            await attachReference(page, ref, i);
        }

        // Per-image waits only prove the count never went backwards. Assert the
        // final tally too: generating with fewer references than the caller
        // asked for still spends full credits, and the clip silently ignores
        // whichever subject never made it into the composer.
        const attached = await page.evaluate(REF_COUNT_SRC);
        if (attached !== references.length) {
            throw new CommandExecutionError(
                `composer holds ${attached} reference image(s) but ${references.length} were requested — refusing to spend credits on an incomplete set`,
            );
        }
        await persistReferenceCache(page, references).catch((error) => {
            process.stderr.write(`\nWarning: direct asset cache was not updated: ${error?.message || error}\n`);
        });

        await setVideoDuration(page, duration.seconds);
        await typePrompt(page, prompt);

        if (!shouldSubmitGeneration(args)) {
            // Nothing here may touch the editor: prepare-only exists to preview
            // exactly what a real run would submit, so anything typed for the
            // sake of inspection would make the preview a lie.
            const diagnostics = await inspectPreparedComposer(page);
            if (process.env.OC_SYMPHONY_SHOT) {
                await page.screenshot({ path: process.env.OC_SYMPHONY_SHOT, fullPage: false });
            }
            return [{
                index: 1,
                status: 'prepared',
                assetId: null,
                url: null,
                mode: mode.key,
                model,
                duration: duration.label,
                referenceCount: references.length,
                prompt,
                diagnostics,
            }];
        }

        await submitComposer(page);

        const stateSrc = cardStateSrc(prompt);

        // A card carrying a progress readout is the site acknowledging the job;
        // without it we would report success for a request that never started.
        const started = await waitForValue(page, `(() => {
            const s = ${stateSrc};
            return s && (s.cardProgress !== null || s.cardErrorCode || s.clip) ? s : null;
        })()`, { label: 'generation to start', timeoutMs: 120000, intervalMs: 2000 });
        rejectIfFailed(started);

        if (protocolCapturing) {
            await page.wait(1);
            const captured = await page.readNetworkCapture().catch(() => []);
            const summary = {
                endpoints: summarizeProtocolEndpoints(captured),
                transactions: summarizeProtocolCapture(captured),
            };
            process.stderr.write(`\nOC_SYMPHONY_PROTOCOL ${JSON.stringify(summary, null, 2)}\n`);
        }

        const row = {
            index: 1,
            status: 'generating',
            assetId: null,
            url: null,
            mode: mode.key,
            model,
            duration: duration.label,
            prompt,
        };

        if (!args.wait) return [row];

        const url = await waitForClip(page, stateSrc, timeoutSec);
        return [{ ...row, status: 'ready', assetId: assetIdFromVideoUrl(url), url }];
    },
});

/** Prepare-only is an explicit safety interlock; normal calls still submit. */
export function shouldSubmitGeneration(args = {}) {
    return args['prepare-only'] !== true;
}

/**
 * Report exactly what a real run would submit, without clicking Send.
 *
 * `editorHTML` is worth carrying alongside the text: the site keeps each blank
 * line as its own paragraph node, and a prompt whose shot boundaries collapsed
 * into one block reads as a different script to the model while looking fine
 * as plain text.
 */
export async function inspectPreparedComposer(page) {
    return page.evaluate(`(() => {
        ${DEEP_QUERY_SRC}
        const editor = document.querySelector('${PROMPT_SELECTOR}');
        const refs = __deepAll(document, (el) => el.tagName === 'IMG')
            .map((img) => img.src || '')
            .filter((s) => /ibyteimg\\.com|creative-tool/.test(s));
        return {
            referenceCount: ${REF_COUNT_SRC},
            referenceSrcs: [...new Set(refs)].map((s) => s.slice(0, 160)),
            editorText: editor ? editor.textContent : null,
            editorHTML: editor ? editor.innerHTML : null,
        };
    })()`);
}

const SENSITIVE_FIELD = /auth|authorization|cookie|csrf|token|ticket|secret|access_?key|signature|(?:^|_)sign(?:_|$)|session|passport|(?:user|account|advertiser|device|client|unique|web|fp|tt4b|aio)_?id|^(?:uid|did)$/i;

/** Return only the protocol details needed to implement a direct API client. */
export function summarizeProtocolCapture(entries = []) {
    return entries
        .filter((entry) => {
            const method = String(entry?.method || '').toUpperCase();
            const url = String(entry?.url || '');
            if (method === 'PUT') return true;
            return ['POST', 'PATCH'].includes(method)
                && /ads\.tiktok\.com\/creative_bff_i18n\/api\/cue\/(?:upload\/token|save_to_my_library|i2v\/gen_r2v_video|generate-task(?:\/(?!check)[^?]*)?)(?:[?]|$)/i.test(url);
        })
        .map((entry) => ({
            method: String(entry.method || '').toUpperCase(),
            url: sanitizeProtocolUrl(entry.url),
            requestHeaderNames: Object.keys(entry.requestHeaders || {})
                .map((key) => key.toLowerCase())
                .filter((key) => /content-type|x-creative-source|x-csrftoken|x-fp-id/.test(key))
                .sort(),
            request: sanitizeProtocolBody(entry.requestBodyPreview, entry.requestHeaders),
            requestBytes: entry.requestBodyFullSize ?? null,
            requestTruncated: entry.requestBodyTruncated === true,
            responseStatus: entry.responseStatus ?? null,
            response: sanitizeProtocolBody(entry.responsePreview, entry.responseHeaders),
            responseBytes: entry.responseBodyFullSize ?? null,
            responseTruncated: entry.responseBodyTruncated === true,
        }));
}

/** List candidate write endpoints without ever retaining headers or bodies. */
export function summarizeProtocolEndpoints(entries = []) {
    return [...new Set(entries
        .filter((entry) => ['POST', 'PUT', 'PATCH'].includes(String(entry?.method || '').toUpperCase()))
        .filter((entry) => !/mcs\.byteoversea|monitor_browser|slardar|applog|\/collect(?:\/|[?]|$)/i.test(String(entry?.url || '')))
        .map((entry) => `${String(entry.method || '').toUpperCase()} ${sanitizeProtocolUrl(entry.url)}`))]
        .sort();
}

function sanitizeProtocolUrl(raw) {
    try {
        const url = new URL(String(raw || ''));
        const keys = [...new Set(url.searchParams.keys())].sort();
        return `${url.origin}${url.pathname}${keys.length ? `?${keys.map((key) => `${encodeURIComponent(key)}=<redacted>`).join('&')}` : ''}`;
    } catch {
        return '<invalid-url>';
    }
}

function sanitizeProtocolBody(raw, headers = {}) {
    if (typeof raw !== 'string' || !raw) return null;
    const contentType = Object.entries(headers || {})
        .find(([key]) => key.toLowerCase() === 'content-type')?.[1] || '';
    if (/multipart|octet-stream|image\/|video\//i.test(contentType) || raw.startsWith('base64:')) {
        return `<binary body omitted: ${contentType || 'unknown content type'}>`;
    }
    try {
        return sanitizeProtocolValue(JSON.parse(raw));
    } catch {}
    if (/application\/x-www-form-urlencoded/i.test(contentType) || /^[^=&]+=[^&]*/.test(raw)) {
        const form = {};
        for (const [key, value] of new URLSearchParams(raw)) {
            form[key] = SENSITIVE_FIELD.test(key) ? '<redacted>' : sanitizeProtocolValue(value);
        }
        return form;
    }
    return raw.length > 500 ? `${raw.slice(0, 500)}…` : raw;
}

function sanitizeProtocolValue(value, key = '') {
    if (SENSITIVE_FIELD.test(key)) return '<redacted>';
    if (Array.isArray(value)) return value.map((item) => sanitizeProtocolValue(item));
    if (value && typeof value === 'object') {
        return Object.fromEntries(Object.entries(value)
            .map(([childKey, child]) => [childKey, sanitizeProtocolValue(child, childKey)]));
    }
    if (typeof value === 'string' && /^[\[{]/.test(value.trim())) {
        try {
            return sanitizeProtocolValue(JSON.parse(value));
        } catch {}
    }
    if (typeof value === 'string' && /^https?:\/\//i.test(value)) return sanitizeProtocolUrl(value);
    if (typeof value === 'string' && value.length > 500) return `${value.slice(0, 500)}…`;
    return value;
}

/** Pick the sub-app that matches how many references the caller supplied. */
function autoMode(refCount) {
    if (refCount === 0) return resolveVideoMode('text');
    if (refCount === 1) return resolveVideoMode('image');
    return resolveVideoMode('reference');
}

/**
 * Wait for the clip to finish.
 *
 * Rendering is slow (tens of minutes) and the card exposes a percentage that
 * stalls near the end, so the only trustworthy completion signal is a new
 * <video> mounting in the feed.
 */
async function waitForClip(page, stateSrc, timeoutSec) {
    const deadline = Date.now() + timeoutSec * 1000;

    for (;;) {
        const state = await page.evaluate(stateSrc);

        // The feed keeps only a handful of cards: a burst of other generations
        // can push ours out while we wait, and silently polling a card that no
        // longer exists would look identical to "still rendering".
        if (!state) {
            throw new CommandExecutionError(
                'the generation card left the Create feed before the clip appeared — check `opencli tiktok-symphony jobs`',
            );
        }
        rejectIfFailed(state);
        if (state.clip) return state.clip;

        if (Date.now() >= deadline) throw new TimeoutError('video generation', timeoutSec);
        await new Promise((r) => setTimeout(r, 10000));
    }
}

/**
 * Surface a rejected render as a failure.
 *
 * Moderation refusals arrive as ordinary card text, not a dialog or an HTTP
 * error, so nothing else stops the command from reporting a clip that will
 * never exist. The credits are spent either way.
 */
function rejectIfFailed(state) {
    if (!state?.errorCode) return;
    const detail = state.cardErrorText || 'the site rejected this generation';
    const task = state.cardTaskId ? ` (task ${state.cardTaskId})` : '';
    throw new CommandExecutionError(`generation rejected [${state.cardErrorCode}]${task}: ${detail}`);
}

/** Clip URLs carry the same `/ad-creative-sg/<assetId>~...` handle as images. */
function assetIdFromVideoUrl(url) {
    const m = /\/ad-creative-sg\/([A-Za-z0-9]+)[~/?]/.exec(String(url || ''));
    return m ? m[1] : null;
}

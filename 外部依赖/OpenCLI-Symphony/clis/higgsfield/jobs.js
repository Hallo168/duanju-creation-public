import { resolve } from 'node:path';
import { cli, Strategy } from '@jackwener/opencli/registry';
import {
    HOST,
    browserSource,
    dismissAnnouncements,
    downloadVideo,
    looksSignedOut,
    waitForGeneration,
    waitForValue,
} from './utils.js';
import { AuthRequiredError } from '@jackwener/opencli/errors';

// Generations render in the panel beside the composer, not on /asset/all —
// that page only lists finished assets and stays empty while a job runs or
// after it fails.
const HISTORY_URL = `https://${HOST}/ai/video`;

/**
 * List recent generations so a submit can be confirmed independently of the
 * click that produced it, and so a caller can poll for completion.
 */
cli({
    site: 'higgsfield',
    name: 'jobs',
    description: 'List recent Higgsfield generations with their status and video URLs',
    access: 'read',
    domain: HOST,
    strategy: Strategy.UI,
    browser: true,
    navigateBefore: false,
    defaultWindowMode: 'background',
    siteSession: 'persistent',
    args: [
        { name: 'limit', type: 'int', default: 10, help: 'Maximum number of generations to return' },
        { name: 'url', type: 'string', default: HISTORY_URL, help: 'Page to read generations from' },
        { name: 'wait', type: 'boolean', default: false, help: 'Wait for the newest generation to finish rendering' },
        { name: 'out', type: 'string', default: '', help: 'Download the newest finished video to this path (implies --wait)' },
        { name: 'timeout', type: 'int', default: 1200, help: 'Seconds to wait when --wait/--out is set' },
        { name: 'raw', type: 'boolean', default: false, help: 'Dump the raw page structure for selector debugging' },
    ],
    columns: ['index', 'status', 'title', 'video', 'poster', 'href', 'download'],
    func: async (page, args) => {
        await page.resizeWindow?.(1600, 1000).catch(() => {});
        await page.goto(String(args.url || HISTORY_URL), { waitUntil: 'load' });
        if (await looksSignedOut(page)) {
            throw new AuthRequiredError(HOST, 'Higgsfield session is signed out; run `opencli higgsfield login`');
        }
        await dismissAnnouncements(page);

        // Wait/download the newest generation without re-submitting, so a poll
        // or download can be retried independently of the job that created it.
        const out = String(args.out ?? '').trim();
        if (args.wait === true || out) {
            const finished = await waitForGeneration(page, { timeoutMs: Math.max(60, Number(args.timeout || 1200)) * 1000 });
            const row = { index: 0, status: 'ready', title: '', video: finished.url, poster: '', href: '', download: '' };
            if (out) {
                const saved = await downloadVideo(page, finished.url, resolve(out));
                row.download = `${saved.path} (${saved.bytes} bytes, ${saved.via})`;
            }
            return [row];
        }

        // The history grid hydrates after the shell, so wait for either a media
        // tile or an explicit empty state rather than reading an empty DOM.
        await waitForValue(page, browserSource(`
            const media = document.querySelectorAll('video, img[src*="cdn"], a[href*="/video"], [data-testid]');
            if (media.length) return true;
            const empty = Array.from(document.querySelectorAll('*')).filter(visible)
                .some((el) => /no (generations|results|history)|nothing here|get started/i.test(text(el)));
            return empty ? 'empty' : null;
        `), { label: 'Higgsfield history', timeoutMs: 45000, intervalMs: 1000 });
        await page.wait(2);

        const limit = Math.max(1, Number(args.limit || 10));

        if (args.raw === true) {
            const raw = await page.evaluate(browserSource(`
                return {
                    url: location.href,
                    videos: Array.from(document.querySelectorAll('video')).slice(0, 20).map((el) => ({
                        src: el.getAttribute('src') || '',
                        poster: el.getAttribute('poster') || '',
                        rect: rectOf(el),
                    })),
                    links: Array.from(document.querySelectorAll('a[href]')).filter(visible)
                        .map((el) => el.getAttribute('href')).filter((href) => href && !href.startsWith('#')).slice(0, 40),
                    testIds: Array.from(document.querySelectorAll('[data-testid]')).filter(visible)
                        .map((el) => el.getAttribute('data-testid')).slice(0, 40),
                    statusish: Array.from(document.querySelectorAll('*')).filter(visible)
                        .map((el) => text(el)).filter((value) => /queue|process|generat|pending|fail|complete|ready/i.test(value))
                        .filter((value) => value.length < 60).slice(0, 25),
                };
            `));
            return [{
                index: 0,
                status: 'raw',
                title: raw.url,
                video: JSON.stringify(raw.videos),
                poster: JSON.stringify({ testIds: raw.testIds, statusish: raw.statusish }),
                href: JSON.stringify(raw.links),
            }];
        }

        const items = await page.evaluate(browserSource(`
            const limit = ${limit};
            const STATUS_RE = /^(generating|queued|processing|pending|failed|completed|ready|done)\\b/i;

            // Anchor on the status label each generation card renders, then walk
            // up to the nearest ancestor that also owns the media, so a card is
            // found whether or not the video element exists yet.
            const anchors = Array.from(document.querySelectorAll('div,span,p')).filter(visible)
                .filter((el) => {
                    const value = text(el);
                    if (!STATUS_RE.test(value) || value.length > 80) return false;
                    return !Array.from(el.children).some((child) => STATUS_RE.test(text(child)));
                });

            const seen = new Set();
            const rows = [];
            for (const anchor of anchors) {
                let card = anchor;
                for (let i = 0; i < 8 && card.parentElement; i += 1) {
                    if (card.querySelector('video,img')) break;
                    card = card.parentElement;
                }
                if (seen.has(card)) continue;
                seen.add(card);

                const video = card.querySelector('video');
                const img = card.querySelector('img');
                const link = card.querySelector('a[href]');
                const blob = text(anchor).toLowerCase();
                const status = /fail|error/.test(blob) ? 'failed'
                    : /generat|queue|pending|process/.test(blob) ? 'running'
                    : /complete|ready|done/.test(blob) ? 'ready'
                    : 'unknown';
                rows.push({
                    status,
                    title: text(anchor).slice(0, 80),
                    video: video ? (video.getAttribute('src') || video.currentSrc || '') : '',
                    poster: video ? (video.getAttribute('poster') || '') : (img ? (img.getAttribute('src') || '') : ''),
                    href: link ? link.getAttribute('href') : '',
                });
                if (rows.length >= limit) break;
            }
            return rows.map((row, index) => ({ index, ...row }));
        `));

        return items.map((item) => ({
            index: item.index,
            status: item.status,
            title: item.title,
            video: item.video,
            poster: item.poster,
            href: item.href,
        }));
    },
});

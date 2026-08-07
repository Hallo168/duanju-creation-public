// tiktok-symphony download — save a generated asset to a local file.
import { copyFile, mkdir, readdir, rename, stat, unlink, writeFile } from 'node:fs/promises';
import { basename, dirname, isAbsolute, join, resolve } from 'node:path';
import { cli, Strategy } from '@jackwener/opencli/registry';
import { ArgumentError, CommandExecutionError, EmptyResultError } from '@jackwener/opencli/errors';
import { DEEP_QUERY_SRC, HOST, LIBRARY_SCROLL_SRC, LIBRARY_URL, waitForValue } from './utils.js';

const EXT_BY_TYPE = {
    'image/jpeg': 'jpg',
    'image/png': 'png',
    'image/webp': 'webp',
    'image/avif': 'avif',
    'image/gif': 'gif',
    'video/mp4': 'mp4',
    'video/quicktime': 'mov',
};

/**
 * Page-context lookup: the signed CDN URL for one assetId, if it is rendered.
 *
 * Images and clips live on different hosts with different identity schemes, so
 * both are searched: an image assetId is a path segment, a clip assetId is the
 * `vid` query parameter (see `generations`).
 */
function findUrlSrc(assetId) {
    return `(() => {
        ${DEEP_QUERY_SRC}
        const want = ${JSON.stringify(assetId)};
        const img = __deepAll(document, (el) => el.tagName === 'IMG'
            && (el.src || '').includes('/ad-creative-sg/' + want + '~'))[0];
        if (img) return img.src;
        const video = __deepAll(document, (el) => el.tagName === 'VIDEO'
            && (el.src || el.currentSrc || '').includes(want))[0];
        return video ? (video.src || video.currentSrc) : null;
    })()`;
}

/**
 * Save a clip through the Library tile's download control.
 *
 * Returns null when the tile or its control is not there — images have no such
 * button, and the caller still has the URL-fetch path for those.
 */
async function downloadViaTile(page, assetId, outDir, timeoutMs) {
    // The grid mounts tiles lazily, so a single look can miss one that is only
    // a scroll away. Without this the click quietly returned null and the
    // caller fell back to the playback stream — a silent drop to 360x640 that
    // the resulting file gives no hint of.
    // Let the grid render before hunting: on a fresh navigation the feed is
    // empty, so the scroll probe reports "already at the end" and the loop
    // below used to bail out before the first tile ever mounted.
    await waitForValue(page, `(() => {
        ${DEEP_QUERY_SRC}
        return __deepAll(document, (el) => el.tagName === 'VIDEO'
            && el.getBoundingClientRect().width > 0).length || null;
    })()`, { label: 'Library grid', timeoutMs: 45000 }).catch(() => null);

    let clicked = null;
    for (let attempt = 0; !clicked && attempt < 10; attempt++) {
        // The tile's controls only mount while it is hovered, and a synthetic
        // MouseEvent does not arm them (same lesson as the composer's other
        // hover-gated UI) — only a genuine CDP pointer move does. Earlier runs
        // that "worked" were ones where the real mouse happened to be resting
        // over the grid.
        const spot = await page.evaluate(hoverTileSrc(assetId));
        if (!spot) {
            const scrolled = await page.evaluate(LIBRARY_SCROLL_SRC);
            await new Promise((r) => setTimeout(r, 900));
            if (scrolled?.atEnd) break;
            continue;
        }
        // The site's download handler reads the currently activated preview,
        // not just the icon's enclosing tile. Without activating the target
        // first it calls watermark_video_info with `vid=` and the export sits
        // at 0% forever. Click the target card centre before arming its
        // hover-only controls.
        await page.nativeClick(spot.x, spot.y);
        await page.wait(0.7);
        await page.cdp('Input.dispatchMouseEvent', { type: 'mouseMoved', x: Math.round(spot.x), y: Math.round(spot.y) });
        await page.wait(0.7);
        if (process.env.OC_SYMPHONY_SHOT) {
            await page.screenshot({ path: `${process.env.OC_SYMPHONY_SHOT}-hover${attempt}.png` });
        }
        // A plain JS click works once the control is mounted — what must NOT
        // happen between hover and click is scrolling. clickWithQuads scrolls
        // the icon to centre first, which moves the page under the pointer,
        // drops the hover, unmounts the control, and clicks nothing.
        const startedAt = Date.now();
        const capturing = process.env.OC_SYMPHONY_NET
            ? await page.startNetworkCapture('').catch(() => false)
            : false;
        clicked = await page.evaluate(clickTileDownloadSrc(assetId)) || null;
        if (clicked && process.env.OC_SYMPHONY_SHOT) {
            await page.wait(1.5);
            await page.screenshot({ path: `${process.env.OC_SYMPHONY_SHOT}-clicked.png` });
        }
        if (clicked) {
            // The daemon serializes commands for one page lease, so waiting
            // must start after the click. resolveFreshDownload closes the
            // resulting race by rejecting old files and watching the download
            // directory when the extension hands back stale history.
            const observed = await page.waitForDownload('', timeoutMs);
            if (capturing) {
                const network = await page.readNetworkCapture().catch(() => []);
                const relevant = network
                    .filter((item) => /download|export|media|asset|generation|video/i.test(item?.url || ''))
                    .map((item) => ({
                        method: item.method || null,
                        url: item.url || null,
                        status: item.status || item.statusCode || null,
                        type: item.type || item.resourceType || null,
                    }))
                    .slice(-40);
                process.stderr.write(`\nOC_SYMPHONY_NET ${JSON.stringify(relevant, null, 2)}\n`);
            }
            const got = await resolveFreshDownload(observed, startedAt, timeoutMs);
            return await saveBrowserDownload(got, assetId, outDir);
        }
    }
    if (!clicked) return null;
    return null;
}

async function findOfficialDownloadUrl(page, assetId) {
    const result = await page.evaluate(`(async () => {
        const q = new URLSearchParams({
            vid: ${JSON.stringify(assetId)},
            aid: '585599',
            app_name: 'creative_aio_client',
            device_platform: 'web',
        });
        const r = await fetch('/creative_bff_i18n/api/cue/watermark_video_info?' + q, {
            credentials: 'include',
        });
        let json = null;
        try { json = await r.json(); } catch {}
        return { status: r.status, json };
    })()`).catch(() => null);
    if (!result || result.status !== 200 || result.json?.code !== 0) return null;
    const url = result.json?.data?.downloadable_url || result.json?.data?.origin_url || null;
    return url ? validateOfficialDownloadUrl(assetId, url) : null;
}

export function validateOfficialDownloadUrl(assetId, url) {
    let parsed;
    try {
        parsed = new URL(url);
    } catch {
        throw new CommandExecutionError(`Symphony returned an invalid original URL for ${assetId}`);
    }
    const returnedId = parsed.searchParams.get('vid');
    if (parsed.protocol !== 'https:' || returnedId !== assetId) {
        throw new CommandExecutionError(
            `Symphony original URL identity mismatch: requested ${assetId}, returned ${returnedId || 'no vid'}`,
        );
    }
    return parsed.toString();
}

export async function resolveFreshDownload(observed, startedAt, timeoutMs) {
    if (observed?.filename) {
        const info = await stat(observed.filename).catch(() => null);
        if (info?.isFile() && info.size > 0 && info.mtimeMs >= startedAt - 2000) {
            return { ...observed, downloaded: true, totalBytes: info.size, state: 'complete' };
        }
        // A stale browser-history result still tells us Chrome's download
        // directory. Watch that directory for the file produced by this click
        // instead of accepting the stale item under the requested assetId.
        return waitForFreshFileInDir(dirname(observed.filename), startedAt, timeoutMs);
    }
    throw new CommandExecutionError(
        `the Library download did not start: ${JSON.stringify(observed)}`,
    );
}

export async function waitForFreshFileInDir(dir, startedAt, timeoutMs) {
    const deadline = startedAt + timeoutMs;
    while (Date.now() < deadline) {
        const names = await readdir(dir).catch(() => []);
        const candidates = [];
        for (const name of names.filter((item) => !/\.(?:crdownload|tmp)$/i.test(item))) {
            const filename = join(dir, name);
            const info = await stat(filename).catch(() => null);
            if (info?.isFile() && info.size > 0 && info.mtimeMs >= startedAt - 2000) {
                candidates.push({ filename, info });
            }
        }
        if (candidates.length > 1) {
            throw new CommandExecutionError(
                `multiple new browser downloads appeared; refusing to guess: ${candidates.map((x) => basename(x.filename)).join(', ')}`,
            );
        }
        if (candidates.length === 1) {
            const { filename, info } = candidates[0];
            return {
                downloaded: true,
                filename,
                mime: /\.mp4$/i.test(filename) ? 'video/mp4' : null,
                totalBytes: info.size,
                state: 'complete',
            };
        }
        await new Promise((resolve) => setTimeout(resolve, 500));
    }
    throw new CommandExecutionError(`the Library download did not complete within ${timeoutMs}ms`);
}

async function saveBrowserDownload(got, assetId, outDir) {
    if (!got?.downloaded || got.state !== 'complete' || !got.filename) {
        throw new CommandExecutionError(
            `the Library download for ${assetId} did not complete: ${JSON.stringify(got)}`,
        );
    }

    // Chrome writes to the browser's own download directory, so the file still
    // has to be moved to --out under a name that identifies the asset.
    const dir = isAbsolute(outDir) ? outDir : resolve(process.cwd(), outDir);
    await mkdir(dir, { recursive: true });
    const ext = EXT_BY_TYPE[got.mime] || 'mp4';
    const dest = join(dir, `${assetId}.${ext}`);
    await rename(got.filename, dest).catch(async (error) => {
        // rename fails across volumes, and the browser's download dir routinely
        // sits on a different drive than the project.
        if (error?.code !== 'EXDEV') throw error;
        await copyFile(got.filename, dest);
        await unlink(got.filename).catch(() => {});
    });

    return { assetId, file: dest, bytes: got.totalBytes ?? null, contentType: got.mime || null };
}

/** Page-context source: centre the Library tile for `assetId` and report where it is. */
function hoverTileSrc(assetId) {
    return `(() => {
        ${DEEP_QUERY_SRC}
        const want = ${JSON.stringify(assetId)};
        const video = __deepAll(document, (el) => el.tagName === 'VIDEO'
            && (el.src || el.currentSrc || '').includes(want))[0];
        if (!video) return null;
        let tile = video;
        // Climb until the next parent would hold a second video: a fixed-depth
        // walk overshot into the whole grid, whose first download icon belongs
        // to a different card — and that card is what got downloaded.
        while (tile.parentElement
            && __deepAll(tile.parentElement, (el) => el.tagName === 'VIDEO').length <= 1) {
            tile = tile.parentElement;
        }
        tile.scrollIntoView({ block: 'center' });
        const r = tile.getBoundingClientRect();
        return r.width > 0 ? { x: r.x + r.width / 2, y: r.y + r.height / 2 } : null;
    })()`;
}

/** Page-context source: click the tile's download control (mounted by hover). */
function clickTileDownloadSrc(assetId) {
    return `(() => {
        ${DEEP_QUERY_SRC}
        const want = ${JSON.stringify(assetId)};
        const video = __deepAll(document, (el) => el.tagName === 'VIDEO'
            && (el.src || el.currentSrc || '').includes(want))[0];
        if (!video) return null;
        let tile = video;
        // Climb until the next parent would hold a second video: a fixed-depth
        // walk overshot into the whole grid, whose first download icon belongs
        // to a different card — and that card is what got downloaded.
        while (tile.parentElement
            && __deepAll(tile.parentElement, (el) => el.tagName === 'VIDEO').length <= 1) {
            tile = tile.parentElement;
        }
        const icon = __deepAll(tile, (el) => __ksTag(el, 'ks-icon')
            && el.getAttribute('name') === 'download'
            && el.getBoundingClientRect().width > 0)[0];
        if (!icon) return null;
        // The icon is decorative; the click target is the button wrapping it.
        const btn = icon.closest('button') || icon.parentElement;
        if (!btn) return null;
        btn.click();
        return true;
    })()`;
}

cli({
    site: 'tiktok-symphony',
    name: 'download',
    description: 'Download a generated Symphony asset by assetId (see: generations)',
    access: 'read',
    example: 'opencli tiktok-symphony download 202607195d0d7ea36ed139b74eccb1e4 --out ./downloads',
    domain: HOST,
    strategy: Strategy.UI,
    browser: true,
    navigateBefore: LIBRARY_URL,
    // The Library/feed tiles mount lazily via IntersectionObserver. A
    // background tab is never rendered, so nothing ever intersects and the
    // grid stays empty — this command is only correct in the foreground.
    defaultWindowMode: 'foreground',
    args: [
        { name: 'asset', type: 'string', required: true, positional: true, help: 'assetId from `generations`, or a full asset URL' },
        { name: 'out', type: 'string', default: '.', help: 'Directory to write the file into' },
        {
            name: 'allow-preview',
            type: 'boolean',
            default: false,
            help: 'Accept the reduced-resolution playback stream when the full render cannot be reached',
        },
        {
            name: 'timeout',
            type: 'int',
            default: 240,
            help: 'Seconds to wait for the Library original download (default 240)',
        },
    ],
    columns: ['assetId', 'file', 'bytes', 'contentType'],
    func: async (page, args) => {
        const asset = String(args.asset ?? '').trim();
        if (!asset) throw new ArgumentError('asset is required: pass an assetId or a full asset URL');

        const outDir = String(args.out ?? '.');
        const timeoutMs = Math.max(1, Number(args.timeout) || 240) * 1000;

        let assetId;
        let url;
        let isOriginal = false;
        if (/^https?:\/\//i.test(asset)) {
            url = asset;
            const image = /\/ad-creative-sg\/([A-Za-z0-9]+)~/.exec(asset);
            const clip = /[?&]vid=([A-Za-z0-9]+)/.exec(asset);
            assetId = image?.[1] || clip?.[1] || basename(new URL(asset).pathname);
        } else {
            if (!/^[A-Za-z0-9]+$/.test(asset)) {
                throw new ArgumentError(`"${asset}" is not a valid assetId (expected alphanumeric) or URL`);
            }
            assetId = asset;

            // The Library button currently calls watermark_video_info with an
            // empty `vid`, leaving its export at 0%. Call the same authenticated
            // site API with the requested ID explicitly; the returned signed
            // URL carries that ID again, so identity can be verified before
            // any bytes are written.
            url = await findOfficialDownloadUrl(page, assetId);
            isOriginal = Boolean(url);
            if (!url) {
                // Retain the UI path for older site versions where the official
                // endpoint is absent but the tile button still works.
                const viaTile = await downloadViaTile(page, assetId, outDir, timeoutMs);
                if (viaTile) return [viaTile];
            }

            // Playback URLs are signed and short-lived, so the final fallback
            // still has to read them from the live Library.
            url ||= await page.evaluate(findUrlSrc(assetId));
            for (let attempt = 0; !url && attempt < 30; attempt++) {
                const scrolled = await page.evaluate(LIBRARY_SCROLL_SRC);
                await new Promise((r) => setTimeout(r, 1200));
                url = await page.evaluate(findUrlSrc(assetId));
                if (!url && scrolled?.atEnd) break;
            }
            if (!url) {
                // Give the grid one slow chance in case it was still hydrating.
                try {
                    url = await waitForValue(page, findUrlSrc(assetId), {
                        label: `asset ${assetId} in Library`,
                        timeoutMs: 8000,
                    });
                } catch {
                    throw new EmptyResultError(
                        'tiktok-symphony download',
                        `assetId ${assetId} was not found in the Library — check \`opencli tiktok-symphony generations\``,
                    );
                }
            }
        }

        // Gate on the URL itself, right before the fetch. An earlier guard
        // asked the DOM "is this a clip?" before the lazy grid had mounted the
        // tile, answered no, and let a 360x640 playback transcode through as if
        // it were the render. The URL host cannot be early like the DOM can:
        // clip streams live on the video CDN, images on the ad-creative hosts.
        if (!isOriginal && /\/video\/tos\//.test(url) && args['allow-preview'] !== true) {
            throw new CommandExecutionError(
                `only the playback stream is reachable for ${assetId}, and it is a reduced-resolution preview `
                + '(360x640 where renders are 720x1280+). Re-run with --allow-preview true to accept it, '
                + 'or save the original from the Library by hand.',
            );
        }

        let resp;
        try {
            resp = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0', Referer: `https://${HOST}/` } });
        } catch (error) {
            throw new CommandExecutionError(`asset download failed: ${error?.message || error}`);
        }
        if (!resp.ok) throw new CommandExecutionError(`asset download failed: HTTP ${resp.status}`);

        const contentType = (resp.headers.get('content-type') || '').split(';')[0].trim() || null;
        const bytes = Buffer.from(await resp.arrayBuffer());
        if (bytes.length === 0) throw new CommandExecutionError(`asset ${assetId} returned an empty body`);

        const ext = EXT_BY_TYPE[contentType] || 'bin';
        const dir = isAbsolute(outDir) ? outDir : resolve(process.cwd(), outDir);
        try {
            await mkdir(dir, { recursive: true });
        } catch (error) {
            throw new CommandExecutionError(`could not create output directory "${dir}": ${error?.message || error}`);
        }

        const file = join(dir, `${assetId}.${ext}`);
        try {
            await writeFile(file, bytes);
        } catch (error) {
            throw new CommandExecutionError(`could not write "${file}": ${error?.message || error}`);
        }

        return [{ assetId, file, bytes: bytes.length, contentType }];
    },
});

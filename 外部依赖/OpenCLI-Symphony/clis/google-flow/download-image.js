import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, extname, resolve } from 'node:path';
import { cli, Strategy } from '@jackwener/opencli/registry';
import { ArgumentError, CommandExecutionError } from '@jackwener/opencli/errors';
import { HOST, projectUrl, waitForValue } from './utils.js';

cli({
    site: 'google-flow',
    name: 'download-image',
    description: 'Download the original generated image from a Google Flow asset page',
    access: 'write',
    domain: HOST,
    strategy: Strategy.UI,
    browser: true,
    navigateBefore: false,
    defaultWindowMode: 'background',
    siteSession: 'persistent',
    args: [
        { name: 'asset', type: 'string', required: true, positional: true, help: 'Flow asset UUID' },
        { name: 'project', type: 'string', required: true, help: 'Flow project UUID' },
        { name: 'out', type: 'string', required: true, help: 'Output PNG, JPEG, or WebP path' },
        { name: 'timeout', type: 'int', default: 120, help: 'Download timeout in seconds' },
    ],
    columns: ['assetId', 'projectId', 'file', 'bytes', 'mimeType', 'width', 'height', 'source'],
    func: async (page, args) => {
        const assetId = String(args.asset ?? '').trim();
        if (!/^[0-9a-f-]{36}$/i.test(assetId)) throw new ArgumentError('asset must be a UUID');
        const out = resolve(String(args.out ?? '').trim());
        if (!['.png', '.jpg', '.jpeg', '.webp'].includes(extname(out).toLowerCase())) {
            throw new ArgumentError('out must end in .png, .jpg, .jpeg, or .webp');
        }

        await page.goto(`${projectUrl(args.project)}/edit/${assetId}`, { waitUntil: 'load' });
        const image = await waitForValue(page, `(() => {
            const item = Array.from(document.querySelectorAll('img')).find((img) =>
                img.alt === '生成的图片'
                && img.naturalWidth > 0
                && img.naturalHeight > 0);
            return item ? {
                src: item.currentSrc || item.src || '',
                width: item.naturalWidth,
                height: item.naturalHeight,
            } : null;
        })()`, {
            label: 'generated Flow image',
            timeoutMs: Math.max(30, Number(args.timeout || 120)) * 1000,
            intervalMs: 1000,
        });

        await page.goto(image.src, { waitUntil: 'load' });
        const signedUrl = await page.evaluate(() => location.href);
        let response;
        try {
            response = await fetch(signedUrl, {
                redirect: 'follow',
                headers: {
                    Referer: `https://${HOST}/`,
                    'User-Agent': 'Mozilla/5.0',
                },
            });
        } catch (error) {
            throw new CommandExecutionError(`Flow image download failed: ${error?.message || error}`);
        }
        if (!response.ok) {
            throw new CommandExecutionError(`Flow image download failed with HTTP ${response.status}`);
        }
        const bytes = Buffer.from(await response.arrayBuffer());
        if (!bytes.length) throw new CommandExecutionError('Flow returned an empty image');
        const mimeType = (response.headers.get('content-type') || '').split(';')[0] || null;
        await mkdir(dirname(out), { recursive: true });
        await writeFile(out, bytes);
        return [{
            assetId,
            projectId: String(args.project),
            file: out,
            bytes: bytes.length,
            mimeType,
            width: image.width,
            height: image.height,
            source: image.src,
        }];
    },
});

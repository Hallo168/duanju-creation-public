import { stat } from 'node:fs/promises';
import { basename, extname, resolve } from 'node:path';
import { cli, Strategy } from '@jackwener/opencli/registry';
import { ArgumentError, CommandExecutionError } from '@jackwener/opencli/errors';
import {
    HOST,
    EDITOR_SELECTOR,
    TEST_PROMPT,
    chooseUploadedReference,
    clickSubmit,
    fillPrompt,
    openReferencePicker,
    projectUrl,
    submitEnabled,
    summarizeCapture,
    uploadLocalReference,
    waitForValue,
} from './utils.js';

cli({
    site: 'google-flow',
    name: 'generate-image',
    aliases: ['derive-image'],
    description: 'Derive a new Google Flow image from one uploaded reference image and a prompt',
    access: 'write',
    domain: HOST,
    strategy: Strategy.UI,
    browser: true,
    navigateBefore: false,
    defaultWindowMode: 'background',
    siteSession: 'persistent',
    args: [
        { name: 'prompt', type: 'string', required: true, positional: true, help: 'Image derivation prompt' },
        { name: 'project', type: 'string', required: true, help: 'Google Flow project UUID' },
        { name: 'ref', type: 'string', default: '', help: 'Local reference image path to upload and attach' },
        { name: 'reference', type: 'string', default: '', help: 'Exact filename of an image already uploaded to this Flow project' },
        { name: 'wait', type: 'boolean', default: true, help: 'Wait for a new generated image asset' },
        { name: 'timeout', type: 'int', default: 600, help: 'Generation wait timeout in seconds' },
        { name: 'prepare-only', type: 'boolean', default: false, help: 'Attach image and prompt but do not submit or spend credits' },
    ],
    columns: ['status', 'projectId', 'reference', 'prompt', 'assetId', 'url', 'protocol'],
    func: async (page, args) => {
        const prompt = String(args.prompt ?? '').trim();
        if (!prompt) throw new ArgumentError('prompt is required and cannot be blank');
        const rawRef = String(args.ref ?? '').trim();
        const existingReference = String(args.reference ?? '').trim();
        if (!rawRef && !existingReference) {
            throw new ArgumentError('pass either --ref with a local image path or --reference with an uploaded Flow asset name');
        }
        if (rawRef && existingReference) {
            throw new ArgumentError('--ref and --reference are mutually exclusive');
        }
        let refPath = null;
        let reference;
        if (rawRef) {
            refPath = resolve(rawRef);
            const info = await stat(refPath).catch(() => null);
            if (!info?.isFile()) throw new ArgumentError(`reference image does not exist: ${refPath}`);
            const extension = extname(refPath).toLowerCase();
            if (!['.png', '.jpg', '.jpeg', '.webp'].includes(extension)) {
                throw new ArgumentError('reference image must be PNG, JPEG, or WebP');
            }
            reference = basename(refPath);
        } else {
            reference = basename(existingReference);
        }
        const projectId = String(args.project ?? '').trim();
        const timeoutSec = Math.max(30, Number(args.timeout || 600));

        const url = new URL(projectUrl(projectId));
        url.searchParams.set('_ocFresh', `${Date.now()}`);
        await page.goto(url.toString(), { waitUntil: 'load' });
        await waitForValue(page, `(() => {
            const editor = document.querySelector('${EDITOR_SELECTOR}');
            return editor && editor.getBoundingClientRect().width > 0 ? true : null;
        })()`, { label: 'Google Flow composer', timeoutMs: 45000 });

        const before = await page.evaluate(() =>
            Array.from(document.querySelectorAll('a[href*="/edit/"]'))
                .map((link) => link.getAttribute('href') || '')
                .filter(Boolean));

        const capturing = await page.startNetworkCapture?.('').catch(() => false);
        await openReferencePicker(page);
        if (refPath) {
            await uploadLocalReference(page, refPath, reference);
        } else {
            await chooseUploadedReference(page, reference);
        }
        await fillPrompt(page, prompt);
        if (!await submitEnabled(page)) {
            throw new CommandExecutionError('Google Flow did not enable generation after attaching the reference and prompt');
        }

        const base = {
            projectId,
            reference,
            prompt,
            assetId: null,
            url: null,
            protocol: null,
        };
        if (args['prepare-only'] === true) {
            const captured = capturing ? await page.readNetworkCapture().catch(() => []) : [];
            return [{ ...base, status: 'prepared', protocol: JSON.stringify(summarizeCapture(captured)) }];
        }

        await clickSubmit(page);

        const acknowledged = await waitForValue(page, `(() => {
            const editor = document.querySelector('${EDITOR_SELECTOR}');
            const text = String(editor?.innerText || editor?.textContent || '').trim();
            return text !== ${JSON.stringify(prompt)} ? true : null;
        })()`, { label: 'Google Flow generation acknowledgement', timeoutMs: 60000 });
        if (!acknowledged) throw new CommandExecutionError('Google Flow did not acknowledge the generation');

        let assetUrl = null;
        if (args.wait !== false) {
            const beforeSet = JSON.stringify(before);
            assetUrl = await waitForValue(page, `(() => {
                const before = new Set(${beforeSet});
                const links = Array.from(document.querySelectorAll('a[href*="/edit/"]'))
                    .map((link) => link.href || link.getAttribute('href') || '')
                    .filter(Boolean);
                return links.find((href) => !before.has(new URL(href, location.origin).pathname)) || null;
            })()`, {
                label: 'derived Google Flow image',
                timeoutMs: timeoutSec * 1000,
                intervalMs: 3000,
            }).catch(() => null);
        }

        await page.wait(1);
        const captured = capturing ? await page.readNetworkCapture().catch(() => []) : [];
        const protocol = summarizeCapture(captured);
        const assetId = assetUrl ? assetUrl.split('/edit/')[1]?.split(/[?#/]/)[0] || null : null;
        return [{
            ...base,
            status: assetUrl ? 'ready' : 'submitted',
            assetId,
            url: assetUrl,
            protocol: JSON.stringify(protocol),
        }];
    },
});

export { TEST_PROMPT };

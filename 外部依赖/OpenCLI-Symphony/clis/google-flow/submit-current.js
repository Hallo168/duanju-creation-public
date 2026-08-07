import { cli, Strategy } from '@jackwener/opencli/registry';
import { ArgumentError, CommandExecutionError } from '@jackwener/opencli/errors';
import {
    EDITOR_SELECTOR,
    clickSubmit,
    fillPrompt,
    submitEnabled,
    summarizeCapture,
    waitForValue,
} from './utils.js';

cli({
    site: 'google-flow',
    name: 'submit-current',
    description: 'Commit the reference visible in the current Flow media picker and submit an image derivation',
    access: 'write',
    domain: 'labs.google',
    strategy: Strategy.UI,
    browser: true,
    navigateBefore: false,
    defaultWindowMode: 'background',
    siteSession: 'persistent',
    args: [
        { name: 'prompt', type: 'string', required: true, positional: true, help: 'Image derivation prompt' },
        { name: 'reference', type: 'string', default: '', help: 'Expected visible reference filename' },
        { name: 'wait', type: 'boolean', default: true, help: 'Wait for a new generated asset' },
        { name: 'timeout', type: 'int', default: 600, help: 'Generation wait timeout in seconds' },
    ],
    columns: ['status', 'prompt', 'reference', 'assetId', 'url', 'protocol'],
    func: async (page, args) => {
        const prompt = String(args.prompt ?? '').trim();
        const expectedReference = String(args.reference ?? '').trim();
        if (!prompt) throw new ArgumentError('prompt must not be empty');

        const current = await page.evaluate((expected) => {
            const visible = (el) => {
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };
            const images = Array.from(document.querySelectorAll('img'))
                .filter(visible)
                .map((img) => ({ alt: img.getAttribute('alt') || '', src: img.getAttribute('src') || '' }));
            const reference = expected
                ? images.find((img) => img.alt === expected)
                : images.find((img) => img.alt && img.src.includes('media.getMediaUrlRedirect'));
            const addButton = Array.from(document.querySelectorAll('button'))
                .filter(visible)
                .find((button) => (button.innerText || button.textContent || '').trim() === '添加到提示');
            return { reference: reference || null, canAdd: !!addButton && !addButton.disabled };
        }, expectedReference);
        if (expectedReference && !current.reference) {
            throw new CommandExecutionError(`expected visible reference not found: ${expectedReference}`);
        }
        if (!current.canAdd) {
            throw new CommandExecutionError('the visible “添加到提示” button is unavailable');
        }

        const before = await page.evaluate(() =>
            Array.from(document.querySelectorAll('a[href*="/edit/"]'))
                .map((link) => new URL(link.href, location.href).pathname)
                .filter(Boolean));
        const capturing = await page.startNetworkCapture?.('').catch(() => false);

        const added = await page.evaluate(() => {
            const visible = (el) => {
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };
            const button = Array.from(document.querySelectorAll('button'))
                .filter(visible)
                .find((candidate) => (candidate.innerText || candidate.textContent || '').trim() === '添加到提示');
            if (!button || button.disabled) return false;
            button.click();
            return true;
        });
        if (!added) throw new CommandExecutionError('could not add the uploaded image to the prompt');

        await waitForValue(page, `(() => {
            const button = Array.from(document.querySelectorAll('button')).find((candidate) =>
                (candidate.innerText || candidate.textContent || '').trim() === '添加到提示');
            if (!button) return true;
            const rect = button.getBoundingClientRect();
            return rect.width === 0 || rect.height === 0 ? true : null;
        })()`, { label: 'Flow media picker to close', timeoutMs: 30000 });

        await fillPrompt(page, prompt);
        if (!await submitEnabled(page)) {
            throw new CommandExecutionError('Flow create button is disabled after attaching the reference and prompt');
        }

        await clickSubmit(page);
        await waitForValue(page, `(() => {
            const editor = document.querySelector('${EDITOR_SELECTOR}');
            const value = String(editor?.innerText || editor?.textContent || '').trim();
            return value !== ${JSON.stringify(prompt)} ? true : null;
        })()`, { label: 'Flow generation acknowledgement', timeoutMs: 60000 });

        let assetUrl = null;
        if (args.wait !== false) {
            assetUrl = await waitForValue(page, `(() => {
                const known = new Set(${JSON.stringify(before)});
                const links = Array.from(document.querySelectorAll('a[href*="/edit/"]'))
                    .map((link) => link.href || '')
                    .filter(Boolean);
                return links.find((href) => !known.has(new URL(href, location.href).pathname)) || null;
            })()`, {
                label: 'a newly generated Flow asset',
                timeoutMs: Math.max(30, Number(args.timeout || 600)) * 1000,
                intervalMs: 3000,
            }).catch(() => null);
        }

        const captured = capturing ? await page.readNetworkCapture().catch(() => []) : [];
        const assetId = assetUrl ? assetUrl.split('/edit/')[1]?.split(/[?#/]/)[0] || null : null;
        return [{
            status: assetUrl ? 'ready' : 'submitted',
            prompt,
            reference: current.reference?.alt || expectedReference || null,
            assetId,
            url: assetUrl,
            protocol: JSON.stringify(summarizeCapture(captured)),
        }];
    },
});

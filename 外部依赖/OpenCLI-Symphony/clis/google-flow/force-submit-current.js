import { cli, Strategy } from '@jackwener/opencli/registry';
import { CommandExecutionError } from '@jackwener/opencli/errors';
import { EDITOR_SELECTOR, summarizeCapture, waitForValue } from './utils.js';

cli({
    site: 'google-flow',
    name: 'force-submit-current',
    description: 'Submit the currently prepared Google Flow image derivation with a trusted browser click',
    access: 'write',
    domain: 'labs.google',
    strategy: Strategy.UI,
    browser: true,
    navigateBefore: false,
    defaultWindowMode: 'background',
    siteSession: 'persistent',
    args: [
        { name: 'wait', type: 'boolean', default: true, help: 'Wait for a new generated asset' },
        { name: 'timeout', type: 'int', default: 600, help: 'Generation wait timeout in seconds' },
    ],
    columns: ['status', 'assetId', 'url', 'protocol'],
    func: async (page, args) => {
        const prompt = await page.evaluate(`(() => {
            const editor = document.querySelector('${EDITOR_SELECTOR}');
            return String(editor?.innerText || editor?.textContent || '').trim();
        })()`);
        if (!prompt) throw new CommandExecutionError('the current Flow prompt is empty');

        const before = await page.evaluate(() =>
            Array.from(document.querySelectorAll('a[href*="/edit/"]'))
                .map((link) => new URL(link.href, location.href).pathname)
                .filter(Boolean));
        const marker = `opencli-flow-submit-${Date.now()}`;
        const marked = await page.evaluate((value) => {
            const visible = (el) => {
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };
            const button = Array.from(document.querySelectorAll('button')).find((el) =>
                visible(el) && /(?:^|\s)arrow_forward(?:\s|$)/.test(el.innerText || el.textContent || ''));
            if (!button || button.disabled) return false;
            button.setAttribute('data-opencli-flow-submit', value);
            return true;
        }, marker);
        if (!marked) throw new CommandExecutionError('Flow create button is unavailable');

        const capturing = await page.startNetworkCapture?.('').catch(() => false);
        try {
            await page.click(`[data-opencli-flow-submit="${marker}"]`);
        } finally {
            await page.evaluate((value) => {
                document.querySelector(`[data-opencli-flow-submit="${value}"]`)
                    ?.removeAttribute('data-opencli-flow-submit');
            }, marker).catch(() => {});
        }

        const acknowledged = await waitForValue(page, `(() => {
            const editor = document.querySelector('${EDITOR_SELECTOR}');
            const current = String(editor?.innerText || editor?.textContent || '').trim();
            const busy = Array.from(document.querySelectorAll('[aria-busy="true"], [role="progressbar"]'))
                .some((el) => el.getBoundingClientRect().width > 0);
            return current !== ${JSON.stringify(prompt)} || busy ? true : null;
        })()`, { label: 'Flow generation acknowledgement', timeoutMs: 90000 }).catch(() => false);

        let assetUrl = null;
        if (acknowledged && args.wait !== false) {
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
            status: assetUrl ? 'ready' : acknowledged ? 'submitted' : 'not-acknowledged',
            assetId,
            url: assetUrl,
            protocol: JSON.stringify(summarizeCapture(captured)),
        }];
    },
});

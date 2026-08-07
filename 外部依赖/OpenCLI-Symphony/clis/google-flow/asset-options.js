import { cli, Strategy } from '@jackwener/opencli/registry';
import { ArgumentError } from '@jackwener/opencli/errors';
import { HOST, projectUrl } from './utils.js';

cli({
    site: 'google-flow',
    name: 'asset-options',
    description: 'Inspect a generated Flow image asset page',
    access: 'read',
    domain: HOST,
    strategy: Strategy.UI,
    browser: true,
    navigateBefore: false,
    defaultWindowMode: 'background',
    siteSession: 'persistent',
    args: [
        { name: 'asset', type: 'string', required: true, positional: true, help: 'Flow asset UUID' },
        { name: 'project', type: 'string', required: true, help: 'Flow project UUID' },
    ],
    columns: ['url', 'images', 'buttons'],
    func: async (page, args) => {
        const asset = String(args.asset ?? '').trim();
        if (!/^[0-9a-f-]{36}$/i.test(asset)) throw new ArgumentError('asset must be a UUID');
        await page.goto(`${projectUrl(args.project)}/edit/${asset}`, { waitUntil: 'load' });
        await page.wait(5);
        const state = await page.evaluate(() => {
            const visible = (el) => {
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };
            return {
                url: location.href,
                images: Array.from(document.querySelectorAll('img'))
                    .filter(visible)
                    .map((img) => ({
                        alt: img.getAttribute('alt') || '',
                        src: img.currentSrc || img.src || '',
                        width: img.naturalWidth,
                        height: img.naturalHeight,
                    })),
                buttons: Array.from(document.querySelectorAll('button, [role="button"]'))
                    .filter(visible)
                    .map((button) => String(button.innerText || button.textContent || '').trim())
                    .filter(Boolean),
            };
        });
        return [{
            url: state.url,
            images: JSON.stringify(state.images),
            buttons: JSON.stringify(state.buttons),
        }];
    },
});

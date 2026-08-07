import { cli, Strategy } from '@jackwener/opencli/registry';
import { ArgumentError } from '@jackwener/opencli/errors';

const HOST = 'labs.google';

cli({
    site: 'google-flow',
    name: 'composer-options',
    description: 'Inspect the active Google Flow project composer without generating media',
    access: 'read',
    domain: HOST,
    strategy: Strategy.UI,
    browser: true,
    navigateBefore: false,
    defaultWindowMode: 'background',
    siteSession: 'persistent',
    args: [
        { name: 'project', type: 'string', required: true, help: 'Google Flow project UUID' },
    ],
    columns: ['projectId', 'url', 'controls'],
    func: async (page, args) => {
        const projectId = String(args.project ?? '').trim();
        if (!/^[0-9a-f-]{36}$/i.test(projectId)) {
            throw new ArgumentError('project must be a Google Flow project UUID');
        }
        const url = `https://${HOST}/fx/zh/tools/flow/project/${projectId}`;
        await page.goto(url, { waitUntil: 'load' });
        await page.wait(4);

        const inspectControls = () => page.evaluate(() => {
            const visible = (el) => {
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };
            return Array.from(document.querySelectorAll(
                'button,input,textarea,[contenteditable="true"],[role="button"],[role="textbox"]',
            ))
                .filter(visible)
                .slice(-120)
                .map((el, index) => {
                    const rect = el.getBoundingClientRect();
                    return {
                        index,
                        tag: el.tagName,
                        role: el.getAttribute('role'),
                        type: el.getAttribute('type'),
                        ariaLabel: el.getAttribute('aria-label'),
                        placeholder: el.getAttribute('placeholder'),
                        testId: el.getAttribute('data-testid'),
                        contentEditable: el.getAttribute('contenteditable'),
                        disabled: el.disabled || el.hasAttribute('disabled'),
                        text: String(el.innerText || el.textContent || '').trim().slice(0, 160),
                        rect: [
                            Math.round(rect.x),
                            Math.round(rect.y),
                            Math.round(rect.width),
                            Math.round(rect.height),
                        ],
                        className: String(el.className || '').slice(0, 180),
                    };
                });
        });
        const controls = await inspectControls();
        const opened = await page.evaluate(() => {
            const visible = (el) => {
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };
            const button = Array.from(document.querySelectorAll('button'))
                .find((el) => visible(el) && /(?:^|\s)add_2(?:\s|$)/.test(el.innerText || el.textContent || ''));
            if (!button) return false;
            button.click();
            return true;
        });
        if (opened) await page.wait(0.5);
        const afterCreate = opened ? await inspectControls() : [];
        const uploadOpened = opened ? await page.evaluate(() => {
            const visible = (el) => {
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };
            const button = Array.from(document.querySelectorAll('button'))
                .find((el) => visible(el) && /(?:^|\s)upload(?:\s|$)/.test(el.innerText || el.textContent || ''));
            if (!button) return false;
            button.click();
            return true;
        }) : false;
        if (uploadOpened) await page.wait(0.5);
        const fileInputs = uploadOpened ? await page.evaluate(() =>
            Array.from(document.querySelectorAll('input[type="file"]')).map((el, index) => ({
                index,
                accept: el.getAttribute('accept'),
                multiple: el.multiple,
                ariaLabel: el.getAttribute('aria-label'),
                className: String(el.className || ''),
            }))) : [];

        return [{
            projectId,
            url,
            controls: JSON.stringify({ before: controls, afterCreate, fileInputs }),
        }];
    },
});

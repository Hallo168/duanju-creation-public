import { cli, Strategy } from '@jackwener/opencli/registry';

cli({
    site: 'google-flow',
    name: 'prepared-state',
    description: 'Inspect the currently prepared Google Flow prompt without changing it',
    access: 'read',
    domain: 'labs.google',
    strategy: Strategy.UI,
    browser: true,
    navigateBefore: false,
    defaultWindowMode: 'background',
    siteSession: 'persistent',
    columns: ['url', 'prompt', 'referenceCount', 'references', 'submitEnabled', 'diagnostics'],
    func: async (page) => {
        const state = await page.evaluate(() => {
            const visible = (el) => {
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };
            const editor = Array.from(document.querySelectorAll('[contenteditable="true"][role="textbox"]'))
                .find(visible);
            const footer = editor?.parentElement?.parentElement?.parentElement || document;
            const refs = Array.from(footer.querySelectorAll('img'))
                .filter(visible)
                .map((img) => img.getAttribute('alt') || img.getAttribute('src') || '')
                .filter(Boolean);
            const submit = Array.from(footer.querySelectorAll('button'))
                .find((button) => /(?:^|\s)arrow_forward(?:\s|$)/.test(button.innerText || button.textContent || ''));
            return {
                url: location.href,
                prompt: String(editor?.innerText || editor?.textContent || '').trim(),
                references: refs,
                submitEnabled: !!submit && !submit.disabled && !submit.hasAttribute('disabled'),
                diagnostics: {
                    visibleButtons: Array.from(document.querySelectorAll('button'))
                        .filter(visible)
                        .map((button) => ({
                            text: String(button.innerText || button.textContent || '').trim().slice(0, 120),
                            disabled: button.disabled || button.hasAttribute('disabled'),
                        }))
                        .slice(-30),
                    options: Array.from(document.querySelectorAll('[role="option"]'))
                        .filter(visible)
                        .map((option) => ({
                            text: String(option.innerText || option.textContent || '').trim().slice(0, 160),
                            selected: option.getAttribute('aria-selected'),
                            className: String(option.className || '').slice(0, 120),
                        }))
                        .slice(-10),
                    images: Array.from(document.querySelectorAll('img'))
                        .filter(visible)
                        .map((img) => ({
                            alt: img.getAttribute('alt') || '',
                            src: String(img.currentSrc || img.src || '').slice(-120),
                        }))
                        .slice(-20),
                    dialogs: Array.from(document.querySelectorAll('[role="dialog"], [role="alert"], [role="status"], [aria-live]'))
                        .filter(visible)
                        .map((item) => String(item.innerText || item.textContent || '').trim().slice(0, 500))
                        .filter(Boolean)
                        .slice(-10),
                },
            };
        });
        return [{
            url: state.url,
            prompt: state.prompt,
            referenceCount: state.references.length,
            references: state.references.join('\n'),
            submitEnabled: state.submitEnabled,
            diagnostics: JSON.stringify(state.diagnostics),
        }];
    },
});

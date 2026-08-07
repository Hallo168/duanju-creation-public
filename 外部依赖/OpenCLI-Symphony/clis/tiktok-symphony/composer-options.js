// tiktok-symphony composer-options — what the composer footer actually offers.
import { cli, Strategy } from '@jackwener/opencli/registry';
import { CommandExecutionError } from '@jackwener/opencli/errors';
import {
    COMPOSER_URL,
    DEEP_QUERY_SRC,
    DROP_ZONE_SELECTOR,
    HOST,
    VIDEO_MODES,
    resolveVideoMode,
    waitForValue,
} from './utils.js';

cli({
    site: 'tiktok-symphony',
    name: 'composer-options',
    description: 'List the model / duration / ratio choices the composer footer currently offers (spends no credits)',
    access: 'read',
    example: 'opencli tiktok-symphony composer-options --mode reference',
    domain: HOST,
    strategy: Strategy.UI,
    browser: true,
    navigateBefore: false,
    args: [
        {
            name: 'mode',
            type: 'string',
            default: 'reference',
            help: `Sub-app to open: ${VIDEO_MODES.map((m) => m.key).join(' / ')}`,
        },
    ],
    columns: ['control', 'current', 'options'],
    func: async (page, args) => {
        const mode = resolveVideoMode(args.mode);
        await page.goto(`${COMPOSER_URL}?subApp=${mode.subApp}`, { waitUntil: 'load' });
        await waitForValue(page, `(() => !!document.querySelector('${DROP_ZONE_SELECTOR}') || null)()`, {
            label: 'Symphony composer',
            timeoutMs: 30000,
        });

        const labels = await waitForValue(page, `(() => {
            ${DEEP_QUERY_SRC}
            const footer = document.querySelector('[data-chatbox-part=footer]');
            if (!footer) return null;
            const out = __deepAll(footer, (el) => __ksTag(el, 'ks-dropdown-menu')).map((dd) => {
                const btn = __deepAll(dd, (el) => __ksTag(el, 'ks-button'))[0];
                return btn ? __ownText(btn) : '';
            });
            return out.length ? out : null;
        })()`, { label: 'composer footer', timeoutMs: 30000 });

        if (!labels) throw new CommandExecutionError('the composer footer exposed no dropdowns');

        // The options only mount once a menu is opened, so each one has to be
        // clicked. Reading them without opening was tried first and silently
        // returned empty lists, which reads the same as "this control has no
        // choices" — worth knowing before trusting a cached option list.
        const controls = [];
        for (const label of labels) {
            if (!label) continue;
            const options = await openAndReadOptions(page, label);
            controls.push({ current: label, options });
            await page.nativeKeyPress('Escape');
            await page.wait(0.5);
        }

        return controls.map((c, i) => ({
            control: `dropdown ${i + 1}`,
            current: c.current || null,
            options: c.options.join(' | '),
        }));
    },
});

/** Page-context source listing every laid-out element's own text. */
const VISIBLE_TEXTS_SRC = `(() => {
    ${DEEP_QUERY_SRC}
    const seen = new Set();
    for (const el of __deepAll(document, (x) => {
        if (__ksTag(x, 'ks-button')) return false;
        const t = __ownText(x);
        if (!t || t.length > 40) return false;
        return x.getBoundingClientRect().width > 0;
    })) seen.add(__ownText(el));
    return [...seen];
})()`;

/**
 * Open the footer dropdown whose trigger currently reads `label`, and report
 * what appeared.
 *
 * Addressing the trigger by its label rather than by position matters: the
 * footer's ks-dropdown-menu elements nest, so indexing into them opened the
 * model menu when asked for the duration one.
 */
async function openAndReadOptions(page, label) {
    const before = new Set(await page.evaluate(VISIBLE_TEXTS_SRC));

    const opened = await page.evaluate(`(() => {
        ${DEEP_QUERY_SRC}
        const want = ${JSON.stringify(label)};
        const footer = document.querySelector('[data-chatbox-part=footer]');
        const btn = __deepAll(footer, (el) => __ksTag(el, 'ks-button') && __ownText(el) === want)[0];
        if (!btn) return false;
        // ks-button wraps a real <button> in its shadow root; clicking the host
        // does nothing.
        const real = (btn.shadowRoot && btn.shadowRoot.querySelector('button')) || btn;
        real.click();
        return true;
    })()`);
    if (!opened) return [];

    await page.wait(0.8);
    if (process.env.OC_SYMPHONY_SHOT) {
        const slug = label.replace(/[^A-Za-z0-9]+/g, '-').toLowerCase();
        await page.screenshot({ path: `${process.env.OC_SYMPHONY_SHOT}-${slug}.png` });
    }
    // Report only what the click revealed. Matching option elements by shape
    // pulled in the whole page chrome; a before/after diff needs no selector at
    // all and survives any restyle.
    const after = await page.evaluate(VISIBLE_TEXTS_SRC);
    const revealed = after.filter((t) => !before.has(t));

    // Not every footer control is a menu — duration is a slider with a number
    // box — so report any input the click revealed alongside the text options.
    const inputs = await page.evaluate(`(() => {
        ${DEEP_QUERY_SRC}
        return __deepAll(document, (el) =>
            (el.tagName === 'INPUT' || el.getAttribute('role') === 'slider')
            && el.getBoundingClientRect().width > 0)
            .map((el) => ({
                tag: el.tagName.toLowerCase(),
                type: el.getAttribute('type'),
                value: el.value !== undefined ? el.value : null,
                min: el.getAttribute('min') || el.getAttribute('aria-valuemin'),
                max: el.getAttribute('max') || el.getAttribute('aria-valuemax'),
                step: el.getAttribute('step'),
                role: el.getAttribute('role'),
            }));
    })()`);
    for (const inp of inputs) {
        revealed.push(`<${inp.tag}${inp.type ? ` type=${inp.type}` : ''}${inp.role ? ` role=${inp.role}` : ''} value=${inp.value} min=${inp.min} max=${inp.max} step=${inp.step}>`);
    }
    return revealed;
}

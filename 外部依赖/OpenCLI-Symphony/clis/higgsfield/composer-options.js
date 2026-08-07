import { cli, Strategy } from '@jackwener/opencli/registry';
import { CommandExecutionError } from '@jackwener/opencli/errors';
import {
    CONTROL_LABELS,
    HOST,
    UNLIMITED_LABEL,
    browserSource,
    openComposer,
    countLibraryTiles,
    openPicker,
    readControls,
    readIdentity,
    selectNewestUpload,
    uploadToLibrary,
    waitUploadReady,
} from './utils.js';

/**
 * Read-only reconnaissance of the video composer.
 *
 * This never clicks Generate, so it costs no credits. Use it to confirm the
 * exact option labels (they differ from the marketing names) before wiring a
 * generation run, and to re-verify selectors after Higgsfield ships a redesign.
 */
cli({
    site: 'higgsfield',
    name: 'composer-options',
    description: 'Inspect the Higgsfield video composer controls and their available options without generating',
    access: 'read',
    domain: HOST,
    strategy: Strategy.UI,
    browser: true,
    navigateBefore: false,
    defaultWindowMode: 'background',
    siteSession: 'persistent',
    args: [
        { name: 'expand', type: 'boolean', default: true, help: 'Open each option picker to enumerate its choices' },
        { name: 'raw', type: 'boolean', default: false, help: 'Also dump every visible control on the page' },
        {
            name: 'click',
            type: 'string',
            default: '',
            help: 'Before dumping, click the composer control whose text matches this (read-only probe of what it reveals)',
        },
        { name: 'screenshot', type: 'string', default: '', help: 'Save a full-page screenshot to this path' },
        { name: 'upload', type: 'string', default: '', help: 'Comma-separated local image paths to upload into the library before probing' },
        { name: 'wait-after', type: 'int', default: 3, help: 'Seconds to wait after uploading before probing/screenshotting' },
        { name: 'select-latest', type: 'boolean', default: false, help: 'After each upload, click the newest tile to select it into the generation' },
    ],
    columns: ['account', 'controls', 'options', 'raw'],
    func: async (page, args) => {
        await openComposer(page);
        const identity = await readIdentity(page).catch(() => null);

        const uploadList = String(args.upload ?? '').split(',').map((item) => item.trim()).filter(Boolean);
        if (uploadList.length) {
            if (args['select-latest'] === true) {
                // Exercise the real upload → wait-ready → click-to-select path.
                for (const item of uploadList) {
                    const baseline = await countLibraryTiles(page).catch(() => 0);
                    await uploadToLibrary(page, [item]);
                    await waitUploadReady(page, baseline);
                    await selectNewestUpload(page);
                }
            } else {
                await uploadToLibrary(page, uploadList);
            }
            await page.wait(Math.max(3, Number(args['wait-after'] || 3)));
        }

        // Comma-separated steps, so a nested panel can be reached in one probe
        // (e.g. --click "Elements,Uploads").
        const clickSteps = String(args.click ?? '').split(',').map((step) => step.trim()).filter(Boolean);
        for (const step of clickSteps) {
            const spot = await page.evaluate(browserSource(`
                const want = ${JSON.stringify(step.toLowerCase())};
                const nodes = Array.from(document.querySelectorAll('button,[role="button"],label,div,section')).filter(visible);
                // Exact match first, then the smallest node that contains the
                // text, so "Upload media\\nImage, Video or Audio" still resolves.
                const exact = nodes.find((el) => text(el).toLowerCase() === want);
                const contains = nodes.filter((el) => text(el).toLowerCase().includes(want))
                    .sort((a, b) => text(a).length - text(b).length)[0];
                const target = exact || contains;
                if (!target) return null;
                target.scrollIntoView({ block: 'center' });
                return rectOf(target);
            `));
            if (!spot) {
                // Screenshot before bailing so the real page state is captured.
                const shot = String(args.screenshot ?? '').trim();
                if (shot) await page.screenshot({ path: shot, fullPage: true });
                throw new CommandExecutionError(`no control matched "${step}"`);
            }
            await page.nativeClick(Number(spot.x), Number(spot.y));
            await page.wait(1.5);
        }

        const shotPath = String(args.screenshot ?? '').trim();
        if (shotPath) await page.screenshot({ path: shotPath, fullPage: true });

        const controls = await readControls(page);

        const options = {};
        if (args.expand !== false) {
            for (const [key, label] of Object.entries(CONTROL_LABELS)) {
                if (!controls[key]) {
                    options[key] = { error: 'control not found' };
                    continue;
                }
                try {
                    await openPicker(page, label);
                    options[key] = await page.evaluate(browserSource(`
                        const items = Array.from(document.querySelectorAll(
                            '[role="option"],[role="menuitem"],[role="menuitemradio"],[role="radio"]',
                        )).filter(visible);
                        const source = items.length
                            ? items
                            : Array.from(document.querySelectorAll('li,button')).filter(visible);
                        return source.map((el) => ({
                            text: text(el).slice(0, 80),
                            role: el.getAttribute('role'),
                            selected: el.getAttribute('aria-selected') || el.getAttribute('aria-checked'),
                            disabled: !!(el.disabled || el.hasAttribute('disabled')),
                        })).slice(0, 60);
                    `));
                } catch (error) {
                    options[key] = { error: String(error?.message || error) };
                }
                // Leave the composer as we found it so a later command starts clean.
                await page.pressKey?.('Escape').catch(() => {});
                await page.wait(0.4);
            }
        }

        const raw = args.raw === true ? await page.evaluate(browserSource(`
            return Array.from(document.querySelectorAll(
                'button,input,textarea,[contenteditable="true"],[role="button"],[role="switch"],[role="slider"]',
            )).filter(visible).map((el) => ({
                tag: el.tagName,
                role: el.getAttribute('role'),
                type: el.getAttribute('type'),
                ariaLabel: label(el),
                ariaChecked: el.getAttribute('aria-checked'),
                disabled: !!(el.disabled || el.hasAttribute('disabled')),
                text: text(el).slice(0, 100),
                rect: rectOf(el),
            })).slice(0, 150);
        `)) : [];

        // Reference thumbnails and their remove buttons, so leftovers from a
        // previous session can be detected and cleared before generating.
        const references = args.raw === true ? await page.evaluate(browserSource(`
            const editor = Array.from(document.querySelectorAll('[contenteditable="true"],textarea')).filter(visible)[0];
            const cutoff = editor ? editor.getBoundingClientRect().top : 99999;
            const imgs = Array.from(document.querySelectorAll('img')).filter(visible)
                .filter((el) => el.getBoundingClientRect().top < cutoff);
            return imgs.map((img) => {
                let card = img;
                for (let i = 0; i < 5 && card.parentElement; i += 1) {
                    if (card.querySelector('button')) break;
                    card = card.parentElement;
                }
                return {
                    src: (img.getAttribute('src') || '').slice(0, 120),
                    alt: img.getAttribute('alt') || '',
                    rect: rectOf(img),
                    buttons: Array.from(card.querySelectorAll('button')).filter(visible).map((el) => ({
                        ariaLabel: label(el),
                        text: text(el).slice(0, 30),
                        rect: rectOf(el),
                    })),
                };
            }).slice(0, 12);
        `)) : [];

        // File inputs are often hidden and mounted only after an "add" control is
        // clicked, so report them regardless of visibility.
        const fileInputs = args.raw === true ? await page.evaluate(browserSource(`
            const inputs = Array.from(document.querySelectorAll('input[type="file"]')).map((el) => ({
                accept: el.getAttribute('accept') || '',
                multiple: !!el.multiple,
                hidden: el.getBoundingClientRect().width === 0,
            }));
            const clickable = Array.from(document.querySelectorAll('button,[role="button"],label')).filter(visible)
                .filter((el) => {
                    const rect = el.getBoundingClientRect();
                    return rect.left < 620 && rect.width <= 120 && rect.height <= 120;
                })
                .map((el) => ({
                    tag: el.tagName,
                    ariaLabel: label(el),
                    text: text(el).slice(0, 30),
                    rect: rectOf(el),
                    hasSvg: !!el.querySelector('svg'),
                }));
            return { inputs, clickable };
        `)) : { inputs: [], clickable: [] };

        // Focused view of the asset panel: what a tile looks like and what is
        // clickable around it. Deliberately small so it is not truncated.
        const panel = args.raw === true ? await page.evaluate(browserSource(`
            const imgs = Array.from(document.querySelectorAll('img')).filter(visible).filter((img) => {
                const rect = img.getBoundingClientRect();
                return rect.left >= 340 && rect.left < 1400 && rect.width >= 40;
            }).slice(0, 6);
            return imgs.map((img) => {
                const holder = img.closest('button,[role="button"],li,[data-testid],a') || img.parentElement;
                return {
                    rect: rectOf(img),
                    alt: (img.getAttribute('alt') || '').slice(0, 30),
                    src: (img.getAttribute('src') || '').slice(-60),
                    holder: holder ? holder.tagName + (holder.getAttribute('role') ? '[' + holder.getAttribute('role') + ']' : '') : null,
                    holderText: holder ? text(holder).slice(0, 30) : null,
                };
            });
        `)) : [];

        // Every image in the composer column above the prompt, unfiltered, so an
        // upload whose src/markup differs from expectations is still visible.
        const refRegion = args.raw === true ? await page.evaluate(browserSource(`
            const editor = Array.from(document.querySelectorAll('[contenteditable="true"],textarea')).filter(visible)[0];
            const cutoff = editor ? editor.getBoundingClientRect().top : 700;
            return Array.from(document.querySelectorAll('*')).filter(visible).filter((el) => {
                const rect = el.getBoundingClientRect();
                return rect.left < 340 && rect.top > 120 && rect.top < cutoff && rect.width >= 12 && rect.width <= 200 && rect.height >= 12;
            }).map((el) => ({
                tag: el.tagName,
                cls: String(el.className || '').slice(0, 44),
                rect: rectOf(el),
                bg: /url\\(/.test(getComputedStyle(el).backgroundImage || '') ? 'yes' : '',
                img: !!el.querySelector('img'),
            })).slice(0, 40);
        `)) : [];

        const leftImages = args.raw === true ? await page.evaluate(browserSource(`
            const editor = Array.from(document.querySelectorAll('[contenteditable="true"],textarea')).filter(visible)[0];
            const cutoff = editor ? editor.getBoundingClientRect().top : 700;
            // Reference thumbnails render as divs with a background-image, not
            // <img>, so include those.
            const bgNodes = Array.from(document.querySelectorAll('div,figure,span')).filter(visible).filter((el) => {
                const rect = el.getBoundingClientRect();
                if (!(rect.left < 620 && rect.top < cutoff && rect.width >= 32 && rect.width <= 160)) return false;
                const bg = getComputedStyle(el).backgroundImage || '';
                return /url\\(/.test(bg);
            }).map((el) => ({ kind: 'bg', rect: rectOf(el), bg: (getComputedStyle(el).backgroundImage || '').slice(0, 90) }));
            const imgNodes = Array.from(document.querySelectorAll('img')).filter(visible).filter((img) => {
                const rect = img.getBoundingClientRect();
                return rect.left < 620 && rect.top < cutoff;
            }).map((img) => {
                let card = img;
                for (let i = 0; i < 4 && card.parentElement; i += 1) { if (card.querySelector('button')) break; card = card.parentElement; }
                return {
                    rect: rectOf(img),
                    alt: (img.getAttribute('alt') || '').slice(0, 30),
                    src: (img.getAttribute('src') || '').slice(0, 90),
                    buttons: Array.from(card.querySelectorAll('button')).filter(visible)
                        .map((b) => ({ aria: label(b), text: text(b).slice(0, 16), rect: rectOf(b) })),
                };
            });
            return { bg: bgNodes, img: imgNodes };
        `)) : { bg: [], img: [] };

        // The "Upload media" dropzone: its inner HTML plus every input/button
        // inside, so the real image-upload trigger can be identified.
        const uploadZone = args.raw === true ? await page.evaluate(browserSource(`
            const zone = Array.from(document.querySelectorAll('div,section,label')).filter(visible)
                .filter((el) => /upload media/i.test(text(el)))
                .sort((a, b) => text(a).length - text(b).length)[0];
            if (!zone) return { found: false };
            const container = zone.closest('div') || zone;
            const wide = container.parentElement || container;
            return {
                found: true,
                html: wide.outerHTML.slice(0, 1600),
                inputs: Array.from(wide.querySelectorAll('input')).map((el) => ({
                    type: el.getAttribute('type'), accept: (el.getAttribute('accept') || '').slice(0, 40),
                })),
                buttons: Array.from(wide.querySelectorAll('button,[role="button"],label')).filter(visible)
                    .map((el) => ({ tag: el.tagName, aria: label(el), text: text(el).slice(0, 20), rect: rectOf(el) })),
            };
        `)) : null;

        return [{
            account: identity?.email || identity?.state || 'unknown',
            controls: JSON.stringify({ ...controls, unlimitedLabel: UNLIMITED_LABEL }),
            options: JSON.stringify(options),
            raw: args.raw === true ? JSON.stringify({ refRegion, leftImages, uploadZone, panel, references }) : '',
        }];
    },
});

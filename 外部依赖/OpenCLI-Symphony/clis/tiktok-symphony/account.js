// tiktok-symphony account — list the sub-accounts on this login, or switch.
import { cli, Strategy } from '@jackwener/opencli/registry';
import { ArgumentError, CommandExecutionError } from '@jackwener/opencli/errors';
import { CREATE_URL, DEEP_QUERY_SRC, HOST, waitForValue } from './utils.js';

/**
 * Read the account menu.
 *
 * Every row renders as "<name>ID: <digits>" (full-width "ID：" when the UI
 * locale is Chinese — match both colons or the menu parses as empty), and the
 * panel header repeats the
 * same pair for whichever account is live — comparing those ids is what makes
 * "active" trustworthy. The visible-rect filter matters because the menu stays
 * mounted while closed.
 */
const MENU_SRC = `(() => {
    ${DEEP_QUERY_SRC}
    const idEls = __deepAll(document, (el) => /^ID[:：]\\s*\\d{10,}$/.test((el.textContent || '').trim())
        && el.getBoundingClientRect().width > 0);
    if (idEls.length < 2) return null;

    const parse = (text) => {
        const m = /^(.*?)ID[:：]\\s*(\\d{10,})/.exec((text || '').trim());
        return m ? { name: m[1].trim(), id: m[2] } : null;
    };

    // The first match sits in the panel header; the rest are the switch list.
    let head = idEls[0];
    for (let i = 0; i < 3 && head.parentElement; i++) head = head.parentElement;
    const active = parse(head.textContent);

    const rows = idEls.slice(1).map((el) => {
        let row = el;
        for (let i = 0; i < 3 && row.parentElement; i++) row = row.parentElement;
        const info = parse(row.textContent);
        if (!info) return null;
        // Aim at the radio, not the row: clicking the row's centre (over the
        // name text) leaves the selection unchanged.
        const radio = __deepAll(row, (x) => __ksTag(x, 'ks-radio'))[0];
        const r = (radio || row).getBoundingClientRect();
        return { ...info, x: r.x + r.width / 2, y: r.y + r.height / 2 };
    }).filter(Boolean);

    return { active, rows };
})()`;

cli({
    site: 'tiktok-symphony',
    name: 'account',
    description: 'List the sub-accounts on this login, or switch the active one',
    access: 'write',
    example: 'opencli tiktok-symphony account --use main-a',
    domain: HOST,
    strategy: Strategy.UI,
    browser: true,
    navigateBefore: CREATE_URL,
    // The menu opens on a real pointer press; a background tab never gets one.
    defaultWindowMode: 'foreground',
    args: [
        { name: 'use', type: 'string', default: '', help: 'Switch to this sub-account (name or numeric ID)' },
        {
            name: 'hold',
            type: 'int',
            default: 0,
            help: 'Watch for this many seconds while you switch accounts by hand, and report what the page did',
        },
    ],
    columns: ['name', 'id', 'active'],
    func: async (page, args) => {
        const holdSec = Math.max(0, Number(args.hold) || 0);
        if (holdSec) return watchManualSwitch(page, holdSec);

        const want = String(args.use ?? '').trim();

        let menu = await openAccountMenu(page);
        if (process.env.OC_SYMPHONY_SHOT) {
            await page.screenshot({ path: `${process.env.OC_SYMPHONY_SHOT}-account-menu.png` });
        }
        if (!want) {
            return menu.rows.map((r) => ({ name: r.name, id: r.id, active: r.id === menu.active?.id }));
        }

        const target = menu.rows.find((r) => r.id === want || r.name === want);
        if (!target) {
            throw new ArgumentError(
                `no sub-account "${want}" — this login has: ${menu.rows.map((r) => r.name).join(', ')}`,
            );
        }

        // One click is not reliably one switch: the menu closes on any outside
        // interaction, and the quads click's own scrollIntoView can BE that
        // outside interaction on a scrollable page. So treat click-and-verify
        // as a unit and repeat it, reopening the menu each round.
        for (let round = 0; round < 3; round++) {
            if (menu?.active?.id === target.id) {
                return [{ name: target.name, id: target.id, active: true }];
            }
            if (menu) {
                const currentTarget = menu.rows.find((row) => row.id === target.id);
                if (currentTarget) {
                    const activated = await page.evaluate(activateRadioSrc(target.id));
                    if (activated) {
                        await page.wait(0.5);
                    }
                    // The menu itself only opens for a native pointer sequence.
                    // Its radio rows have the same constraint: clickWithQuads
                    // scrolls first, which can close the popover before the
                    // click lands. MENU_SRC already measured the radio centre,
                    // so use those fresh viewport coordinates directly.
                    const x = Math.round(currentTarget.x);
                    const y = Math.round(currentTarget.y);
                    if (!activated) {
                        await page.cdp('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
                        await page.wait(0.35);
                        await page.cdp('Input.dispatchMouseEvent', {
                            type: 'mousePressed',
                            x,
                            y,
                            button: 'left',
                            buttons: 1,
                            clickCount: 1,
                        });
                        await page.cdp('Input.dispatchMouseEvent', {
                            type: 'mouseReleased',
                            x,
                            y,
                            button: 'left',
                            buttons: 0,
                            clickCount: 1,
                        });
                    }
                    if (process.env.OC_SYMPHONY_SHOT) {
                        await page.wait(0.5);
                        await page.screenshot({
                            path: `${process.env.OC_SYMPHONY_SHOT}-account-click-${round}.png`,
                        });
                    }
                }
            }
            // Switching re-authenticates and reloads, so poll the menu rather
            // than trusting the click: submitting to the wrong sub-account
            // spends the wrong balance and fills the wrong Library.
            for (let attempt = 0; attempt < 8; attempt++) {
                await page.wait(1.5);
                menu = await openAccountMenu(page).catch(() => null);
                if (menu?.active?.id === target.id) {
                    return [{ name: menu.active.name, id: menu.active.id, active: true }];
                }
                if (menu) break; // menu is open again but not switched — go re-click
            }
        }
        throw new CommandExecutionError(`switched to "${want}" but the menu still reports ${menu?.active?.name ?? 'nothing'}`);
    },
});

/**
 * Watch while a human switches accounts.
 *
 * Clicking the switcher through CDP reports a hit on the right row and changes
 * nothing, so the question is what a real switch does that the synthetic one
 * does not. The request log across the switch is the answer; the credit balance
 * is sampled alongside it because it is the only per-account value visible
 * without reopening the menu.
 */
async function watchManualSwitch(page, seconds) {
    process.stderr.write(
        `\n>>> Watching for ${seconds}s. Switch sub-account by hand now:\n`
        + '>>> click the avatar (top right), pick a different account, and let the page settle.\n\n',
    );

    const capturing = await page.startNetworkCapture('');
    const balanceSrc = `(() => {
        ${DEEP_QUERY_SRC}
        const coin = __deepAll(document, (el) => __ksTag(el, 'ks-icon') && el.getAttribute('name') === 'coin')[0];
        return coin && coin.parentElement ? coin.parentElement.textContent.trim() : null;
    })()`;

    const seen = [];
    const rounds = Math.ceil(seconds / 0.5);
    for (let i = 0; i < rounds; i++) {
        const balance = await page.evaluate(balanceSrc).catch(() => null);
        if (!seen.length || seen[seen.length - 1].balance !== balance) {
            seen.push({ round: i, balance });
        }
        await page.wait(0.5);
    }

    const net = capturing ? await page.readNetworkCapture() : [];
    // Drop the telemetry firehose; what matters is calls to the app's own API.
    const apiCalls = net
        .map((n) => `${n.method || ''} ${(n.url || '').split('?')[0]}`)
        .filter((u) => /ads\.tiktok\.com|passport|account|login|user/i.test(u)
            && !/monitor_browser|mcs|\/list$/.test(u));

    return [{
        name: `balances seen: ${JSON.stringify(seen.map((s) => s.balance))}`,
        id: `api calls: ${JSON.stringify([...new Set(apiCalls)])}`,
        active: seen.length > 1,
    }];
}

/** Trigger the actual radio input/label once the native-opened menu exists. */
function activateRadioSrc(accountId) {
    return `(() => {
        ${DEEP_QUERY_SRC}
        const idEls = __deepAll(document, (el) => /^ID[:：]\\s*\\d{10,}$/.test((el.textContent || '').trim())
            && el.getBoundingClientRect().width > 0);
        for (const el of idEls.slice(1)) {
            let row = el;
            for (let i = 0; i < 3 && row.parentElement; i++) row = row.parentElement;
            if (!(row.textContent || '').includes(${JSON.stringify(accountId)})) continue;
            const input = __deepAll(row, (x) => x.tagName === 'INPUT' && x.type === 'radio')[0];
            if (input) {
                input.click();
                return 'input';
            }
            const radio = __deepAll(row, (x) => __ksTag(x, 'ks-radio'))[0];
            if (radio) {
                radio.click();
                return 'ks-radio';
            }
            return null;
        }
        return null;
    })()`;
}

/** Open the avatar menu and read it. */
async function openAccountMenu(page) {
    for (let attempt = 0; attempt < 3; attempt++) {
        // Clicking the avatar toggles, so check before every retry. A menu can
        // finish animating just after the prior poll; blindly clicking again
        // would close it and turn a slow open into a false failure.
        const already = await page.evaluate(MENU_SRC);
        if (already) return already;

        const spot = await waitForValue(page, `(() => {
            ${DEEP_QUERY_SRC}
            const av = __deepAll(document, (el) => __ksTag(el, 'ks-avatar'))[0];
            if (!av) return null;
            const r = av.getBoundingClientRect();
            return r.width > 0 ? { x: r.x + r.width / 2, y: r.y + r.height / 2 } : null;
        })()`, { label: 'account avatar', timeoutMs: 30000 });

        // The menu opens on pointer HOVER (2026-08 site behaviour): a full
        // press/release toggles the freshly-opened popover shut again, which
        // is why click-only rounds always found it closed. Hover first and
        // give the row list time to load (it fills in async behind a spinner;
        // MENU_SRC stays null until at least one row has rendered). Only if
        // hovering alone does nothing, fall back to a native click.
        await page.cdp('Input.dispatchMouseEvent', { type: 'mouseMoved', x: spot.x, y: spot.y });
        let menu = await waitForValue(page, MENU_SRC, {
            label: 'account menu (hover)',
            timeoutMs: 8000,
            intervalMs: 250,
        }).catch(() => null);
        if (menu) return menu;

        await page.nativeClick(spot.x, spot.y);
        menu = await waitForValue(page, MENU_SRC, {
            label: 'account menu',
            timeoutMs: 4000,
            intervalMs: 250,
        }).catch(() => null);
        if (menu) return menu;
    }
    throw new CommandExecutionError('the account menu did not open after 3 native-click attempts');
}

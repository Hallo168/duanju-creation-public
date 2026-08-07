// tiktok-symphony direct-session — bootstrap browserless API credentials.
import { spawn } from 'node:child_process';
import { cli, Strategy } from '@jackwener/opencli/registry';
import { CommandExecutionError } from '@jackwener/opencli/errors';
import { CREATE_URL, HOST } from './utils.js';

const DEFAULT_PROXY_DIR = '<本机路径>';

cli({
    site: 'tiktok-symphony',
    name: 'direct-session',
    description: 'Securely store the current Symphony login for browserless direct API calls',
    access: 'read',
    domain: HOST,
    strategy: Strategy.UI,
    browser: true,
    navigateBefore: CREATE_URL,
    defaultWindowMode: 'background',
    siteSession: 'persistent',
    args: [
        {
            name: 'main-account',
            type: 'string',
            default: '',
            help: 'Stable local alias for this independent TikTok main-account login',
        },
        {
            name: 'browser-profile',
            type: 'string',
            default: '',
            help: 'OpenCLI Browser Bridge profile bound to this main account (defaults to --main-account)',
        },
    ],
    columns: ['stored', 'mainAccount', 'browserProfile', 'cookieCount', 'expiresAt'],
    func: async (page, args) => {
        const mainAccount = String(args['main-account'] ?? '').trim();
        const browserProfile = String(args['browser-profile'] ?? '').trim() || mainAccount;
        const capturing = await page.startNetworkCapture('generating-task-count');
        if (!capturing) throw new CommandExecutionError('network capture is unavailable; cannot bootstrap a direct session safely');

        const fresh = new URL(CREATE_URL);
        fresh.searchParams.set('_ocAuth', `${Date.now()}`);
        await page.goto(fresh.toString(), { waitUntil: 'load' });
        await page.wait(3);
        const entries = await page.readNetworkCapture();
        const requests = entries.filter((entry) =>
            /\/creative_bff_i18n\/api\/cue\/generating-task-count/i.test(entry?.url || ''));
        if (!requests.length) throw new CommandExecutionError('could not observe an authenticated Symphony API request');

        // A fresh navigation can leave both an early bootstrap request and the
        // fully authenticated request in the capture. Prefer the entry carrying
        // both identity fields instead of blindly using the first match.
        const request = [...requests].reverse().find((entry) => {
            const candidateHeaders = lowerCaseHeaders(entry.requestHeaders);
            const candidateUrl = new URL(entry.url);
            return Boolean(
                candidateHeaders['x-csrftoken']
                && (candidateUrl.searchParams.get('device_id') || candidateUrl.searchParams.get('did'))
            );
        }) || requests[requests.length - 1];

        const cookies = await page.getCookies({ url: 'https://ads.tiktok.com' });
        if (!cookies.length) throw new CommandExecutionError('Symphony cookies are unavailable');

        const headers = lowerCaseHeaders(request.requestHeaders);
        const url = new URL(request.url);
        const deviceId = url.searchParams.get('device_id') || url.searchParams.get('did') || '';
        const session = {
            version: 1,
            created_at: new Date().toISOString(),
            cookies: cookies.map(({ name, value, domain, path, expires, secure, httpOnly, sameSite }) => ({
                name, value, domain, path, expires, secure, httpOnly, sameSite,
            })),
            csrf: headers['x-csrftoken'] || '',
            fp_id: headers['x-fp-id'] || '',
            creative_source: headers['x-creative-source'] || 'CreativeStudio/MiniApp/ImageToVideo',
            device_id: deviceId,
            user_agent: headers['user-agent'] || '',
            sec_ch_ua: headers['sec-ch-ua'] || '',
            sec_ch_ua_mobile: headers['sec-ch-ua-mobile'] || '',
            sec_ch_ua_platform: headers['sec-ch-ua-platform'] || '',
        };
        if (!session.csrf) {
            throw new CommandExecutionError(
                `the observed Symphony request lacked required identity fields `
                + `(matches=${requests.length}, csrf=${Boolean(session.csrf)}, device=${Boolean(session.device_id)})`,
            );
        }

        await storeWithWindowsCredentialManager(session, mainAccount, browserProfile);
        const expiries = cookies
            .map((cookie) => Number(cookie.expires || 0))
            .filter((value) => Number.isFinite(value) && value > 0);
        return [{
            stored: true,
            mainAccount: mainAccount || null,
            browserProfile: browserProfile || null,
            cookieCount: cookies.length,
            expiresAt: expiries.length ? new Date(Math.min(...expiries) * 1000).toISOString() : null,
        }];
    },
});

function lowerCaseHeaders(headers = {}) {
    return Object.fromEntries(Object.entries(headers).map(([key, value]) => [key.toLowerCase(), String(value)]));
}

async function storeWithWindowsCredentialManager(session, mainAccount, browserProfile) {
    const proxyDir = process.env.SYMPHONY_PROXY_DIR || DEFAULT_PROXY_DIR;
    const args = [
        'run',
        '--project', proxyDir,
        'python', '-m', 'app.session_store', 'save',
    ];
    if (mainAccount) args.push('--main-account', mainAccount);
    if (browserProfile) args.push('--browser-profile', browserProfile);
    await new Promise((resolve, reject) => {
        const child = spawn('uv', args, {
            cwd: proxyDir,
            windowsHide: true,
            stdio: ['pipe', 'pipe', 'pipe'],
        });
        let stdout = '';
        let stderr = '';
        child.stdout.on('data', (chunk) => { stdout += chunk; });
        child.stderr.on('data', (chunk) => { stderr += chunk; });
        child.on('error', reject);
        child.on('close', (code) => {
            if (code === 0 && /"ok"\s*:\s*true/.test(stdout)) return resolve();
            reject(new CommandExecutionError(
                `could not store Symphony session in Windows Credential Manager: ${stderr.trim() || `exit ${code}`}`,
            ));
        });
        // Secrets travel over an anonymous stdin pipe, never argv or logs.
        child.stdin.end(JSON.stringify(session));
    });
}

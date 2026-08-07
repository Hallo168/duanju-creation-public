import { AuthRequiredError, CommandExecutionError } from '@jackwener/opencli/errors';
import { registerSiteAuthCommands } from '../_shared/site-auth.js';
import { HOST } from './utils.js';

const CLERK_ORIGIN = 'https://clerk.higgsfield.ai';
const APP_ORIGIN = `https://${HOST}`;

async function hasHiggsfieldClerkCookie(page) {
    // Clerk stores the session JWT on the application origin and only mirrors
    // some cookies onto its FAPI subdomain, so check the app origin first.
    // Clerk also hands out __client to anonymous visitors; only __session
    // implies an authenticated user, so gate on it to avoid navigating away
    // while the user is still finishing the Google OAuth hop.
    for (const url of [APP_ORIGIN, CLERK_ORIGIN]) {
        const cookies = await page.getCookies({ url }).catch(() => []);
        if (cookies.some((cookie) => cookie.name === '__session' && cookie.value)) return true;
    }
    return false;
}

async function verifyHiggsfieldIdentity(page) {
    if (!await hasHiggsfieldClerkCookie(page)) {
        throw new AuthRequiredError(HOST, 'Higgsfield Clerk __session cookie missing');
    }
    await page.goto(`https://${HOST}/`);
    await page.wait(2);
    const probe = await page.evaluate(`(async () => {
        try {
            const response = await fetch('${CLERK_ORIGIN}/v1/client?_clerk_js_version=5', {
                credentials: 'include',
                headers: { Accept: 'application/json' },
            });
            if (response.status === 401 || response.status === 403) {
                return { kind: 'auth', detail: 'clerk.higgsfield.ai /v1/client HTTP ' + response.status };
            }
            if (!response.ok) return { kind: 'http', httpStatus: response.status };
            const data = await response.json();
            const sessions = (data && data.response && data.response.sessions) || [];
            if (!Array.isArray(sessions) || sessions.length === 0) {
                return { kind: 'auth', detail: 'clerk.higgsfield.ai sessions=[] — anonymous' };
            }
            const active = sessions.find((session) => session.status === 'active') || sessions[0];
            const user = active && active.user;
            if (!user || !user.id) {
                return { kind: 'auth', detail: 'clerk.higgsfield.ai session present but no user.id — stale session' };
            }
            const email = (user.email_addresses && user.email_addresses[0] && user.email_addresses[0].email_address)
                || user.primary_email_address_id
                || '';
            const name = [user.first_name, user.last_name].filter(Boolean).join(' ').trim();
            return { ok: true, user_id: String(user.id), email: String(email), name };
        } catch (error) {
            return { kind: 'exception', detail: String((error && error.message) || error) };
        }
    })()`);
    if (probe?.kind === 'auth') throw new AuthRequiredError(HOST, probe.detail);
    if (probe?.kind === 'http') throw new CommandExecutionError(`HTTP ${probe.httpStatus} from clerk.higgsfield.ai`);
    if (probe?.kind === 'exception') throw new CommandExecutionError(`Higgsfield whoami failed: ${probe.detail}`);
    if (!probe?.ok) throw new CommandExecutionError(`Unexpected Higgsfield probe: ${JSON.stringify(probe)}`);
    return { user_id: probe.user_id, email: probe.email, name: probe.name };
}

registerSiteAuthCommands({
    site: 'higgsfield',
    domain: HOST,
    loginUrl: `https://${HOST}/`,
    columns: ['user_id', 'email', 'name'],
    quickCheck: hasHiggsfieldClerkCookie,
    verify: verifyHiggsfieldIdentity,
    poll: async (page) => {
        if (!await hasHiggsfieldClerkCookie(page)) {
            throw new AuthRequiredError(HOST, 'Waiting for the Higgsfield Clerk __session cookie');
        }
        return verifyHiggsfieldIdentity(page);
    },
});

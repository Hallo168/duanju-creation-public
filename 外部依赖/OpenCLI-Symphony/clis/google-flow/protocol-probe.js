import { cli, Strategy } from '@jackwener/opencli/registry';

cli({
    site: 'google-flow',
    name: 'protocol-probe',
    description: 'Read a redacted summary of captured Google Flow API traffic',
    access: 'read',
    domain: 'labs.google',
    strategy: Strategy.UI,
    browser: true,
    navigateBefore: false,
    defaultWindowMode: 'background',
    siteSession: 'persistent',
    columns: ['method', 'url', 'status', 'requestBody', 'responseBody'],
    func: async (page) => {
        const entries = await page.readNetworkCapture?.().catch(() => []) || [];
        const rows = [];
        for (const entry of entries) {
            const rawUrl = String(entry?.url || '');
            if (!rawUrl.includes('labs.google/fx/api/')) continue;
            let url;
            try {
                const parsed = new URL(rawUrl);
                url = `${parsed.origin}${parsed.pathname}`;
            } catch {
                continue;
            }
            rows.push({
                method: String(entry?.method || entry?.requestMethod || 'GET').toUpperCase(),
                url,
                status: Number(entry?.status || entry?.statusCode || 0) || null,
                requestBody: safeBody(entry?.postData || entry?.requestBody || entry?.body),
                responseBody: safeBody(entry?.responseBody || entry?.response),
            });
        }
        return rows;
    },
});

function safeBody(value) {
    if (value === undefined || value === null) return null;
    let text;
    try {
        text = typeof value === 'string' ? value : JSON.stringify(value);
    } catch {
        return '[unserializable]';
    }
    return text
        .replace(/("(?:token|authorization|cookie|csrf|signature)"\s*:\s*")[^"]+/gi, '$1[REDACTED]')
        .replace(/([?&](?:token|auth|key|signature)=)[^&\s]+/gi, '$1[REDACTED]')
        .slice(0, 4000);
}

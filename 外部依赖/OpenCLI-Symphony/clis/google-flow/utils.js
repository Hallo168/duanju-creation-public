import { readFile } from 'node:fs/promises';
import { ArgumentError, CommandExecutionError, TimeoutError } from '@jackwener/opencli/errors';

export const HOST = 'labs.google';
export const PROJECT_ID_RE = /^[0-9a-f-]{36}$/i;
export const EDITOR_SELECTOR = '[contenteditable="true"][role="textbox"]';
export const TEST_PROMPT = '以参考图为主体，保持原有构图与主要物体一致，派生成精致的中国水墨工笔插画风格，宣纸纹理，墨色层次清晰，柔和自然光，高细节，不添加文字。';

export function projectUrl(projectId) {
    const value = String(projectId ?? '').trim();
    if (!PROJECT_ID_RE.test(value)) {
        throw new ArgumentError('project must be a Google Flow project UUID');
    }
    return `https://${HOST}/fx/zh/tools/flow/project/${value}`;
}

export async function waitForValue(page, source, {
    label,
    timeoutMs = 30000,
    intervalMs = 500,
} = {}) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        const value = await page.evaluate(source);
        if (value) return value;
        await page.wait(intervalMs / 1000);
    }
    throw new TimeoutError(`Timed out waiting for ${label || 'Flow page state'}`, timeoutMs);
}

export async function openReferencePicker(page) {
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
    if (!opened) throw new CommandExecutionError('Google Flow reference picker button was not found');
    await waitForValue(page, `(() => {
        const input = document.querySelector('input[aria-label="搜索资源"]');
        return input && input.getBoundingClientRect().width > 0 ? true : null;
    })()`, { label: 'Google Flow reference picker' });
}

function addButtonExpression() {
    return `(el.innerText || el.textContent || '').trim() === '添加到提示'
        && el.getBoundingClientRect().width > 0`;
}

export async function uploadLocalReference(page, filePath, referenceName) {
    const opened = await page.evaluate(() => {
        const visible = (el) => {
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
        };
        const button = Array.from(document.querySelectorAll('button'))
            .find((el) => visible(el) && /(?:^|\s)upload(?:\s|$)/.test(el.innerText || el.textContent || ''));
        if (!button) return false;
        button.click();
        return true;
    });
    if (!opened) throw new CommandExecutionError('Google Flow upload media button was not found');

    await waitForValue(page, `(() =>
        document.querySelectorAll('input[type="file"][accept*="image"]').length || null
    )()`, { label: 'Google Flow image file input', timeoutMs: 15000 });

    const bytes = await readFile(filePath);
    const extension = referenceName.split('.').pop()?.toLowerCase();
    const mime = extension === 'png'
        ? 'image/png'
        : extension === 'webp'
            ? 'image/webp'
            : 'image/jpeg';
    const injected = await page.evaluate(({ base64, name, type }) => {
        const binary = atob(base64);
        const data = new Uint8Array(binary.length);
        for (let index = 0; index < binary.length; index += 1) data[index] = binary.charCodeAt(index);
        const file = new File([data], name, { type, lastModified: Date.now() });
        const transfer = new DataTransfer();
        transfer.items.add(file);
        const inputs = Array.from(document.querySelectorAll('input[type="file"][accept*="image"]'));
        const input = inputs.find((candidate) => candidate.accept === 'image/*') || inputs[0];
        if (!input) return false;
        input.files = transfer.files;
        input.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
        input.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
        return input.files?.[0]?.name || false;
    }, {
        base64: bytes.toString('base64'),
        name: referenceName,
        type: mime,
    });
    if (injected !== referenceName) {
        throw new CommandExecutionError(`could not inject local image "${referenceName}" into Flow`);
    }

    const selected = await waitForValue(page, `(() => {
        const want = ${JSON.stringify(referenceName)};
        const candidates = Array.from(document.querySelectorAll('img')).filter((img) =>
            (img.getAttribute('alt') || '') === want && img.getBoundingClientRect().width > 0);
        if (!candidates.length) return null;
        const src = candidates[0].getAttribute('src') || '';
        const button = Array.from(document.querySelectorAll('button')).find((el) =>
            ${addButtonExpression()});
        return src && button && !button.disabled ? { src } : null;
    })()`, { label: `upload "${referenceName}"`, timeoutMs: 180000, intervalMs: 1000 });

    const added = await page.evaluate(() => {
        const button = Array.from(document.querySelectorAll('button')).find((el) =>
            (el.innerText || el.textContent || '').trim() === '添加到提示'
            && el.getBoundingClientRect().width > 0);
        if (!button || button.disabled) return false;
        button.click();
        return true;
    });
    if (!added) throw new CommandExecutionError(`could not add uploaded reference "${referenceName}" to the prompt`);

    await waitForValue(page, `(() => {
        const src = ${JSON.stringify(selected.src)};
        const attached = Array.from(document.querySelectorAll('img')).some((img) =>
            img.getAttribute('src') === src
            && !img.closest('[role="option"]')
            && img.getBoundingClientRect().width > 0);
        return attached ? true : null;
    })()`, { label: `uploaded reference "${referenceName}" in prompt`, timeoutMs: 30000 });
    return selected.src;
}

export async function chooseUploadedReference(page, referenceName) {
    const name = String(referenceName ?? '').trim();
    if (!name) throw new ArgumentError('reference is required and must be an uploaded Flow asset name');
    await page.fillText('input[aria-label="搜索资源"]', name);

    const matches = await waitForValue(page, `(() => {
        const want = ${JSON.stringify(name)};
        const imgs = Array.from(document.querySelectorAll('img')).filter((img) =>
            (img.getAttribute('alt') || '') === want && img.getBoundingClientRect().width > 0);
        if (!imgs.length) return null;
        const options = Array.from(new Set(imgs.map((img) => img.closest('[role="option"]')).filter(Boolean)));
        return options.length ? options.map((option) => ({
            alt: want,
            src: option.querySelector('img[alt]')?.getAttribute('src') || '',
        })) : null;
    })()`, { label: `uploaded Flow asset "${name}"`, timeoutMs: 45000 });
    if (matches.length !== 1) {
        throw new CommandExecutionError(`uploaded Flow asset "${name}" is ambiguous`);
    }

    const selectedSrc = await page.evaluate((want) => {
        const options = Array.from(new Set(
            Array.from(document.querySelectorAll('img'))
                .filter((item) => (item.getAttribute('alt') || '') === want
                    && item.getBoundingClientRect().width > 0)
                .map((item) => item.closest('[role="option"]'))
                .filter(Boolean),
        ));
        if (options.length !== 1) return false;
        const src = options[0].querySelector('img[alt]')?.getAttribute('src') || '';
        options[0].click();
        return src || false;
    }, name);
    if (!selectedSrc) throw new CommandExecutionError(`could not select uploaded Flow asset "${name}"`);

    const outcome = await waitForValue(page, `(() => {
        const src = ${JSON.stringify(selectedSrc)};
        const attached = Array.from(document.querySelectorAll('img')).some((img) =>
            img.getAttribute('src') === src
            && !img.closest('[role="option"]')
            && img.getBoundingClientRect().width > 0);
        if (attached) return 'attached';
        const button = Array.from(document.querySelectorAll('button')).find((el) =>
            ${addButtonExpression()});
        return button && !button.disabled ? 'confirm' : null;
    })()`, { label: 'Add to prompt button' });

    if (outcome === 'confirm') {
        const added = await page.evaluate(() => {
            const button = Array.from(document.querySelectorAll('button')).find((el) =>
                (el.innerText || el.textContent || '').trim() === '添加到提示'
                && el.getBoundingClientRect().width > 0);
            if (!button || button.disabled) return false;
            button.click();
            return true;
        });
        if (!added) throw new CommandExecutionError('could not add the selected Flow asset to the prompt');
    }

    return waitForValue(page, `(() => {
        const src = ${JSON.stringify(selectedSrc)};
        const editor = document.querySelector('${EDITOR_SELECTOR}');
        if (!editor) return null;
        let root = editor;
        for (let i = 0; i < 6 && root.parentElement; i++) root = root.parentElement;
        const exact = Array.from(root.querySelectorAll('img')).filter((img) =>
            img.getAttribute('src') === src && img.getBoundingClientRect().width > 0);
        return exact.length === 1 ? exact.length : null;
    })()`, { label: `reference "${name}" in prompt`, timeoutMs: 30000 });
}

export async function fillPrompt(page, prompt) {
    const value = String(prompt ?? '').trim();
    if (!value) throw new ArgumentError('prompt is required and cannot be blank');
    await page.fillText(EDITOR_SELECTOR, value);
    const actual = await page.evaluate(`(() => {
        const editor = document.querySelector('${EDITOR_SELECTOR}');
        return String(editor?.innerText || editor?.textContent || '').trim();
    })()`);
    if (actual !== value) throw new CommandExecutionError('Google Flow prompt did not register exactly');
    return actual;
}

export async function submitEnabled(page) {
    return page.evaluate(() => {
        const visible = (el) => {
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
        };
        const button = Array.from(document.querySelectorAll('button')).find((el) =>
            visible(el) && /(?:^|\s)arrow_forward(?:\s|$)/.test(el.innerText || el.textContent || ''));
        return !!button && !button.disabled && !button.hasAttribute('disabled');
    });
}

export async function clickSubmit(page) {
    const marker = `opencli-flow-submit-${Date.now()}`;
    const marked = await page.evaluate((value) => {
        const visible = (el) => {
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
        };
        const button = Array.from(document.querySelectorAll('button')).find((el) =>
            visible(el) && /(?:^|\s)arrow_forward(?:\s|$)/.test(el.innerText || el.textContent || ''));
        if (!button || button.disabled || button.hasAttribute('disabled')) return false;
        button.setAttribute('data-opencli-flow-submit', value);
        return true;
    }, marker);
    if (!marked) throw new CommandExecutionError('Google Flow submit button is not enabled');
    try {
        await page.click(`[data-opencli-flow-submit="${marker}"]`);
    } finally {
        await page.evaluate((value) => {
            document.querySelector(`[data-opencli-flow-submit="${value}"]`)
                ?.removeAttribute('data-opencli-flow-submit');
        }, marker).catch(() => {});
    }
}

export function summarizeCapture(entries = []) {
    const safe = [];
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
        const method = String(entry?.method || entry?.requestMethod || 'GET').toUpperCase();
        const body = entry?.postData || entry?.requestBody || entry?.body;
        safe.push({
            method,
            url,
            status: Number(entry?.status || entry?.statusCode || 0) || null,
            bodyKeys: body && typeof body === 'object' ? Object.keys(body).sort() : [],
            bodyBytes: typeof body === 'string' ? body.length : null,
        });
    }
    return safe;
}

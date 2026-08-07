import { readFile, stat } from 'node:fs/promises';
import { resolve } from 'node:path';
import { cli, Strategy } from '@jackwener/opencli/registry';
import { ArgumentError, CommandExecutionError } from '@jackwener/opencli/errors';
import {
    HOST,
    clearReferences,
    clickGenerate,
    downloadVideo,
    fillPrompt,
    uploadAndSelect,
    installSubmitProbe,
    openComposer,
    readControls,
    readSubmitProbe,
    setOption,
    setUnlimited,
    waitForGeneration,
    waitForUnlimitedSwitch,
} from './utils.js';

const REFERENCE_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.webp'];

function normalizeList(value) {
    return String(value ?? '')
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean);
}

cli({
    site: 'higgsfield',
    name: 'generate-video',
    description: 'Generate a Higgsfield video through the real composer UI (Seedance 2.0, Unlimited mode)',
    access: 'write',
    domain: HOST,
    strategy: Strategy.UI,
    browser: true,
    navigateBefore: false,
    defaultWindowMode: 'background',
    siteSession: 'persistent',
    args: [
        { name: 'prompt', type: 'string', default: '', positional: true, help: 'Video prompt (or use --prompt-file)' },
        {
            name: 'prompt-file',
            type: 'string',
            default: '',
            help: 'Read the prompt from a UTF-8 file. Use this for storyboard prompts, which exceed the command-line length limit',
        },
        { name: 'ref', type: 'string', default: '', help: 'Comma-separated local reference image paths' },
        { name: 'duration', type: 'string', default: '15s', help: 'Target duration as shown in the composer, e.g. 15s' },
        { name: 'resolution', type: 'string', default: '720p', help: 'Target resolution, e.g. 720p' },
        { name: 'ratio', type: 'string', default: '9:16', help: 'Target aspect ratio, e.g. 9:16' },
        { name: 'unlimited', type: 'boolean', default: true, help: 'Require Unlimited mode to be on before submitting' },
        {
            name: 'prepare-only',
            type: 'boolean',
            default: true,
            help: 'Configure the composer but do NOT submit. Pass --prepare-only false to actually spend credits',
        },
        { name: 'wait', type: 'boolean', default: false, help: 'After submitting, poll until the video is ready' },
        { name: 'out', type: 'string', default: '', help: 'Download the finished video to this path (implies --wait)' },
        { name: 'timeout', type: 'int', default: 1200, help: 'Seconds to wait for the generation to finish' },
        {
            name: 'navigate',
            type: 'boolean',
            default: true,
            help: 'Reload the composer first. Pass --navigate false to reuse the page as-is (e.g. references picked by hand)',
        },
    ],
    columns: ['status', 'model', 'duration', 'resolution', 'ratio', 'unlimited', 'submit', 'prompt', 'references', 'jobId', 'video', 'download'],
    func: async (page, args) => {
        const promptFile = String(args['prompt-file'] ?? '').trim();
        const inlinePrompt = String(args.prompt ?? '').trim();
        if (promptFile && inlinePrompt) {
            throw new ArgumentError('pass either a positional prompt or --prompt-file, not both');
        }
        let prompt = inlinePrompt;
        if (promptFile) {
            const path = resolve(promptFile);
            const info = await stat(path).catch(() => null);
            if (!info?.isFile()) throw new ArgumentError(`prompt file does not exist: ${path}`);
            prompt = (await readFile(path, 'utf8')).trim();
        }
        if (!prompt) throw new ArgumentError('a prompt is required: pass it positionally or via --prompt-file');

        const refPaths = [];
        for (const raw of normalizeList(args.ref)) {
            const path = resolve(raw);
            const info = await stat(path).catch(() => null);
            if (!info?.isFile()) throw new ArgumentError(`reference image does not exist: ${path}`);
            if (!REFERENCE_EXTENSIONS.some((ext) => path.toLowerCase().endsWith(ext))) {
                throw new ArgumentError(`reference image must be PNG, JPEG, or WebP: ${path}`);
            }
            refPaths.push(path);
        }

        const wantDuration = String(args.duration ?? '').trim();
        const wantResolution = String(args.resolution ?? '').trim();
        const wantRatio = String(args.ratio ?? '').trim();
        const wantUnlimited = args.unlimited !== false;

        await openComposer(page, { navigate: args.navigate !== false });

        // Each setter short-circuits when the composer already shows the wanted
        // value, and throws rather than silently generating at the wrong
        // setting — a wrong setting still costs credits.
        const norm = (value) => String(value ?? '').replace(/\s+/g, ' ').trim().toLowerCase();
        const ratio = await setOption(
            page,
            'ratio',
            `value === ${JSON.stringify(wantRatio)}`,
            { expect: (value) => norm(value) === norm(wantRatio), label: `ratio ${wantRatio}` },
        );
        const resolution = await setOption(
            page,
            'resolution',
            `value === ${JSON.stringify(wantResolution)}`,
            { expect: (value) => norm(value) === norm(wantResolution), label: `resolution ${wantResolution}` },
        );
        const duration = await setOption(
            page,
            'duration',
            `value === ${JSON.stringify(wantDuration)}`,
            { expect: (value) => norm(value) === norm(wantDuration), label: `duration ${wantDuration}` },
        );

        let unlimited = null;
        if (wantUnlimited) {
            unlimited = await setUnlimited(page, true);
        } else {
            unlimited = await waitForUnlimitedSwitch(page).then((state) => state.checked).catch(() => null);
        }

        // Always clear stale references first. Higgsfield's persistent session
        // keeps the previous run's selected images, and leaving a person photo
        // attached to a landscape prompt makes the generation fail (verified
        // 2026-08-01). Text-to-video must therefore start from a clean composer.
        const cleared = await clearReferences(page);

        // Write the prompt, then upload and select each reference in order.
        // Higgsfield ties the click order of uploaded images to the prompt's
        // 图片1/图片2/… references, so refs must be passed in that exact order.
        await fillPrompt(page, prompt);
        const attached = refPaths.length ? await uploadAndSelect(page, refPaths) : 0;
        if (attached !== refPaths.length) {
            throw new CommandExecutionError(
                `attached ${attached} reference image(s) but ${refPaths.length} were requested`,
            );
        }

        const controls = await readControls(page);
        const submit = controls.submit;
        if (!submit) throw new CommandExecutionError('Higgsfield Generate button was not found after configuring the composer');

        const row = {
            model: controls.model?.value ?? null,
            duration,
            resolution,
            ratio,
            unlimited,
            submit: submit.text,
            prompt: `${prompt.length} chars`,
            references: `${attached} attached, ${cleared} stale cleared`,
        };

        // Everything above is free. Submitting is the billable step, so it is
        // opt-in: the default is prepare-only.
        if (args['prepare-only'] !== false) {
            return [{ ...row, status: 'prepared' }];
        }

        if (wantUnlimited && unlimited !== true) {
            throw new CommandExecutionError('refusing to submit: Unlimited mode is not on');
        }
        if (submit.disabled) {
            throw new CommandExecutionError(`refusing to submit: Generate button is disabled ("${submit.text}")`);
        }

        // Observe the composer's own job request so a submit is confirmed by
        // Higgsfield's response, not merely by our click landing. A click that
        // changes nothing otherwise reports success.
        await installSubmitProbe(page);
        const capturing = await page.startNetworkCapture?.('').catch(() => false);
        const clicked = await clickGenerate(page);
        // Poll rather than sleeping once: an accepted submit can stay pending
        // far longer than a rejected one, and a fixed short wait made a
        // successful generation look like a failure.
        const deadline = Date.now() + 90000;
        let probe = [];
        while (Date.now() < deadline) {
            await page.wait(3);
            probe = (await readSubmitProbe(page).catch(() => [])) || [];
            if (probe.length) break;
        }
        // Cross-check: if the in-page hooks miss the transport, OpenCLI's own
        // capture still proves whether a job request left the browser.
        const capturedJobCalls = capturing
            ? ((await page.readNetworkCapture().catch(() => [])) || [])
                .map((entry) => String(entry?.url || ''))
                .filter((url) => /\/jobs/.test(url))
            : [];
        const accepted = probe.filter((entry) => entry.status >= 200 && entry.status < 300);
        const rejected = probe.filter((entry) => entry.status >= 400 || entry.error);

        const after = await readControls(page);
        const evidence = JSON.stringify(probe.map((entry) => ({
            url: entry.url,
            status: entry.status ?? null,
            error: entry.error ?? null,
            request: String(entry.requestBody || '').slice(0, 600),
            response: String(entry.response || '').slice(0, 400),
        })));

        // A 429 means the request was well-formed but the workspace is already
        // at its concurrent-job limit — retrying immediately will fail the same
        // way, so surface it as its own condition rather than a rejection.
        const limited = probe.find((entry) => entry.status === 429);
        if (limited && !accepted.length) {
            let detail = null;
            try { detail = JSON.parse(limited.response || '{}').detail || null; } catch (error) { detail = null; }
            const cap = detail?.concurrent_jobs_limit;
            throw new CommandExecutionError(
                `Higgsfield is at its concurrent job limit${cap ? ` (${cap})` : ''} — wait for the running generation to finish, then retry. Request: ${String(limited.requestBody || '').slice(0, 400)}`,
            );
        }

        if (!probe.length) {
            if (capturedJobCalls.length) {
                // The request left the browser but its response was never seen.
                // The generation may well have started, so report the ambiguity
                // instead of failing — a retry here would double-submit.
                return [{
                    ...row,
                    status: 'submit_unconfirmed',
                    submit: `${clicked} | request sent, response not observed: ${capturedJobCalls[0]}`,
                }];
            }
            throw new CommandExecutionError(
                'clicked Generate but no job request left the browser; the composer did not accept the submit',
            );
        }
        if (!accepted.length) {
            throw new CommandExecutionError(`Higgsfield rejected the generation request: ${evidence}`);
        }

        // Pull the job id out of the accepted response for traceability.
        let jobId = null;
        try {
            const parsed = JSON.parse(accepted[0].response || '{}');
            // The real job id is job_sets[0].id; the top-level id is the
            // project/workspace id, not the job.
            jobId = (Array.isArray(parsed.job_sets) && parsed.job_sets[0] && parsed.job_sets[0].id)
                || parsed.job_id || parsed.job_set_id || parsed.id || null;
        } catch (error) {
            jobId = null;
        }

        const result = {
            ...row,
            status: 'submitted',
            submit: clicked,
            model: after.model?.value ?? row.model,
            jobId,
            evidence,
            video: null,
            download: null,
        };

        const wantOut = String(args.out ?? '').trim();
        if (args.wait === true || wantOut) {
            const finished = await waitForGeneration(page, { timeoutMs: Math.max(60, Number(args.timeout || 1200)) * 1000 });
            result.status = 'ready';
            result.video = finished.url;
            if (wantOut) {
                const saved = await downloadVideo(page, finished.url, resolve(wantOut));
                result.download = `${saved.path} (${saved.bytes} bytes, ${saved.via})`;
            }
        }

        return [result];
    },
});

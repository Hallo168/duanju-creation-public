import { describe, expect, it } from 'vitest';
import {
    inspectPreparedComposer,
    shouldSubmitGeneration,
    summarizeProtocolCapture,
    summarizeProtocolEndpoints,
} from './generate-video.js';

describe('tiktok-symphony prepare-only mode', () => {
    it('refuses to submit when prepare-only is enabled', () => {
        expect(shouldSubmitGeneration({ 'prepare-only': true })).toBe(false);
    });

    it('submits normally by default', () => {
        expect(shouldSubmitGeneration({})).toBe(true);
        expect(shouldSubmitGeneration({ 'prepare-only': false })).toBe(true);
    });
});

describe('tiktok-symphony prepared composer inspection', () => {
    it('reports what the composer currently holds', async () => {
        const expected = {
            referenceCount: 4,
            referenceSrcs: ['https://p19-creative-tool-sg.ibyteimg.com/a~tplv.image'],
            editorText: 'a prompt',
            editorHTML: '<p>a prompt</p>',
        };
        const page = { evaluate: async () => expected };
        await expect(inspectPreparedComposer(page)).resolves.toEqual(expected);
    });

    it('never types into the editor', async () => {
        // The preview is only worth having if it shows the exact prompt a real
        // run would send. An earlier version typed '@' here to probe the asset
        // suggestion panel and silently corrupted the prompt it was previewing.
        const calls = [];
        const page = new Proxy({ evaluate: async () => ({}) }, {
            get(target, prop) {
                if (prop !== 'evaluate') calls.push(String(prop));
                return target[prop];
            },
        });
        await inspectPreparedComposer(page);
        expect(calls).toEqual([]);
    });
});

describe('tiktok-symphony protocol capture sanitizing', () => {
    it('keeps API structure while dropping headers and sensitive values', () => {
        const rows = summarizeProtocolCapture([{
            method: 'POST',
            url: 'https://ads.tiktok.com/creative_bff_i18n/api/cue/generate-task?aid=1&signature=secret',
            requestHeaders: {
                authorization: 'Bearer secret',
                'content-type': 'application/json',
            },
            requestBodyPreview: JSON.stringify({
                prompt: 'hello',
                asset_id: 'asset-1',
                access_token: 'secret',
                accountId: 'private',
            }),
            requestBodyFullSize: 100,
            responseStatus: 200,
            responseHeaders: { 'content-type': 'application/json' },
            responsePreview: JSON.stringify({ code: 0, task_id: 'task-1', session_token: 'secret' }),
        }]);

        expect(rows).toEqual([{
            method: 'POST',
            url: 'https://ads.tiktok.com/creative_bff_i18n/api/cue/generate-task?aid=<redacted>&signature=<redacted>',
            requestHeaderNames: ['content-type'],
            request: {
                prompt: 'hello',
                asset_id: 'asset-1',
                access_token: '<redacted>',
                accountId: '<redacted>',
            },
            requestBytes: 100,
            requestTruncated: false,
            responseStatus: 200,
            response: { code: 0, task_id: 'task-1', session_token: '<redacted>' },
            responseBytes: null,
            responseTruncated: false,
        }]);
        expect(JSON.stringify(rows)).not.toContain('Bearer secret');
    });

    it('never emits multipart file contents', () => {
        const rows = summarizeProtocolCapture([{
            method: 'PUT',
            url: 'https://upload.byteoversea.com/file',
            requestHeaders: { 'content-type': 'multipart/form-data; boundary=x' },
            requestBodyPreview: 'raw-image-bytes',
        }]);
        expect(rows[0].request).toContain('binary body omitted');
        expect(JSON.stringify(rows)).not.toContain('raw-image-bytes');
    });

    it('drops telemetry and redacts temporary upload credentials', () => {
        const rows = summarizeProtocolCapture([
            {
                method: 'POST',
                url: 'https://sgali-mcs.byteoversea.com/list',
                requestBodyPreview: '{"user_unique_id":"private"}',
            },
            {
                method: 'POST',
                url: 'https://ads.tiktok.com/creative_bff_i18n/api/cue/upload/token',
                responseHeaders: { 'content-type': 'application/json' },
                responsePreview: JSON.stringify({
                    code: 0,
                    data: {
                        AccessKeyId: 'private-key-id',
                        SecretAccessKey: 'private-secret',
                        SessionToken: 'private-token',
                    },
                }),
            },
        ]);
        expect(rows).toHaveLength(1);
        expect(rows[0].response.data).toEqual({
            AccessKeyId: '<redacted>',
            SecretAccessKey: '<redacted>',
            SessionToken: '<redacted>',
        });
        expect(JSON.stringify(rows)).not.toContain('private');
    });

    it('lists candidate write endpoints without bodies or telemetry', () => {
        const endpoints = summarizeProtocolEndpoints([
            {
                method: 'POST',
                url: 'https://ads.tiktok.com/creative_bff_i18n/api/cue/mystery-create?aid=1',
                requestBodyPreview: 'do-not-log',
            },
            {
                method: 'POST',
                url: 'https://sgali-mcs.byteoversea.com/list',
                requestBodyPreview: 'telemetry',
            },
        ]);
        expect(endpoints).toEqual([
            'POST https://ads.tiktok.com/creative_bff_i18n/api/cue/mystery-create?aid=<redacted>',
        ]);
        expect(JSON.stringify(endpoints)).not.toContain('do-not-log');
    });
});

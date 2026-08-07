import { describe, expect, it } from 'vitest';
import { projectUrl, summarizeCapture } from './utils.js';

describe('google-flow image derivation helpers', () => {
    it('builds a project URL only for UUID-shaped ids', () => {
        expect(projectUrl('e890840e-c2ff-4705-a82f-f15d804e9872'))
            .toBe('https://labs.google/fx/zh/tools/flow/project/e890840e-c2ff-4705-a82f-f15d804e9872');
        expect(() => projectUrl('not-a-project')).toThrow(/project/);
    });

    it('sanitizes protocol capture down to endpoint metadata', () => {
        expect(summarizeCapture([{
            method: 'POST',
            url: 'https://labs.google/fx/api/trpc/flow.generate?batch=1&token=secret',
            postData: '{"input":{"prompt":"private"}}',
            requestHeaders: { authorization: 'secret' },
        }])).toEqual([{
            method: 'POST',
            url: 'https://labs.google/fx/api/trpc/flow.generate',
            status: null,
            bodyKeys: [],
            bodyBytes: 30,
        }]);
    });
});

import { mkdtemp, rm, utimes, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { validateOfficialDownloadUrl, waitForFreshFileInDir } from './download.js';

const dirs = [];

afterEach(async () => {
    await Promise.all(dirs.splice(0).map((dir) => rm(dir, { recursive: true, force: true })));
});

describe('tiktok-symphony fresh download recovery', () => {
    it('ignores old and in-progress files and returns only the new completed file', async () => {
        const dir = await mkdtemp(join(tmpdir(), 'opencli-download-test-'));
        dirs.push(dir);
        const oldFile = join(dir, 'old.mp4');
        await writeFile(oldFile, 'old-video');
        const oldTime = new Date(Date.now() - 10_000);
        await utimes(oldFile, oldTime, oldTime);
        await writeFile(join(dir, 'clip.mp4.crdownload'), 'partial');

        const startedAt = Date.now();
        const pending = waitForFreshFileInDir(dir, startedAt, 3000);
        await writeFile(join(dir, 'clip.mp4'), 'complete-video');

        await expect(pending).resolves.toMatchObject({
            downloaded: true,
            filename: join(dir, 'clip.mp4'),
            mime: 'video/mp4',
            state: 'complete',
        });
    });
});

describe('tiktok-symphony official download identity', () => {
    it('accepts only an HTTPS URL carrying the requested vid', () => {
        const url = 'https://cdn.example/video.mp4?vid=asset123';
        expect(validateOfficialDownloadUrl('asset123', url)).toBe(url);
        expect(() => validateOfficialDownloadUrl('asset123', 'https://cdn.example/video.mp4?vid=other'))
            .toThrow(/identity mismatch/);
        expect(() => validateOfficialDownloadUrl('asset123', 'http://cdn.example/video.mp4?vid=asset123'))
            .toThrow(/identity mismatch/);
    });
});

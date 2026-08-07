import { describe, expect, it } from 'vitest';
import { JSDOM } from 'jsdom';
import { SCRAPE_SRC } from './jobs.js';

describe('tiktok-symphony empty Create feed', () => {
    it('does not claim the feed is empty before the SPA has hydrated', () => {
        const dom = new JSDOM('<!doctype html><body></body>', {
            runScripts: 'outside-only',
        });

        expect(dom.window.eval(SCRAPE_SRC)).toBeNull();
    });
});

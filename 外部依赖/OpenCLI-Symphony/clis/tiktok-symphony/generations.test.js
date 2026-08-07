import { describe, expect, it } from 'vitest';
import { JSDOM } from 'jsdom';
import { SCRAPE_SRC } from './generations.js';

describe('tiktok-symphony empty Library', () => {
    it('does not claim the Library is empty before the SPA has hydrated', () => {
        const dom = new JSDOM('<!doctype html><body><h1>Library</h1></body>', {
            runScripts: 'outside-only',
        });

        expect(dom.window.eval(SCRAPE_SRC)).toBeNull();
    });
});

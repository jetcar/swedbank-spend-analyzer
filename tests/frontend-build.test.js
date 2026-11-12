import { afterEach, expect, test } from 'vitest';
import { appendFile, cp, mkdtemp, readFile, readdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { buildFrontend } from '../scripts/build-frontend.mjs';

let directory;
afterEach(async () => { if (directory) await rm(directory, {recursive:true, force:true}); });

test('builds hashed assets and rewrites HTML and shared module references', async () => {
  directory = await mkdtemp(join(tmpdir(), 'finance-build-'));
  const source = join(directory, 'source'), output = join(directory, 'output');
  await cp(new URL('../app/static/', import.meta.url), source, {recursive:true});
  const first = await buildFrontend(source, output);
  const files = await readdir(join(output, 'assets'));
  expect(files.every(name => /-[A-Za-z0-9_-]{8,}\.(js|css|svg)$/.test(name))).toBe(true);
  for (const page of ['index.html', 'login.html']) {
    const html = await readFile(join(output, page), 'utf8');
    for (const [, url] of html.matchAll(/(?:src|href)="(\/static\/[^\"]+)"/g)) {
      expect(files).toContain(url.replace('/static/assets/',''));
    }
    expect(html).toContain(`<script src="${first['theme.js']}"></script>`);
  }
  for (const file of files.filter(name => name.endsWith('.js'))) {
    const js = await readFile(join(output, 'assets', file), 'utf8');
    for (const [, asset] of js.matchAll(/"\/static\/assets\/([^\"]+)"/g)) expect(files).toContain(asset);
    expect(js).not.toMatch(/\/static\/(?:i18n|finance-domain|expense-domain)\.js/);
  }
  expect(await buildFrontend(source, output)).toEqual(first);
  await appendFile(join(source, 'i18n.js'), '\nconsole.info("changed dependency");\n');
  const changed = await buildFrontend(source, output);
  expect(changed['app.js']).not.toBe(first['app.js']);
  expect(changed['login.js']).not.toBe(first['login.js']);
  expect(changed['theme.js']).toBe(first['theme.js']);
  expect(changed['favicon.svg']).toBe(first['favicon.svg']);
});

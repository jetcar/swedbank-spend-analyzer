import { readdirSync, readFileSync } from 'node:fs';
import { describe, expect, test } from 'vitest';

const directory = new URL('../app/static/', import.meta.url);
const sources = new Map(readdirSync(directory).filter(name => name.endsWith('.js'))
  .map(name => [name, readFileSync(new URL(name, directory), 'utf8')]));
const dependencies = new Map([...sources].map(([name, source]) => [name,
  [...source.matchAll(/\b(?:from|import)\s+['"](?:\.\/|\/static\/)([^'"]+)['"]/g)].map(match => match[1]),
]));

describe('frontend module boundaries', () => {
  test('all local imports resolve without cycles or dependencies on the entry point', () => {
    const complete = new Set();
    function visit(name, path = []) {
      expect(path, `Import cycle: ${[...path, name].join(' -> ')}`).not.toContain(name);
      if (complete.has(name)) return;
      for (const dependency of dependencies.get(name)) {
        expect(sources.has(dependency), `${name} imports missing ${dependency}`).toBe(true);
        expect(dependency).not.toBe('app.js');
        visit(dependency, [...path, name]);
      }
      complete.add(name);
    }
    for (const name of sources.keys()) visit(name);
  });

  test('domain, HTTP client and loader are independent of views and the DOM', () => {
    for (const name of ['expense-domain.js', 'finance-domain.js', 'api-client.js', 'dashboard-state.js']) {
      expect(sources.get(name), name).not.toMatch(/\b(document|window)\b/);
      expect(dependencies.get(name).every(dependency => dependency.endsWith('-domain.js')), name).toBe(true);
    }
  });

  test('feature views do not import each other', () => {
    for (const [name, imports] of dependencies) {
      if (!name.endsWith('-view.js') && name !== 'split-editor.js') continue;
      expect(imports.filter(dependency => dependency.endsWith('-view.js') || dependency === 'split-editor.js'), name).toEqual([]);
    }
  });
});

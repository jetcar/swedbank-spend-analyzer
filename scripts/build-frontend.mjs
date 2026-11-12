import { build } from 'esbuild';
import { createHash } from 'node:crypto';
import { mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { basename, extname, join, relative, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const defaultOutput = join(root, 'frontend-dist');

export async function buildFrontend(sourceDir = join(root, 'app/static'), outputDir = defaultOutput) {
  sourceDir = resolve(sourceDir);
  outputDir = resolve(outputDir);
  await mkdir(join(outputDir, 'assets'), {recursive:true});
  const pages = new Map();
  for (const name of (await readdir(sourceDir)).filter(name => name.endsWith('.html')).sort()) {
    pages.set(name, await readFile(join(sourceDir, name), 'utf8'));
  }
  const references = [...new Set([...pages.values()].flatMap(html =>
    [...html.matchAll(/(?:src|href)="\/static\/([^"?#]+)"/g)].map(match => match[1])))].sort();
  const manifest = {};
  // esbuild hashes entry points and their shared dependencies, rewriting chunk imports.
  // https://esbuild.github.io/api/#entry-names
  const options = {
    absWorkingDir:sourceDir, outdir:join(outputDir, 'assets'), bundle:true,
    entryNames:'[name]-[hash]', chunkNames:'chunk-[hash]', assetNames:'[name]-[hash]',
    minify:true, metafile:true, target:'es2022', publicPath:'/static/assets',
    plugins:[{name:'static-imports', setup(builder) {
      builder.onResolve({filter:/^\/static\//}, args => ({path:join(sourceDir, args.path.slice('/static/'.length))}));
    }}],
  };
  const results = await Promise.all([
    build({...options, entryPoints:references.filter(name => /\.(js|css)$/.test(name) && name !== 'theme.js'), format:'esm', splitting:true}),
    // Keep theme initialization synchronous and external for the strict CSP.
    build({...options, entryPoints:['theme.js'], format:'iife'}),
  ]);
  for (const result of results) {
    for (const [path, metadata] of Object.entries(result.metafile.outputs)) {
      if (metadata.entryPoint) {
        manifest[relative(sourceDir, resolve(sourceDir, metadata.entryPoint)).replaceAll('\\','/')] =
          '/static/' + relative(outputDir, resolve(sourceDir, path)).replaceAll('\\','/');
      }
    }
  }
  for (const name of references.filter(name => !/\.(html|js|css)$/.test(name))) {
    const content = await readFile(join(sourceDir, name));
    const hash = createHash('sha256').update(content).digest('hex').slice(0,16);
    const output = `${basename(name, extname(name))}-${hash}${extname(name)}`;
    await writeFile(join(outputDir, 'assets', output), content);
    manifest[name] = `/static/assets/${output}`;
  }
  for (const [name, html] of pages) {
    const rewritten = html.replace(/((?:src|href)=")\/static\/([^"?#]+)(")/g, (match, prefix, asset, suffix) => {
      if (asset.endsWith('.html')) return match;
      if (!manifest[asset]) throw new Error(`Unbuilt frontend asset: ${asset}`);
      return prefix + manifest[asset] + suffix;
    });
    await writeFile(join(outputDir, name), rewritten);
  }
  await writeFile(join(outputDir, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
  return manifest;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  // This fixed build directory is inside the repository and never contains sources.
  await rm(defaultOutput, {recursive:true, force:true});
  const manifest = await buildFrontend();
  console.log(`Built ${Object.keys(manifest).length} fingerprinted frontend entries.`);
}

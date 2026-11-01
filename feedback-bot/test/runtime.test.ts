import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import { webcrypto } from 'node:crypto';
import { createContext, SourceTextModule, SyntheticModule, type Module } from 'node:vm';
import { fixture, message, OWNER, USER } from './helpers.js';

test('built production handlers execute without Node/browser/timer globals; maintenance is owner-only', async (t) => {
  const f = fixture(); t.after(() => f.db.close());
  const context = createContext({
    console: { log() {}, error() {}, warn() {}, info() {}, debug() {}, trace() {} },
    TextEncoder, TextDecoder, crypto: webcrypto,
  });
  const sdk = new SyntheticModule(['api', 'db', 'BotApiError', 'InputFile', 'EndpointError'], function () {
    this.setExport('api', new Proxy({}, { get: (_target, name) => {
      if (name === 'getMe') return async () => f.bot.botInfo;
      return (...args: unknown[]) => (f.bot.api.raw as any)[name](...args);
    } }));
    this.setExport('db', f.sql);
    this.setExport('BotApiError', class extends Error {});
    this.setExport('InputFile', class {});
    this.setExport('EndpointError', class extends Error {});
  }, { context });
  const dbSdk = new SyntheticModule(['eq'], function () { this.setExport('eq', () => {}); }, { context });
  const modules = new Map<string, Module>();
  const load = async (url: URL): Promise<SourceTextModule> => {
    const cached = modules.get(url.href);
    if (cached) return cached as SourceTextModule;
    const module = new SourceTextModule(await readFile(url, 'utf8'), { context, identifier: url.href });
    modules.set(url.href, module);
    return module;
  };
  const resolve = async (specifier: string, parent: Module) => {
    if (specifier === 'sdk') return sdk;
    if (specifier === 'sdk/db') return dbSdk;
    assert.ok(specifier.startsWith('.'), `Unexpected runtime import: ${specifier}`);
    return load(new URL(specifier, parent.identifier));
  };
  const handler = await load(new URL('../tgcloud/handlers/message.js', import.meta.url));
  await handler.link(resolve);
  await handler.evaluate();
  const update = message();
  await (handler.namespace as any).default(update.message, { update });
  assert.ok((await f.store.get(`${USER}:1`))?.copy_id);
  const callback = await load(new URL('../tgcloud/handlers/callback_query.js', import.meta.url));
  await callback.link(resolve);
  await callback.evaluate();
  assert.equal(typeof (callback.namespace as any).default, 'function');

  const cleanup = await load(new URL('../tgcloud/endpoints/cleanup.js', import.meta.url));
  await cleanup.link(resolve);
  await cleanup.evaluate();
  await assert.rejects((cleanup.namespace as any).default({}, { initData: { user: { id: USER } } }), /Not allowed/);
  f.db.exec('UPDATE feedback SET created_at = 1');
  await (cleanup.namespace as any).default({}, { initData: { user: { id: OWNER } } });
  assert.equal(await f.store.get(`${USER}:1`), null);
});

const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

function authHarness(fetchRequest) {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      value: "", textContent: "", disabled: false, handlers: {},
      addEventListener(type, handler) { this.handlers[type] = handler; },
      classList: { add() {}, remove() {}, toggle() {}, contains() { return true; } },
      close() {}, showModal() {},
    });
    return elements.get(id);
  }
  const context = vm.createContext({
    document: { getElementById: element, querySelectorAll: () => [] },
    window: { location: { pathname: "/server-desk/", origin: "https://example.invalid" } },
    URL, setInterval() {},
    fetch: (url, options) => url.endsWith("api/me")
      ? Promise.resolve({ ok: true, json: async () => ({ authenticated: false }) })
      : fetchRequest(url, options),
  });
  vm.runInContext(readFileSync(join(__dirname, "../app/static/app.js"), "utf8"), context);
  return context;
}

test("expired concurrent API calls share one browser restore and retry without a password", async () => {
  let restores = 0;
  let restored = false;
  const context = authHarness(async (url) => {
    if (url.endsWith("auth/refresh")) {
      restores++;
      await Promise.resolve();
      restored = true;
      return { ok: true, json: async () => ({ ok: true }) };
    }
    return restored
      ? { ok: true, json: async () => ({ restored: true }) }
      : { ok: false, status: 401, json: async () => ({ detail: "not authenticated" }) };
  });
  await new Promise(setImmediate);
  vm.runInContext("state.deviceAuthEnabled = true", context);
  const results = await vm.runInContext('Promise.all([api("/api/servers"), api("/api/dashboard")])', context);
  assert.equal(restores, 1);
  assert.ok(results.every((r) => r.restored));
  assert.equal(vm.runInContext("reauthPending", context), null);
});

test("a wrong password does not trigger saved-browser restore", async () => {
  let requests = 0;
  const context = authHarness(async () => {
    requests++;
    return { ok: false, status: 401, json: async () => ({ detail: "invalid credentials" }) };
  });
  await new Promise(setImmediate);
  vm.runInContext("state.deviceAuthEnabled = true", context);
  await assert.rejects(vm.runInContext('api("/api/login", {method:"POST"})', context));
  assert.equal(requests, 1);
});

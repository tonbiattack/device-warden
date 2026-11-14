// Test the plugin's real tool executor without starting an OpenCode session or a device.
import assert from "node:assert/strict"
import { readFile, mkdtemp, rm, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { stripTypeScriptTypes } from "node:module"

const directory = await mkdtemp(join(tmpdir(), "device-warden-plugin-"))
const timers = new Map()
const realSetInterval = globalThis.setInterval
const realClearInterval = globalThis.clearInterval
try {
  await writeFile(join(directory, "broker.token"), "test-token")
  process.env.DEVICE_WARDEN_HOME = directory

  // Replace timers so the heartbeat schedule is observable and does not wait 30s.
  globalThis.setInterval = (callback, delay) => {
    const id = Symbol(delay)
    timers.set(id, { callback, delay })
    return id
  }
  globalThis.clearInterval = (id) => timers.delete(id)

  const source = (await readFile(new URL("./index.ts", import.meta.url), "utf8"))
    .replace('import { Plugin } from "@opencode/plugin"', "const Plugin = { define: value => value }")
  const code = stripTypeScriptTypes(source, { mode: "strip" })
  const { default: plugin } = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`)

  let tool
  const ctx = {
    location: { directory: "C:/work", project: { canonical: "C:/project" } },
    session: { get: async ({ sessionID }) => ({ title: `Title ${sessionID}` }) },
    tool: { transform: async (apply) => apply({ add: (definition) => { tool = definition } }) },
  }
  const cleanup = await plugin.setup(ctx)

  const requests = []
  globalThis.fetch = async (url, options) => {
    const body = JSON.parse(options.body)
    const action = String(url).split("/").pop()
    requests.push({ url, action, body, auth: options.headers.Authorization })
    const result = action === "claim" ? { lease_id: `lease-${body.session_id}` }
      : action === "heartbeat" ? { exit_code: 0 } : null
    return { ok: true, json: async () => ({ result }) }
  }

  for (const sessionID of ["session-one", "session-two"]) {
    await tool.execute({ action: "claim" }, { sessionID })
  }
  assert.deepEqual(requests.map((r) => r.body.session_id), ["session-one", "session-two"])
  assert.equal(requests[0].body.title, "Title session-one")
  assert.equal(requests[0].body.project, "C:/project")
  assert.equal(requests[0].body.tool, "OpenCode")
  assert.equal(requests[0].auth, "Bearer test-token")
  assert.equal(timers.size, 2, "each claim should start a heartbeat")

  // A heartbeat keeps the lease alive without an explicit tool call.
  const [firstTimer] = timers.values()
  assert.equal(firstTimer.delay, 30_000)
  firstTimer.callback()
  await new Promise((resolve) => setTimeout(resolve, 0))
  assert.ok(requests.some((r) => r.action === "heartbeat" && r.body.lease_id === "lease-session-one"))

  // Releasing one lease stops only that lease's heartbeat.
  await tool.execute({ action: "release", lease_id: "lease-session-one" }, { sessionID: "session-one" })
  assert.equal(timers.size, 1)
  await tool.execute({ action: "release", lease_id: "lease-session-two" }, { sessionID: "session-two" })
  assert.equal(timers.size, 0)

  // Plugin unload clears any remaining timers.
  await tool.execute({ action: "claim" }, { sessionID: "session-three" })
  assert.equal(timers.size, 1)
  cleanup()
  assert.equal(timers.size, 0)

  console.log("OpenCode plugin executor: OK")
} finally {
  globalThis.setInterval = realSetInterval
  globalThis.clearInterval = realClearInterval
  await rm(directory, { recursive: true, force: true })
}

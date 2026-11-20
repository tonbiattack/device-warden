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
    const result = action === "claim" ? { lease_id: `lease-${body.session_id}`, device_id: `device-${body.session_id}` }
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
  // Await the callback: it reads the token file before fetching, so a single
  // timer tick is not enough on a slow filesystem (this failed on Windows CI).
  await firstTimer.callback()
  assert.ok(requests.some((r) => r.action === "heartbeat" && r.body.lease_id === "lease-session-one"))

  // Releasing one lease stops only that lease's heartbeat.
  await tool.execute({ action: "release", lease_id: "lease-session-one" }, { sessionID: "session-one" })
  assert.equal(timers.size, 1)
  await tool.execute({ action: "release", lease_id: "lease-session-two" }, { sessionID: "session-two" })
  assert.equal(timers.size, 0)

  // A failed heartbeat must stop ADB and new claims in that session, but not
  // affect another session. An explicit release is still possible.
  for (const sessionID of ["failure", "healthy"]) {
    await tool.execute({ action: "claim" }, { sessionID })
  }
  const [failureTimer] = timers.values()
  const healthyFetch = globalThis.fetch
  globalThis.fetch = async (url, options) => {
    if (String(url).endsWith("/heartbeat")) throw new Error("connection refused")
    return healthyFetch(url, options)
  }
  await failureTimer.callback()
  await assert.rejects(tool.execute({ action: "adb", lease_id: "lease-failure", args: ["shell", "id"] },
    { sessionID: "failure" }), /heartbeat失敗/)
  await assert.rejects(tool.execute({ action: "claim" }, { sessionID: "failure" }), /heartbeat失敗/)
  await tool.execute({ action: "list" }, { sessionID: "healthy" })
  globalThis.fetch = healthyFetch
  await tool.execute({ action: "release", lease_id: "lease-failure" }, { sessionID: "failure" })
  await tool.execute({ action: "release", lease_id: "lease-healthy" }, { sessionID: "healthy" })
  assert.equal(timers.size, 0)

  for (const status of [401, 409]) {
    const sessionID = `error-${status}`
    await tool.execute({ action: "claim" }, { sessionID })
    const [timer] = timers.values()
    globalThis.fetch = async (url, options) => String(url).endsWith("/heartbeat")
      ? { ok: false, status, json: async () => ({ error: "failed" }) }
      : healthyFetch(url, options)
    await timer.callback()
    // `list` is allowed while stalled so the session can find its device; a
    // write action must still fail.
    await tool.execute({ action: "list" }, { sessionID })
    await assert.rejects(tool.execute({ action: "adb", lease_id: `lease-${sessionID}`, args: ["shell", "id"] },
      { sessionID }), /heartbeat失敗/)
    globalThis.fetch = healthyFetch
    await tool.execute({ action: "release", lease_id: `lease-${sessionID}` }, { sessionID })
  }

  // A session must not release or drive another session's lease.
  await tool.execute({ action: "claim" }, { sessionID: "owner" })
  await assert.rejects(
    tool.execute({ action: "release", lease_id: "lease-owner" }, { sessionID: "intruder" }),
    /別のセッション/)
  await assert.rejects(
    tool.execute({ action: "adb", lease_id: "lease-owner", args: ["shell", "id"] }, { sessionID: "intruder" }),
    /別のセッション/)
  assert.equal(timers.size, 1, "a rejected release must leave the owner's heartbeat alone")
  await tool.execute({ action: "release", lease_id: "lease-owner" }, { sessionID: "owner" })
  assert.equal(timers.size, 0)

  // Recovery: a session that lost its lease ID can release by device ID after
  // `list` reports owns_lease, but another session cannot.
  await tool.execute({ action: "claim" }, { sessionID: "recovery" })
  const recoveryTimer = [...timers.values()][0]
  globalThis.fetch = async (url, options) => {
    const body = JSON.parse(options.body)
    const action = String(url).split("/").pop()
    if (action === "heartbeat") return { ok: false, status: 409, json: async () => ({ error: "stale" }) }
    if (action === "list") {
      return { ok: true, json: async () => ({ result: [
        { id: "device-recovery", owns_lease: body.session_id === "recovery" },
      ] }) }
    }
    if (action === "release") {
      requests.push({ url, action, body })
      return { ok: true, json: async () => ({ result: { device_id: body.device_id, state: "released" } }) }
    }
    return healthyFetch(url, options)
  }
  await recoveryTimer.callback()
  await tool.execute({ action: "list" }, { sessionID: "recovery" })
  // Another session's list call must not erase the device this session can release.
  await tool.execute({ action: "list" }, { sessionID: "intruder" })
  await assert.rejects(
    tool.execute({ action: "adb", lease_id: "lease-recovery", args: ["shell", "id"] }, { sessionID: "recovery" }),
    /heartbeat失敗/)
  await assert.rejects(
    tool.execute({ action: "release", device_id: "device-recovery" }, { sessionID: "intruder" }),
    /自分のリースではありません/)
  const released = JSON.parse((await tool.execute(
    { action: "release", device_id: "device-recovery" }, { sessionID: "recovery" })).content)
  assert.equal(released.device_id, "device-recovery")
  globalThis.fetch = healthyFetch
  const releaseCalls = requests.filter((r) => r.action === "release")
  const releaseCall = releaseCalls.at(-1)
  assert.equal(releaseCall.body.session_id, "recovery")
  assert.equal(releaseCall.body.device_id, "device-recovery")
  assert.equal(releaseCall.body.lease_id, undefined)
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

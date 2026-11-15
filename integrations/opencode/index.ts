import { Plugin } from "@opencode/plugin"
import { readFile } from "node:fs/promises"
import { homedir } from "node:os"
import { join } from "node:path"

class BrokerFailure extends Error {
  readonly reason: string
  constructor(reason: string) {
    super(reason)
    this.reason = reason
  }
}

async function broker(action: string, body: object) {
  const home = process.env.DEVICE_WARDEN_HOME || join(process.env.USERPROFILE || homedir(), ".device-warden")
  let token: string
  try {
    token = (await readFile(join(home, "broker.token"), "utf8")).trim()
  } catch {
    throw new BrokerFailure("Brokerトークンを読み取れません")
  }
  // ADB runs for up to a minute inside the Broker, so a shorter client timeout
  // would report success as failure and invite a duplicate operation.
  const timeout = action === "adb" ? 70_000 : 15_000
  let response: Response
  try {
    response = await fetch(`http://127.0.0.1:47631/${action}`, {
      method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify(body), signal: AbortSignal.timeout(timeout),
    })
  } catch {
    throw new BrokerFailure("Brokerに接続できないか応答がありません")
  }
  if (response.status === 401) throw new BrokerFailure("Brokerの認証に失敗しました")
  if (action === "heartbeat" && response.status === 409) {
    throw new BrokerFailure("リースが解放済みか要確認状態です")
  }
  const result = await response.json() as { result?: unknown; error?: string }
  if (!response.ok) throw new BrokerFailure(result.error || "Device Wardenの操作に失敗しました")
  return result.result
}

export default Plugin.define({
  id: "device-warden",
  async setup(ctx) {
    // Keep leases alive while the session works, and stop when the lease is released.
    const heartbeats = new Map<string, ReturnType<typeof setInterval>>()
    // Fail closed per lease and session. Do not store an exception message:
    // network errors may contain local paths or other sensitive information.
    const failures = new Map<string, { sessionID: string; at: string; reason: string }>()
    // Lease IDs are release credentials. Remember which session obtained each
    // one so a session cannot release or drive another session's device.
    const owners = new Map<string, string>()
    // A device whose heartbeat already failed, so the session can still release
    // it by device ID even though the lease ID is hidden from `list`.
    const stalled = new Map<string, string>()
    const owns = (leaseID: string, sessionID: string) => owners.get(leaseID) === sessionID
    const stop = (leaseID: string) => {
      const timer = heartbeats.get(leaseID)
      if (timer) clearInterval(timer)
      heartbeats.delete(leaseID)
    }
    const cleanup = () => {
      for (const leaseID of [...heartbeats.keys()]) stop(leaseID)
      failures.clear()
      owners.clear()
      stalled.clear()
    }
    const track = (leaseID: string, sessionID: string, deviceID?: string) => {
      stop(leaseID)
      failures.delete(leaseID)
      owners.set(leaseID, sessionID)
      if (deviceID) stalled.delete(deviceID)
      // The Broker treats 120s without a heartbeat as recovery_required.
      const timer = setInterval(async () => {
        try {
          await broker("heartbeat", { lease_id: leaseID })
        } catch (error) {
          // Ignore responses from a timer that has already been released or replaced.
          if (heartbeats.get(leaseID) !== timer) return
          stop(leaseID)
          owners.set(leaseID, sessionID)
          if (deviceID) stalled.set(deviceID, sessionID)
          failures.set(leaseID, { sessionID, at: new Date().toISOString(),
            reason: error instanceof BrokerFailure ? error.reason : "Brokerとの通信またはリースの確認に失敗しました" })
        }
      }, 30_000)
      heartbeats.set(leaseID, timer)
    }
    await ctx.tool.transform((editor) => {
      editor.add({
        name: "device_warden", description: "Manage Android device leases. Leases heartbeat automatically every 30s after claim, but still call release when done. Use the adb action for device operations instead of running adb directly.",
        input: { type: "object", properties: {
          action: { type: "string", enum: ["list", "claim", "heartbeat", "release", "adb"] },
          device_id: { type: "string" }, lease_id: { type: "string" },
          args: { type: "array", items: { type: "string" } },
          resume: { type: "boolean" },
        }, required: ["action"], additionalProperties: false },
        options: { codemode: true },
        execute: async (input, context) => {
          const { action, ...body } = input as { action: string; device_id?: string; lease_id?: string; args?: string[] }
          // Release by device ID is allowed only for a device this session held,
          // and the Broker re-checks ownership against the session ID.
          if (action === "release" && body.device_id) {
            if (stalled.get(body.device_id) !== context.sessionID) {
              throw new Error("この端末は自分のリースではありません。")
            }
            const released = await broker("release", { device_id: body.device_id, session_id: context.sessionID })
            stalled.delete(body.device_id)
            return { content: JSON.stringify(released) }
          }
          // A lease belongs to the session that claimed it. Without this check a
          // session could release or run adb against another session's device.
          if (body.lease_id && owners.has(body.lease_id) && !owns(body.lease_id, context.sessionID)) {
            throw new Error("このリースは別のセッションが保持しています。自分の端末を list で確認してください。")
          }
          const failed = [...failures.values()].find((item) => item.sessionID === context.sessionID)
          // `list` stays available: it is read-only and is how the session finds
          // the device ID and ownership needed to recover.
          if (failed && action !== "release" && action !== "list") {
            const held = [...failures.entries()]
              .filter(([, item]) => item.sessionID === context.sessionID)
              .map(([leaseID]) => leaseID).join(", ")
            const devices = [...stalled.entries()]
              .filter(([, sessionID]) => sessionID === context.sessionID)
              .map(([deviceID]) => deviceID).join(", ")
            const hint = devices
              ? `release に device_id=${devices} を渡せば自分のリースだけを解放できます。`
              : "そのリースだけをreleaseするか、管理者に確認を依頼してください。"
            throw new Error(`heartbeat失敗（${failed.at}）: ${failed.reason}。対象リース: ${held}。ADB操作や再確保は行わず、利用状況を確認してください。${hint}`)
          }
          let payload: object = body
          if (action === "claim") {
            let title: string | undefined
            try { title = (await ctx.session.get({ sessionID: context.sessionID })).title } catch { /* optional */ }
            payload = { ...body, kind: "ai", tool: "OpenCode", session_id: context.sessionID,
              title, project: ctx.location.project.canonical, workdir: ctx.location.directory }
          }
          // The Broker computes `owns_lease` for this session, so it needs the ID.
          if (action === "list") payload = { ...body, session_id: context.sessionID }
          const result = await broker(action, payload)
          if (action === "claim") {
            const claimed = result as { lease_id: string; device_id: string }
            track(claimed.lease_id, context.sessionID, claimed.device_id)
          }
          if (action === "list") {
            // `owns_lease` is computed by the Broker for this session, so it also
            // covers a lease that was interrupted before this plugin instance saw it.
            // Only touch this session's entries: another session's list call must
            // not erase the device this session can still release.
            const rows = Array.isArray(result) ? result as Array<{ id: string; owns_lease?: boolean }> : []
            for (const device of rows) {
              if (device.owns_lease) stalled.set(device.id, context.sessionID)
              else if (stalled.get(device.id) === context.sessionID) stalled.delete(device.id)
            }
          }
          if (action === "release" && body.lease_id) {
            stop(body.lease_id)
            failures.delete(body.lease_id)
          }
          return { content: JSON.stringify(result) }
        },
      })
    })
    return cleanup
  },
})

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
  let response: Response
  try {
    response = await fetch(`http://127.0.0.1:47631/${action}`, {
      method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify(body), signal: AbortSignal.timeout(15000),
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
    const stop = (leaseID: string) => {
      const timer = heartbeats.get(leaseID)
      if (timer) clearInterval(timer)
      heartbeats.delete(leaseID)
    }
    const cleanup = () => {
      for (const leaseID of [...heartbeats.keys()]) stop(leaseID)
      failures.clear()
    }
    const track = (leaseID: string, sessionID: string) => {
      stop(leaseID)
      failures.delete(leaseID)
      // The Broker treats 120s without a heartbeat as recovery_required.
      const timer = setInterval(async () => {
        try {
          await broker("heartbeat", { lease_id: leaseID })
        } catch (error) {
          // Ignore responses from a timer that has already been released or replaced.
          if (heartbeats.get(leaseID) !== timer) return
          stop(leaseID)
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
        }, required: ["action"], additionalProperties: false },
        options: { codemode: true },
        execute: async (input, context) => {
          const { action, ...body } = input as { action: string; device_id?: string; lease_id?: string; args?: string[] }
          const failed = [...failures.values()].find((item) => item.sessionID === context.sessionID)
          if (failed && action !== "release") {
            throw new Error(`heartbeat失敗（${failed.at}）: ${failed.reason}。ADB操作や再確保は行わず、利用状況を確認してください。明示的にreleaseするか、管理者に確認を依頼してください。`)
          }
          let payload: object = body
          if (action === "claim") {
            let title: string | undefined
            try { title = (await ctx.session.get({ sessionID: context.sessionID })).title } catch { /* optional */ }
            payload = { ...body, kind: "ai", tool: "OpenCode", session_id: context.sessionID,
              title, project: ctx.location.project.canonical, workdir: ctx.location.directory }
          }
          const result = await broker(action, payload)
          if (action === "claim") track((result as { lease_id: string }).lease_id, context.sessionID)
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

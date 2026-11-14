import { Plugin } from "@opencode/plugin"
import { readFile } from "node:fs/promises"
import { homedir } from "node:os"
import { join } from "node:path"

async function broker(action: string, body: object) {
  const home = process.env.DEVICE_WARDEN_HOME || join(process.env.USERPROFILE || homedir(), ".device-warden")
  let token: string
  try {
    token = (await readFile(join(home, "broker.token"), "utf8")).trim()
  } catch {
    throw new Error("Device Warden Brokerに接続できません。device-warden-brokerを起動してください。")
  }
  const response = await fetch(`http://127.0.0.1:47631/${action}`, {
    method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  const result = await response.json() as { result?: unknown; error?: string }
  if (!response.ok) throw new Error(result.error || "Device Warden request failed")
  return result.result
}

export default Plugin.define({
  id: "device-warden",
  async setup(ctx) {
    // Keep leases alive while the session works, and stop when the lease is released.
    const heartbeats = new Map<string, ReturnType<typeof setInterval>>()
    const stop = (leaseID: string) => {
      const timer = heartbeats.get(leaseID)
      if (timer) clearInterval(timer)
      heartbeats.delete(leaseID)
    }
    const cleanup = () => { for (const leaseID of [...heartbeats.keys()]) stop(leaseID) }
    const track = (leaseID: string) => {
      stop(leaseID)
      // The Broker treats 120s without a heartbeat as recovery_required.
      heartbeats.set(leaseID, setInterval(() => {
        broker("heartbeat", { lease_id: leaseID }).catch(() => {
          // A failed heartbeat must stay visible to the AI instead of silently retrying forever.
          stop(leaseID)
        })
      }, 30_000))
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
          let payload: object = body
          if (action === "claim") {
            let title: string | undefined
            try { title = (await ctx.session.get({ sessionID: context.sessionID })).title } catch { /* optional */ }
            payload = { ...body, kind: "ai", tool: "OpenCode", session_id: context.sessionID,
              title, project: ctx.location.project.canonical, workdir: ctx.location.directory }
          }
          const result = await broker(action, payload)
          if (action === "claim") track((result as { lease_id: string }).lease_id)
          if (action === "release") stop(payload.lease_id as string)
          return { content: JSON.stringify(result) }
        },
      })
    })
    return cleanup
  },
})

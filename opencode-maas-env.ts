import { readFile } from "node:fs/promises"
import { parseEnv } from "node:util"

// Config variable substitution happens before plugins load. Set the provider's
// API key in the config hook so ordinary `opencode` also reads the project .env.
export const MaasEnv = async ({ directory }) => ({
  config: async (config) => {
    const provider = config.provider?.["huawei-maas"]
    if (!provider) return

    const env = parseEnv(await readFile(`${directory}/.env`, "utf8"))
    const apiKey = env.MAAS_API_KEY
    if (!apiKey || apiKey !== apiKey.trim() || /[\r\n]/.test(apiKey)) {
      throw new Error("Set a nonempty MAAS_API_KEY without surrounding whitespace in the project .env")
    }

    provider.options ??= {}
    provider.options.apiKey = apiKey
  },
})

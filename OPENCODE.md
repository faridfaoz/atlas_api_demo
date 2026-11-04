OpenCode is configured to use Huawei Cloud MaaS `glm-5.3` for this project.

Start it from this project directory:

```bash
opencode
```

You can also pass OpenCode commands:

```bash
./opencode-maas models huawei-maas
./opencode-maas run "Reply with OK."
```

The local plugin `opencode-maas-env.ts` reads `MAAS_API_KEY` from the
project `.env` at startup and sets the provider API key. It requires no external
packages. The public Hong Kong MaaS URL is set explicitly
in `opencode.json` and uses the `/v2` base path; OpenCode appends
`/chat/completions`.

The `./opencode-maas` launcher is also available; it exports shell-compatible
`.env` assignments before starting OpenCode and works from any directory.
Restart existing OpenCode sessions after changing this configuration or `.env`.

`opencode.json` selects GLM-5.3 explicitly. The existing `MAAS_MODEL` variable
is available to other applications and does not override this selection.
The API key stays in `.env` and is loaded into memory at startup.

References: [OpenCode providers](https://docs.opencode.ai/docs/providers/),
[Huawei Cloud MaaS quickstart](https://support.huaweicloud.com/intl/en-us/qs-maas/qs-maas-0001.html).

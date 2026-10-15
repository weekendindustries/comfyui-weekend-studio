# Weekend Studio for ComfyUI

Use your own ComfyUI workflows as generators in Weekend Studio.

1. Install **Weekend Studio** from ComfyUI Manager and restart ComfyUI.
2. Open the **Weekend Studio** tab in ComfyUI and click **Connect**.
3. Tick **Show in Weekend Studio** on the workflows you want. They appear in Weekend Studio's
   generator list, with the settings you picked.

Three nodes connect a workflow to your document:

- **Weekend Image In**: the generation area, active layer or whole canvas.
- **Weekend Mask In**: the selection. White = the part to change.
- **Weekend Output**: the result, which becomes a new layer. An optional mask sets its
  transparency (white = visible).

Each input node has an optional `fallback`, so you can build and test the workflow in ComfyUI.

Your workflows stay on your machine. Weekend Studio sends only the settings you exposed, and your
ComfyUI checks every value before it runs anything.

## Testing without the app

The node adds these routes to ComfyUI (same-origin only):

- `GET /weekend-studio/status`
- `POST /weekend-studio/connect`, then open the `approveUrl` it returns
- `POST /weekend-studio/disconnect`
- `GET` / `POST /weekend-studio/workflows` (publish takes `{name, prompt}`, where `prompt` is the
  API-format export)
- `POST /weekend-studio/test-run` (`{workflowId, values, images: {key: base64}}`)

`weekend_studio/service.json` says where Weekend Studio is. Generate it with
`node tools/write-service-json.mjs`. `WEEKEND_STUDIO_FUNCTIONS_URL`, `WEEKEND_STUDIO_API_KEY` and
`WEEKEND_STUDIO_APP_URL` override it.

## Publishing to the Comfy Registry

Publisher `weekendindustries`; source at github.com/weekendindustries/comfyui-weekend-studio.

1. `node tools/write-service-json.mjs` writes the production `weekend_studio/service.json`.
2. `node tools/sync-fixtures.mjs` copies the shared test cases in (from the Weekend Studio repo).
3. `node tools/release-check.mjs` must say "Ready".
4. Bump `version` in `pyproject.toml` and `VERSION` in `weekend_studio/routes.py` together, then push to
   `main`. The GitHub Action (`.github/workflows/publish_action.yml`) publishes to the Registry
   whenever `pyproject.toml` changes, using the repository secret `REGISTRY_ACCESS_TOKEN`. By hand:
   `comfy node publish` from this folder.

`.comfyignore` leaves `tests/`, `tools/` and `.github/` out of the published package.

## Development

    python3 -m unittest discover -s tests -t .

The shared test cases come from `packages/comfy/fixtures/` in the Weekend Studio repository; `tests/fixtures/` is
the synced copy, so the tests also run here on their own.

## License

MIT, copyright Weekend Industries. See `LICENSE`.

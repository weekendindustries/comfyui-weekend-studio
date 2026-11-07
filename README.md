# Weekend Studio for ComfyUI

Use your own ComfyUI workflows as generators in Weekend Studio.

1. Install **Weekend Studio** from ComfyUI Manager and restart ComfyUI.
2. Open the **Weekend Studio** tab in ComfyUI, name this computer, and click **Connect**. Enter the
   code it shows at **weekendstudio.app/#comfy**, on any device you're signed in on (on this
   computer, clicking that address fills the code in). Check the button names this computer, and
   click it.
3. Tick **Show in Weekend Studio** on the workflows you want. They appear in the **My ComfyUI** panel
   of every Weekend Studio app that can use them, with the settings you picked: a workflow that makes
   a picture from a prompt or from another picture shows in Images, Moods, Doc, Sketchbook and Animate;
   one that needs a mask or the whole canvas shows only in Images. Under **Show in**, untick an app to keep a workflow out of it.

On the same computer you can skip the account: in the tab, turn on **Use from a browser on this
computer, without an account**. It's off until you turn it on.

These nodes connect a workflow to your document:

- **Weekend Image In**: the generation area, active layer, whole canvas, or a reference picture.
  Its `provided` output is yes when Weekend Studio sent the picture. A reference is optional, so
  connect `provided` to a Switch node to leave it out when none is sent.
- **Weekend Mask In**: the selection. White = the part to change.
- **Weekend Output**: the result, which becomes a new layer. An optional mask sets its
  transparency (white = visible).
- **Weekend Is Empty**: yes when a mask is empty. Use it with a Switch node.

Each input node has an optional `fallback`, so you can build and test the workflow in ComfyUI.

## Starter workflow

**Flux3 Klein Edit** is in ComfyUI's **Templates**, under Extensions › comfyui-weekend-studio. It
edits the area with up to three reference pictures. On an empty area it makes a new picture from
the prompt and the references. It shows Prompt, Output megapixels, Seed and Steps. Open it, then in
the Weekend Studio tab choose **Use here and show it in Weekend Studio**.

It uses FLUX 3 Image Klein from Black Forest Labs (about 11 GB). ComfyUI offers the three files when
you open the template. They're gated: sign in to Hugging Face and accept the FLUX Kommunity License
on [the model page](https://huggingface.co/black-forest-labs/flux-3-image-klein-comfy) first. That
license is between you and Black Forest Labs. Read it before using what you make commercially.

Weekend Studio never sends your ComfyUI a workflow. It sends a workflow id and the values of the
settings you chose to show, and your ComfyUI checks every value before it runs anything. Settings
that look like a file path, a web address or code can't be shown at all.

## Network and data

Exactly what this node sends, where, and when. The same list is in the tab, under **Network and
data**.

**Until you click Connect, nothing leaves this computer.** Importing the node only adds the tab's
routes to ComfyUI's own server. It starts no other server and opens no outbound connection.

| When | Where to | What |
|---|---|---|
| You click **Connect** | Weekend Studio's functions (`<project>.supabase.co/functions/v1/comfy-pair`) | The name you typed for this computer. You get back a one-time code and a link, which opens in your browser for you to approve. The node then asks every few seconds until you approve or the code expires (10 minutes). |
| Approved | the same | A machine token comes back. It's kept in this node's own folder (below), readable by your user only. |
| Each start, and every 10 minutes while connected | `…/functions/v1/comfy-token` | The machine token, swapped for a short-lived pass onto the relay. |
| While connected | `wss://comfy.weekendindustries.app/hub` (one outbound WebSocket) | For every workflow you show: its name, the settings you picked (labels, groups, limits and current values, **prompt text included**), dropdown lists (for a model or LoRA setting, **the file names of your installed models**), node titles, and the names of nodes that aren't installed. Also this node's version and whether it's busy. Sent on connect and after each change. |
| During a run | the relay, and `https://comfy.weekendindustries.app/blob/…` | Progress, and errors in words. Paths under your home folder are shown as `~`. The result pictures, with the workflow and other metadata (PNG text, EXIF, XMP) removed. Input pictures come the other way. |

Never sent: the workflow graph, workflows you didn't show, your files and models themselves, or
this computer's own name (unless you typed it as the name).

**What Weekend Studio keeps:**

- the computer's name, with your account, until you remove it (Account → ComfyUI machines);
- the list of shown workflows above, on the relay, until you remove the computer or 30 days
  after it last connected;
- pictures of a run, until your browser has them (at most a day).

**Same-computer mode** (off until you turn it on): a small server on `127.0.0.1:8189` (8189–8193)
that answers only Weekend Studio's website, only from this computer: the Host must be
`127.0.0.1`/`localhost`, the Origin must be `https://weekendstudio.app` (or its earlier address
`https://weekendindustries.app`, or `https://studio.weekendindustries.dev`), and a run must be JSON, which makes the browser ask first.
It answers Chrome's local-network check (`Access-Control-Allow-Private-Network`) for those
sites only. No picture, prompt or setting leaves the computer in this mode; pictures go straight between the
browser and ComfyUI. If you're signed in, Weekend Studio records that a ComfyUI run was made for the
document you're in, so that document's AI label stays true. Nothing else about the run is sent.

**Stop:** **Disconnect** in the tab (this also removes the computer from your account), or remove
it in Weekend Studio. Turn same-computer mode off in the tab. Uninstalling the node and deleting
its folder removes everything it keeps.

## Where it keeps things

Never under ComfyUI's `user/` folder (ComfyUI serves that over HTTP). One folder per ComfyUI
install, private to your user (0700 / 0600):

- Windows: `%LOCALAPPDATA%\WeekendStudio\ComfyUI\<install>\`
- macOS: `~/Library/Application Support/Weekend Studio/ComfyUI/<install>/`
- Linux: `~/.config/weekend-studio/comfyui/<install>/` (or `$XDG_CONFIG_HOME`)

`<install>` comes from the ComfyUI folder's path, so two ComfyUIs never share a machine token.
Moving ComfyUI to another folder means connecting again. In Docker, put that folder (or
`$XDG_CONFIG_HOME/weekend-studio`) on a volume, or the token is lost with the container.

Inside: `machine.json` (the token), `install.json` (a random id for this install), `settings.json`
(the two switches), `workflows/` (what you showed). Version 0.2 kept the token and workflows in
`ComfyUI/user/default/weekend-studio/`; 0.3 moves them out on its first start.

## Who can use the tab's routes

The tab talks to ComfyUI's own server under `/weekend-studio/*`. Every route refuses a request
unless:

- it carries the header `X-Weekend-Studio: 1` (a page on another site can't add it: ComfyUI's
  CORS answer, even with `--enable-cors-header`, allows only `Content-Type` and `Authorization`);
- its Host is `127.0.0.1`, `localhost` or `[::1]` (so DNS rebinding fails), or a specific address
  ComfyUI was told to `--listen` on, or a name listed under `allowedHosts` in `settings.json` (below);
- its Origin, when the browser sends one, is the same as the Host.

With `--listen` on every address (`0.0.0.0`), the tab works only on the ComfyUI computer itself
(`http://127.0.0.1:8188`) unless you name the address you use under `allowedHosts`.

Two keys in this install's `settings.json` (in the folder above) are edited by hand, never by the tab:

    { "allowedHosts": ["studio-pc.local"], "localPort": 9200 }

`allowedHosts` lists extra names the tab's routes answer on (a reverse proxy, a LAN name).
`localPort` is where same-computer mode starts looking for a free port (default 8189; it tries five).
Restart ComfyUI after changing them.

## Testing without the app

The routes (each needs `-H 'X-Weekend-Studio: 1'` and a loopback Host):

- `GET /weekend-studio/status`
- `POST /weekend-studio/settings` (`{sameComputer?, autoSync?}`)
- `POST /weekend-studio/connect` (`{name?}`), then open the `approveUrl` it returns
- `POST /weekend-studio/disconnect`
- `GET` / `POST /weekend-studio/workflows` (publish takes `{name, prompt}`, where `prompt` is the
  API-format export)
- `POST /weekend-studio/test-run` (`{workflowId, values, images: {key: base64}}`)

`weekend_studio/service.json` says where Weekend Studio is. Generate it with
`node tools/write-service-json.mjs`. Its `publicAnonKey` is Supabase's public project key (the
same one the website ships), not a secret: every call is authorized by a one-time pairing secret
or by this computer's own machine token. A development copy writes its own (`--staging`; for a local
site, add a `localOrigins` list to it by hand). Nothing comes from environment variables.

## For Comfy Registry reviewers

What a scan of this package finds, and why:

- **An outbound WebSocket that receives `run` messages.** That is the product: the user's own
  browser, signed in to their Weekend Studio account, asks their own ComfyUI to run a workflow
  they published. It opens only after the user clicks Connect, and closes on Disconnect. The relay
  can only name a workflow the user published here and send values for the settings they chose
  to show. It can never send a graph, a file path or code (`runner.py`, `description.py`).
- **A second HTTP server** (`local_server.py`): off until the user turns on same-computer mode in
  the tab, bound to `127.0.0.1` only, Origin- and Host-checked on every request.
- **A key-shaped string** in `weekend_studio/service.json`: `publicAnonKey`, Supabase's public
  project key (`sb_publishable_…`), published in every page of the website. It grants nothing by
  itself.
- **Unverified TLS** (`comfy_client.py` `own_tls_context`): only for this ComfyUI's own address
  when ComfyUI itself was started with `--tls-keyfile` (usually a self-signed certificate for
  127.0.0.1). Everything else is verified.
- **Base64 decoded and written to disk**: input pictures of a run, checked to be PNG, JPEG or
  WebP by their bytes, size-capped, written under `input/weekend-studio/<job>/` with names the node
  chooses, and deleted after the run.
- **Automatic upload of the open workflow**: only with "Update shown workflows automatically"
  turned on, and only for workflows this ComfyUI published itself.
- **No environment variables** (since 0.3.2). 0.2.0 was flagged under `policy-v0.6:
  credential-exfiltration`: it read `WEEKEND_STUDIO_API_KEY` (an override of the public key above,
  for development) near the relay connection. Every override is gone; where Weekend Studio is comes
  only from `service.json`. The only environment reads left are the OS's own folder names
  (`LOCALAPPDATA`, `XDG_CONFIG_HOME`) in `home.config_base`, to find the per-user settings folder.
  `tools/release-check.mjs` refuses a release that reads any other.

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
    node --test tests/web_signature.test.mjs tests/web_rules.test.mjs

The HTTP, route and run-loop tests need aiohttp (ComfyUI ships it); without it they skip. The
shared test cases come from `packages/comfy/fixtures/` in the Weekend Studio repository;
`tests/fixtures/` is the synced copy, so the tests also run here on their own.

## License

MIT, copyright Weekend Industries. See `LICENSE`.

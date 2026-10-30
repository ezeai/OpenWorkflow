# Connecting ComfyUI

ComfyUI is the engine. You install and start it yourself; Open Workflow connects to it.

## 1. Start ComfyUI

Start ComfyUI the way you normally do (for a portable install, its `run_nvidia_gpu.bat` or similar launcher). Wait until its console says it is running and the web page loads.

## 2. Find its address

ComfyUI's default address is:

```
http://127.0.0.1:8188
```

If you started it with a different port (`--port`), use that port instead. ComfyUI's console prints its address when it starts.

## 3. Point Open Workflow at it

The interface does not have an address field yet. Set it in the settings file, with Open Workflow **stopped**:

1. Close Open Workflow.
2. Open `data\config.json` in a text editor.
3. Set `"comfy_url"` to your ComfyUI address, for example `"http://127.0.0.1:8188"`.
4. Save, then start Open Workflow again.

Only loopback addresses are accepted. If the file cannot be read or a value is invalid, Open Workflow does **not** show an error. It renames the file to `data\config.json.corrupt`, starts with default settings, and keeps your old file for you to copy values back from. Check the file after you edit it.

Then check the status in the top bar of the app (or in Settings). It shows:

- **ComfyUI connected**: ComfyUI answered.
- **ComfyUI not running**: nothing answered at that address. Start ComfyUI, then reload the page.

## What Open Workflow will and will not accept

- Only loopback addresses are accepted: `127.0.0.1`, `localhost` or `::1`. A remote address is refused, so your workflows never leave your computer.
- Open Workflow never starts ComfyUI for you in this release, and it never switches to a cloud service.
- It reads ComfyUI's status and submits your runs. It does not change ComfyUI's configuration, its models or its custom nodes.

## When the connection fails

| What you see | What to do |
| --- | --- |
| Not running | Start ComfyUI; wait for its page to load; test again. |
| Wrong address or port | Check ComfyUI's console for its address and set `comfy_url` in `data\config.json` (see step 3). |
| ComfyUI is busy (slow to answer) | Wait a few seconds and test again. Apps show a wait, not a failure, while ComfyUI is busy. |
| Connected, but an app says "missing" | This is not a connection problem. See [MODEL_REQUIREMENTS.md](MODEL_REQUIREMENTS.md) and the missing-node notes in the app. |

Your work is kept when the connection fails: projects, apps and outputs stay on disk.

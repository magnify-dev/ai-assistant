# Jarvis Firefox Extension

The Firefox bridge lets Jarvis read the real DOM from your logged-in Firefox tabs.

## Important

Firefox temporary add-ons loaded through `about:debugging` are removed when Firefox restarts.

For a permanent install in normal Firefox, Mozilla requires the extension to be signed. There is no reliable local-only bypass for regular Firefox release builds.

## Best Permanent Option

Use Mozilla's **unlisted signing**:

1. Package the extension:

```powershell
cd C:\Users\marce\Documents\Programming\ai-assistant
.\package-firefox-extension.ps1
```

2. Upload `dist\jarvis-page-bridge.xpi` to Mozilla Add-ons as an **unlisted** extension.

3. Download the signed `.xpi` from Mozilla.

4. Open the signed `.xpi` in Firefox and install it once.

After that it stays installed like a normal extension.

## Local Developer Option

If you want fully local unsigned installs, use Firefox Developer Edition, Nightly, or ESR with extension signature enforcement disabled.

In that browser:

1. Open `about:config`.
2. Set `xpinstall.signatures.required` to `false`.
3. Open `about:addons`.
4. Install `dist\jarvis-page-bridge.xpi`.

This does not work reliably in normal Firefox release.

## Temporary Debug Install

For quick testing:

1. Open `about:debugging#/runtime/this-firefox`.
2. Click **Load Temporary Add-on...**.
3. Select `firefox-extension\manifest.json`.

This is removed after Firefox restarts.

## Automatic Session Install

The Jarvis UI has a **Start Firefox Bridge** button.

This starts Firefox with your default profile, packages the extension, installs it temporarily, and opens YouTube playlists.

Important:

- Close Firefox first.
- The script uses your default Firefox profile, so your normal login should be available.
- The extension is still temporary, but you do not have to load it manually.
- Keep the Firefox bridge process running while using Jarvis with Firefox.

## Runtime

Jarvis must be running so the local bridge exists:

`http://127.0.0.1:8765/context`

The extension only sends page data to localhost on your machine.

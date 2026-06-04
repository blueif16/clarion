# vendor/ — vendored third-party code (no remote code in MV3)

MV3's content-security policy forbids loading executable code from a remote
origin, so the LiveKit browser SDK is **vendored** here and loaded as a local
classic `<script>` from `offscreen.html`. Nothing is fetched from a CDN.

## `livekit-client.umd.js`

- **Package:** [`livekit-client`](https://www.npmjs.com/package/livekit-client)
  (the npm name; the scoped `@livekit/client` alias 404s — the package is
  unscoped). Browser SDK, MIT-licensed.
- **Version:** `2.19.1`
- **File:** the prebuilt UMD bundle shipped in the package, copied verbatim from
  `dist/livekit-client.umd.js`. It is fully self-contained — zero bare imports,
  all dependencies bundled — so a single file is all that is needed. The
  end-to-end-encryption / packet-trailer Web Workers are NOT vendored because
  Clarion does not enable E2EE (those `new Worker(...)` paths never run).
- **Global:** loaded as a classic script it assigns `globalThis.LivekitClient`
  (`{ Room, RoomEvent, Track, ... }`).
- **SHA-256:** `5a4d11f54007aab8233943cbd2f8c7ba4ed5101f0fffaf8bf7630f813ce86b33`

### How it was produced (reproducible)

```bash
cd $(mktemp -d)
npm pack livekit-client                       # → livekit-client-2.19.1.tgz
tar -xzf livekit-client-2.19.1.tgz \
  package/dist/livekit-client.umd.js
cp package/dist/livekit-client.umd.js \
  <repo>/web/extension/vendor/livekit-client.umd.js
shasum -a 256 <repo>/web/extension/vendor/livekit-client.umd.js
# expect: 5a4d11f54007aab8233943cbd2f8c7ba4ed5101f0fffaf8bf7630f813ce86b33
```

To bump the SDK: re-`npm pack` the new version, copy the UMD, update the version
and SHA above, and re-run the smoke check below.

### Smoke check

```bash
node web/extension/vendor/smoke.cjs    # prints SMOKE: PASS, exits 0
```

It loads the UMD via Node's CommonJS loader and asserts it exposes the exact
symbols `offscreen.js` uses — `Room` (with instance `connect` / `disconnect` /
`localParticipant.setMicrophoneEnabled`), `RoomEvent.TrackSubscribed`, and
`Track.Kind.Audio` — plus `version === 2.19.1`. (A plain `require()` from inside
`web/extension/` would fail because that directory's `package.json` sets
`"type":"module"`; the smoke script compiles the source as CommonJS to avoid
that, matching how the browser's classic-script load behaves.)

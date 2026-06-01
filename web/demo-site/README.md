# Clarion Demo Site (Dsite)

Self-hosted fake utility-account site — the **shell** the Clarion demo runs against.
This is the scaffold only. Scripted accessibility flaws arrive in **D2**; the LiveKit
legibility panel arrives in **U1**. Keep this skeleton clean.

> Sandboxed. Modeled on real sites; **no real money or credentials**.

## Stack

- **Next.js 16.2.2** (App Router, TypeScript)
- **React 19**
- Turbopack is the default dev/build bundler (no flag needed)
- No Tailwind, no ESLint, no extra libraries — deps are intentionally minimal

## Install & run

```bash
cd web/demo-site
npm install
npm run dev
```

Then open **http://localhost:3000**.

(If port 3000 is taken, Next picks the next free port and prints the URL.)

## Routes (App Router segments under `app/`)

| Route            | File                       | Purpose                                  |
| ---------------- | -------------------------- | ---------------------------------------- |
| `/`              | `app/page.tsx`             | Landing / login (username + password)    |
| `/account`       | `app/account/page.tsx`     | Account & billing overview               |
| `/account/pay`   | `app/account/pay/page.tsx` | Bill payment form                        |

Shared chrome (top bar, disclosure, global styles) lives in `app/layout.tsx` +
`app/globals.css`.

## Build & production start

```bash
npm run build
npm run start
```

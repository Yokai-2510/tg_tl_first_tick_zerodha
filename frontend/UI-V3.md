# Console UI v3 — what changed and how to land it

This replaces the Tailwind-class UI under `frontend/src` with the v3 surface.
The backend contract is unchanged: `lib/api.ts`, `lib/store.ts` and
`lib/format.ts` are the repo's existing files and were not modified.

## Landing it

```bash
git clone https://github.com/Yokai-2510/tg_tl_first_tick_zerodha.git
cd tg_tl_first_tick_zerodha
# copy the frontend/ folder from the download over the repo's frontend/
cd frontend && npm install && npm run build   # tsc -b && vite build
git add -A && git commit -m "UI v3: token surface, sidebar shell, seven screens"
git push
```

### Files added

```
src/index.css                 design tokens (light + dark), resets, tabular numerals
tailwind.config.js            colour names mirroring the CSS variables
src/lib/style.ts              card/pill/segmented/chip helpers, tone(), spark()
src/lib/prefs.ts              theme, accent, display name, sign-in greetings
src/lib/sections.ts           curated config groups, icons, phase meanings
src/lib/patch.ts              dotted-path get + RFC 7386 merge patch builder
src/lib/toast.ts              transient notifications
src/components/ui.tsx         primitives (Card, Segmented, Dialog, Toasts, …)
src/components/Shell.tsx      sidebar, topbar, session actions, alert stack
src/components/ConfigForm.tsx curated config renderer
src/pages/*.tsx              SignIn, Dashboard, Positions, LiveData,
                              StatusPage, Strategy, Settings, LogsEvents
```

### Files replaced

- `src/App.tsx` — now routing only; sign-in, topbar and alerts moved out.
- `src/main.tsx` — calls `applyPrefs()` before mount so a dark setup does not
  flash light.
- `src/components/ui.tsx` — `Confirm` → `Dialog`, `StatusDot` → inline dot,
  plus `Toasts`, `Segmented`, `Stepper`, `Gauge`, `StackedBar`, `Scroller`.
  Nothing else imports the old primitives.

### Files deleted

Any old page files not in the list above (`src/pages/Settings.tsx` etc. are
overwritten in place; no stale imports remain). If the repo's `src/components/`
holds other helpers, they are unused by these screens and can go.

## Conventions worth keeping

**Styling is inline, from CSS variables.** One token change in `index.css`
restyles the console, the operator's accent is a live variable rather than a
rebuilt class, and there is no cascade to trace while reading a screen.
`lib/style.ts` holds the shared vocabulary.

**Config edits are merge patches.** Each Save sends only the paths that changed
(`lib/patch.ts`), so two sections never overwrite each other and a 422 can be
shown verbatim, pointing at a real JSON path.

**Curated config, not a generated form.** `lib/sections.ts` groups the paths an
operator touches daily, in the order the engine makes the decisions, with the
sequence spelled out where behaviour is not obvious from a field name. Anything
uncurated stays reachable through Settings → Raw.

**Sections that need a restart say so.** `structural: true` renders a
RESTART REQUIRED badge; the patch is accepted either way.

**Credentials are documented, not edited.** `credentials.json` is never returned
by the API, so that tab shows the shape and runs five real requests as a
connection test.

**Dashboard P&L is sampled client-side.** `/status` is a point-in-time snapshot
with no history, so the curve is what this console has watched since it was
opened. The card says so rather than implying a server-side series.

## Not verified here

`npm run build` was not run in this environment. Everything is plain
React + TypeScript against the existing `lib/` contract, but the first local
build is the real check — most likely snags are `noUnusedLocals` on any helper
you trim and the router version's function-as-children on `NavLink`.

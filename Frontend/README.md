# React + Vite

## Frontend API and rollout contracts (September 2026)

- USER chat messages: maximum **800 Unicode code points before trimming**.
  The composer shows a counter and preserves an oversized draft for correction.
  AI replies and stored conversation history are not truncated by this policy.
- Manual summary edits: maximum **12,000 stripped Unicode code points**.
  This is not an automatic-memory count or storage quota.
- Memory GET retains `facts`, `relations`, and object-or-null `summary`.
  Optional `page` / `page_size` requests use `pagination.page`, `page_size`,
  `facts.has_next`, and `relations.has_next`; legacy unpaginated responses work.
  Edit preconditions use `expected_updated_at` and summary `expected_summary_id`.
  A 409 keeps the draft; “Reload latest” preserves it, shows the latest saved
  text for review, and explicitly rebases available rows. Missing targets retain
  their old preconditions rather than becoming unconditional writes.
- Turning reminders OFF prevents **new** reminders. Existing scheduled reminders
  remain active, with the calendar still available for editing and cancellation.
  Create inputs use the returned profile `timezone`; each saved row uses its
  returned snapshot `timezone` and `due_at_local` wall time. No browser-local or
  current-offset conversion is applied.
- Password creation/reset/change accepts 8–128 Unicode code points; server-side
  validators remain authoritative. An unlinked Google/email match requires
  password sign-in before explicit linking.
- Push bodies remain metadata-only. Foreground suppression requires receipt of
  that event and a rendered in-app notice ACK within 1 second. Without that ACK
  (including Settings/offline/disconnected chat), the worker shows a system alert.
  Local logout is immediate. Browser and remote push cleanup are independent,
  each bounded to 2 seconds; remote requests retain the departing token and do
  not refresh or borrow the next account’s token. Partial cleanup is reported.
  No telemetry is added.

### Maelezo ya matumizi

Ujumbe wa **mtumiaji** una kikomo cha herufi 800 kabla ya kuondoa nafasi za
pembeni; majibu ya AI hayana kikomo hiki. Rasimu ndefu haikatwi kiotomatiki.
Muhtasari unaohaririwa una kikomo cha herufi 12,000 baada ya kuondoa nafasi za
pembeni; hili si kikomo cha idadi ya kumbukumbu za kiotomatiki.
Ukizima vikumbusho, vipya havitaundwa, lakini vilivyopangwa vinaendelea.
Kalenda bado inaruhusu kuvihariri au kuvighairi. Saa inaonyeshwa pamoja na eneo
la saa la akaunti au la kikumbusho kilichohifadhiwa.
Nenosiri jipya lina herufi 8 hadi 128. Ingia kwa nenosiri kwanza ili kuunganisha
Google na akaunti iliyopo.

### Production/staging metadata

`SEO_PROFILE=staging` is the default and keeps the staging `public/robots.txt`
block. Production builds **must** explicitly set `SEO_PROFILE=production`.
The build emits preserved production `robots.txt`, `sitemap.xml`, and `llms.txt`
from `seo/production/`, byte-for-byte from production commit
`0d4c69f8695cec133127da23463c0f277432266e`.
Keep that directory when cherry-picking to production.

Before deploying, the hosting owner must verify that metadata paths return
their file/content type or a real 404, never the SPA shell; check both a fresh
client and a controlled PWA. The service worker navigation fallback excludes
metadata paths, but cannot configure the hosting platform's own SPA fallback.

Validation: `npm ci`, `npm test -- --maxWorkers=2`, `npm run lint`, and
`VITE_API_BASE_URL=https://api.example.test npm run build`.
For the production artifact, additionally set `SEO_PROFILE=production`.

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the ESLint configuration

If you are developing a production application, we recommend using TypeScript with type-aware lint rules enabled. Check out the [TS template](https://github.com/vitejs/vite/tree/main/packages/create-vite/template-react-ts) for information on how to integrate TypeScript and [`typescript-eslint`](https://typescript-eslint.io) in your project.

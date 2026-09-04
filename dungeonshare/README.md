# Dungeon Share

A player-facing campaign journal with a protected manager. It keeps separate
campaign timelines, accepts drafts from Dungeon Maker and DnD Tracker, and
publishes readable text-and-photo entries.

## What is included

- Public campaign index and chronological journals
- Draft, published, and archived entry states
- Manual notes plus NPC, item, location, lore, and session entry types
- Add, replace, caption, remove, and reorder photos
- Reorder and pin journal entries
- Secure manager account with an email allowlist, rate-limited sign-in, and
  optional passkeys
- Source-specific bearer tokens that can only create drafts and upload photos
- Revision snapshots before every saved edit
- Demo data whenever a local database is not configured

## Local preview

```powershell
npm install
npm run dev
```

Open `http://localhost:3000`. Without a `DATABASE_URL`, the public site and
manager use resettable demo data so the complete workflow can be previewed.

## Neon setup

Dungeon Share uses two new schemas inside the existing Neon database:
`dungeonshare` and `dungeonshare_auth`. It does not alter existing application
tables.

Load `DATABASE_URL` into the current shell, then run:

```powershell
npm run db:migrate
npm run db:seed
```

The seed command adds two starter campaign shells and is safe to rerun.

## Vercel setup

1. Create or link a Vercel project named `dungeonshare`.
2. Connect the existing Neon project or add its pooled `DATABASE_URL`.
3. Create a Vercel Blob store and connect it to the project. Vercel supplies
   `BLOB_READ_WRITE_TOKEN`.
4. Add the values listed in `.env.example` to Vercel.
5. Use `https://dungeonshare.vercel.app` for both `BETTER_AUTH_URL` and
   `NEXT_PUBLIC_SITE_URL`.
6. Run the migrations against the production Neon branch and deploy.

### Create the first manager safely

Generate three unrelated random values of at least 32 characters: a Better Auth
secret, a one-time bootstrap code, and each app's publishing token.

Temporarily set:

```text
DUNGEONSHARE_ALLOW_ADMIN_SIGNUP=true
DUNGEONSHARE_BOOTSTRAP_TOKEN=<one-time code>
DUNGEONSHARE_ADMIN_EMAILS=<your exact email address>
```

Visit `/sign-in`, choose first-time setup, and enter the allowlisted email plus
the one-time code. After the account exists, set
`DUNGEONSHARE_ALLOW_ADMIN_SIGNUP=false`, remove
`DUNGEONSHARE_BOOTSTRAP_TOKEN`, and redeploy. Add a passkey from the manager for
a stronger, easier sign-in.

## Source app integration

See [docs/INTEGRATION.md](docs/INTEGRATION.md). Source tokens cannot edit,
publish, reorder, archive, or delete existing entries. Every source submission
lands in the manager's draft inbox.

## Verification

```powershell
npm run lint
npx tsc --noEmit
npm run build
```

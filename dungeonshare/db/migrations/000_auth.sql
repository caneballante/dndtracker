create schema if not exists dungeonshare_auth;

create table if not exists dungeonshare_auth."user" (
  id text primary key,
  name text not null,
  email text not null unique,
  "emailVerified" boolean not null default false,
  image text,
  "createdAt" timestamptz not null default now(),
  "updatedAt" timestamptz not null default now()
);

create table if not exists dungeonshare_auth.session (
  id text primary key,
  "expiresAt" timestamptz not null,
  token text not null unique,
  "createdAt" timestamptz not null default now(),
  "updatedAt" timestamptz not null default now(),
  "ipAddress" text,
  "userAgent" text,
  "userId" text not null references dungeonshare_auth."user"(id) on delete cascade
);
create index if not exists dungeonshare_auth_session_user_idx
  on dungeonshare_auth.session ("userId");

create table if not exists dungeonshare_auth.account (
  id text primary key,
  "accountId" text not null,
  "providerId" text not null,
  "userId" text not null references dungeonshare_auth."user"(id) on delete cascade,
  "accessToken" text,
  "refreshToken" text,
  "idToken" text,
  "accessTokenExpiresAt" timestamptz,
  "refreshTokenExpiresAt" timestamptz,
  scope text,
  password text,
  "createdAt" timestamptz not null default now(),
  "updatedAt" timestamptz not null default now()
);
create index if not exists dungeonshare_auth_account_user_idx
  on dungeonshare_auth.account ("userId");

create table if not exists dungeonshare_auth.verification (
  id text primary key,
  identifier text not null,
  value text not null,
  "expiresAt" timestamptz not null,
  "createdAt" timestamptz not null default now(),
  "updatedAt" timestamptz not null default now()
);
create index if not exists dungeonshare_auth_verification_identifier_idx
  on dungeonshare_auth.verification (identifier);

create table if not exists dungeonshare_auth.passkey (
  id text primary key,
  name text,
  "publicKey" text not null,
  "userId" text not null references dungeonshare_auth."user"(id) on delete cascade,
  "credentialID" text not null,
  counter integer not null,
  "deviceType" text not null,
  "backedUp" boolean not null,
  transports text,
  "createdAt" timestamptz,
  aaguid text
);
create index if not exists dungeonshare_auth_passkey_user_idx
  on dungeonshare_auth.passkey ("userId");
create index if not exists dungeonshare_auth_passkey_credential_idx
  on dungeonshare_auth.passkey ("credentialID");

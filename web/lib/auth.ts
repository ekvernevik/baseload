import "server-only";

import { createHmac, timingSafeEqual } from "node:crypto";
import { cookies } from "next/headers";

// Shared access-code gate for the demo. Not a full user-auth system — it issues
// a single signed session cookie when the visitor presents the correct access
// code. Swap for Auth.js/Clerk when per-user accounts are needed (see
// docs/strategic_review). Runs only in Node contexts (server actions, route
// handlers, server components) — never in proxy.ts, which does an optimistic
// cookie-presence check only.

export const SESSION_COOKIE = "bsld_session";
const MAX_AGE_SECONDS = 60 * 60 * 24 * 7; // 7 days

function secret(): string {
  const s = process.env.SESSION_SECRET;
  if (s && s.length > 0) return s;
  if (process.env.NODE_ENV === "production") {
    throw new Error("SESSION_SECRET must be set in production");
  }
  // Dev-only fallback so `next dev` runs without local env setup.
  console.warn("[auth] SESSION_SECRET unset — using insecure dev fallback");
  return "dev-insecure-secret";
}

function b64url(input: Buffer | string): string {
  return Buffer.from(input).toString("base64url");
}

function sign(payload: string): string {
  return createHmac("sha256", secret()).update(payload).digest("base64url");
}

/** Constant-time compare of the submitted access code against the configured one. */
export function verifyAccessCode(submitted: string): boolean {
  const expected = process.env.BASELOAD_ACCESS_CODE ?? "";
  if (expected.length === 0) {
    if (process.env.NODE_ENV === "production") return false;
    console.warn("[auth] BASELOAD_ACCESS_CODE unset — accepting 'baseload' in dev");
    return submitted === "baseload";
  }
  const a = Buffer.from(submitted);
  const b = Buffer.from(expected);
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}

function createSessionToken(): string {
  const payload = b64url(JSON.stringify({ iat: Date.now(), v: 1 }));
  return `${payload}.${sign(payload)}`;
}

function isFreshToken(token: string): boolean {
  const [payload, sig] = token.split(".");
  if (!payload || !sig) return false;

  const expected = sign(payload);
  const got = Buffer.from(sig);
  const exp = Buffer.from(expected);
  if (got.length !== exp.length || !timingSafeEqual(got, exp)) return false;

  try {
    const { iat } = JSON.parse(Buffer.from(payload, "base64url").toString());
    return typeof iat === "number" && Date.now() - iat < MAX_AGE_SECONDS * 1000;
  } catch {
    return false;
  }
}

/** Issue the session cookie. Call from a server action or route handler. */
export async function createSession(): Promise<void> {
  const store = await cookies();
  store.set(SESSION_COOKIE, createSessionToken(), {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: MAX_AGE_SECONDS,
  });
}

/** Clear the session cookie. Call from a server action or route handler. */
export async function destroySession(): Promise<void> {
  const store = await cookies();
  store.delete(SESSION_COOKIE);
}

/** Real session check (verifies signature + freshness). Use in the data layer. */
export async function hasValidSession(): Promise<boolean> {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  return token ? isFreshToken(token) : false;
}

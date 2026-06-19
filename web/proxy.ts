import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// Next.js 16 renamed Middleware → Proxy (same functionality, runs before the
// request completes). This is an OPTIMISTIC gate only: it redirects visitors
// without a session cookie away from /dashboard. The real signature/freshness
// check happens server-side in the dashboard layout via hasValidSession().
const SESSION_COOKIE = "bsld_session";

export function proxy(request: NextRequest) {
  const hasCookie = request.cookies.has(SESSION_COOKIE);
  if (!hasCookie) {
    const url = new URL("/login", request.url);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: "/dashboard/:path*",
};

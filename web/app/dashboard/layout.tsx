import Link from "next/link";
import { redirect } from "next/navigation";

import { hasValidSession } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { logout } from "./actions";

export const metadata = {
  title: "Platform — Baseload",
};

const NAV = [
  { label: "Overview", href: "#overview" },
  { label: "Valuation", href: "#valuation" },
  { label: "Price & Spread", href: "#signal" },
  { label: "Regimes", href: "#regimes" },
  { label: "Congestion", href: "#congestion" },
];

export default async function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Real session check (signature + freshness). proxy.ts only did the optimistic
  // cookie-presence gate; this is the authoritative one.
  if (!(await hasValidSession())) {
    redirect("/login");
  }

  return (
    <div className="flex min-h-screen bg-black text-white">
      {/* Sidebar */}
      <aside className="hidden w-60 shrink-0 flex-col border-r border-white/10 px-5 py-6 md:flex">
        <Link href="/" className="mb-8 font-semibold tracking-tight">
          Baseload
        </Link>
        <nav className="flex flex-col gap-1">
          {NAV.map((item) => (
            <a
              key={item.href}
              href={item.href}
              className="rounded-md px-3 py-2 text-sm text-white/55 transition-colors hover:bg-white/5 hover:text-white"
            >
              {item.label}
            </a>
          ))}
        </nav>
        <div className="mt-auto text-xs text-white/30">
          NO1–NO5 · day-ahead
          <br />
          Nordic grid intelligence
        </div>
      </aside>

      {/* Main */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center justify-between border-b border-white/10 px-6">
          <div className="flex items-center gap-3 text-sm">
            <span className="font-medium">Platform</span>
            <span className="rounded-full border border-amber-400/30 bg-amber-400/10 px-2 py-0.5 text-xs text-amber-300/90">
              Demo data
            </span>
          </div>
          <form action={logout}>
            <Button type="submit" variant="ghost" size="sm">
              Sign out
            </Button>
          </form>
        </header>
        <main className="flex-1 overflow-y-auto px-6 py-8">{children}</main>
      </div>
    </div>
  );
}

import Link from "next/link";
import { redirect } from "next/navigation";

import { hasValidSession } from "@/lib/auth";
import { LoginForm } from "./login-form";

export const metadata = {
  title: "Sign in — Baseload",
};

export default async function LoginPage() {
  // Already authenticated visitors skip straight to the platform.
  if (await hasValidSession()) {
    redirect("/dashboard");
  }

  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-black px-6">
      <div className="w-full max-w-sm">
        <Link
          href="/"
          className="mb-10 block text-center text-sm text-white/40 transition-colors hover:text-white"
        >
          ← Baseload
        </Link>

        <h1 className="mb-1 text-2xl font-bold tracking-tight text-white">
          Platform access
        </h1>
        <p className="mb-8 text-sm text-white/50">
          Nordic grid intelligence. Enter the access code we shared with you.
        </p>

        <LoginForm />

        <p className="mt-8 text-center text-xs text-white/30">
          Don&apos;t have a code?{" "}
          <Link href="/#contact" className="underline hover:text-white/60">
            Request access
          </Link>
        </p>
      </div>
    </main>
  );
}

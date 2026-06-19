"use client";

import { useActionState } from "react";

import { Button } from "@/components/ui/button";
import { login, type LoginState } from "./actions";

const initial: LoginState = {};

export function LoginForm() {
  const [state, formAction, pending] = useActionState(login, initial);

  return (
    <form action={formAction} className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <label htmlFor="code" className="text-sm text-white/60">
          Access code
        </label>
        <input
          id="code"
          name="code"
          type="password"
          autoComplete="off"
          autoFocus
          required
          className="h-10 rounded-lg border border-white/15 bg-white/5 px-3 text-white outline-none transition-colors focus:border-white/40"
          placeholder="Enter your access code"
        />
      </div>

      {state.error ? (
        <p className="text-sm text-red-400" role="alert">
          {state.error}
        </p>
      ) : null}

      <Button type="submit" size="lg" disabled={pending} className="mt-2 w-full">
        {pending ? "Verifying…" : "Enter platform"}
      </Button>
    </form>
  );
}

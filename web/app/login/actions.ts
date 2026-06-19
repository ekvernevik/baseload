"use server";

import { redirect } from "next/navigation";

import { createSession, verifyAccessCode } from "@/lib/auth";

export interface LoginState {
  error?: string;
}

export async function login(
  _prev: LoginState,
  formData: FormData,
): Promise<LoginState> {
  const code = String(formData.get("code") ?? "");

  if (!verifyAccessCode(code)) {
    return { error: "Invalid access code." };
  }

  await createSession();
  redirect("/dashboard");
}

"use client";

import { KeyRound, LoaderCircle, ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";

import { authClient } from "@/lib/auth-client";

export function SignInForm({ allowSignup }: { allowSignup: boolean }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [bootstrapCode, setBootstrapCode] = useState("");
  const [mode, setMode] = useState<"sign-in" | "sign-up">("sign-in");
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setMessage("");
    try {
      const result =
        mode === "sign-up"
          ? await authClient.signUp.email(
              {
                email,
                password,
                name: "Dungeon Share Manager",
              },
              {
                headers: {
                  "x-dungeonshare-bootstrap": bootstrapCode,
                },
              },
            )
          : await authClient.signIn.email({ email, password });
      if (result.error) throw new Error(result.error.message);
      router.push("/manage");
      router.refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Sign-in failed.");
    } finally {
      setPending(false);
    }
  }

  async function signInWithPasskey() {
    setPending(true);
    setMessage("");
    try {
      const result = await authClient.signIn.passkey({
        autoFill: false,
      });
      if (result?.error) throw new Error(result.error.message);
      router.push("/manage");
      router.refresh();
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : "Passkey sign-in failed.",
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="sign-in-card">
      <div className="sign-in-seal" aria-hidden="true">
        <ShieldCheck />
      </div>
      <p className="eyebrow">Dungeon Share manager</p>
      <h1>{mode === "sign-up" ? "Create the administrator" : "Welcome back"}</h1>
      <p>
        {mode === "sign-up"
          ? "Create the single account allowed to publish and manage the journal."
          : "Sign in to review drafts, repair entries, and publish to your players."}
      </p>

      <form onSubmit={submit}>
        <label>
          Email
          <input
            autoComplete="email"
            onChange={(event) => setEmail(event.target.value)}
            required
            type="email"
            value={email}
          />
        </label>
        <label>
          Password
          <input
            autoComplete={
              mode === "sign-up" ? "new-password" : "current-password"
            }
            minLength={12}
            onChange={(event) => setPassword(event.target.value)}
            required
            type="password"
            value={password}
          />
        </label>
        {mode === "sign-up" ? (
          <label>
            One-time setup code
            <input
              autoComplete="off"
              minLength={32}
              onChange={(event) => setBootstrapCode(event.target.value)}
              required
              type="password"
              value={bootstrapCode}
            />
          </label>
        ) : null}
        <button className="button button-primary full-button" disabled={pending}>
          {pending ? (
            <LoaderCircle className="spin" aria-hidden="true" />
          ) : (
            <KeyRound aria-hidden="true" />
          )}
          {mode === "sign-up" ? "Create administrator" : "Sign in securely"}
        </button>
      </form>

      {mode === "sign-in" ? (
        <>
          <div className="or-rule">
            <span>or</span>
          </div>
          <button
            className="button button-quiet full-button"
            disabled={pending}
            onClick={signInWithPasskey}
            type="button"
          >
            Use a passkey
          </button>
        </>
      ) : null}

      {message ? <p className="form-message error-message">{message}</p> : null}

      {allowSignup ? (
        <button
          className="text-button"
          onClick={() =>
            setMode((current) =>
              current === "sign-in" ? "sign-up" : "sign-in",
            )
          }
          type="button"
        >
          {mode === "sign-in"
            ? "First-time setup: create the administrator"
            : "Return to sign in"}
        </button>
      ) : null}
    </div>
  );
}

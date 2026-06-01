"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export default function LandingPage() {
  const router = useRouter();
  const [error, setError] = useState("");

  function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    const user = fd.get("username") as string;
    const pass = fd.get("password") as string;

    // Accept any non-empty username + password "demo"/"demo" for the sandbox.
    if (user.trim() && pass === "demo") {
      sessionStorage.setItem("nw_auth", "1");
      router.push("/account");
    } else {
      setError("Invalid credentials. Hint: username = anything, password = demo");
    }
  }

  return (
    <>
      <h1>Sign in to your account</h1>
      <p className="muted">
        Manage your electric service, view statements, and pay your bill.
      </p>

      <section className="card" aria-label="Sign in">
        <form onSubmit={handleSubmit}>
          {/* PROPERLY labeled input — kept for contrast */}
          <div className="field">
            <label htmlFor="username">Username</label>
            <input
              id="username"
              name="username"
              type="text"
              autoComplete="username"
              placeholder="you@example.com"
            />
          </div>

          {/* UNLABELED input — REAL a11y flaw (D2):
              No <label for>, no aria-label, no aria-labelledby, no title,
              no placeholder.  The visually-adjacent <span> text "Password"
              is NOT programmatically associated (no for/aria-labelledby).
              Per accname-1.1 the accessible name MUST be empty ("").
              A screen reader will announce this as an unlabeled edit field. */}
          <div className="field">
            <span className="field-hint">Password</span>
            <input
              name="password"
              type="password"
              autoComplete="current-password"
            />
          </div>

          {error && (
            <p className="field-error" role="alert">
              {error}
            </p>
          )}

          <button className="btn" type="submit">
            Sign in
          </button>
        </form>
      </section>

      <p className="muted hint-block">
        Demo: username = anything, password = <strong>demo</strong>
      </p>
    </>
  );
}

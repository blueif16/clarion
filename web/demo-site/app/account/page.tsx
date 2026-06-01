"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

export default function AccountPage() {
  const router = useRouter();

  // Auth wall: if sessionStorage flag is absent the user is not signed in.
  // Client-side check → redirect to / (the login page).
  useEffect(() => {
    if (!sessionStorage.getItem("nw_auth")) {
      router.replace("/");
    }
  }, [router]);

  return (
    <>
      <h1>Account &amp; Billing</h1>
      <p className="muted">Service address: 1450 Birchwood Ave, Apt 3B</p>

      <section className="card" aria-label="Current balance">
        <h2>Current balance</h2>
        <div className="row">
          <span>Amount due</span>
          <strong>$84.32</strong>
        </div>
        <div className="row">
          <span>Due date</span>
          <span>June 15, 2026</span>
        </div>
        <div className="row">
          <span>Account number</span>
          <span>NW-4417-0093</span>
        </div>
        <p style={{ marginTop: 16 }}>
          <Link className="btn" href="/account/pay">
            Pay bill
          </Link>
        </p>
      </section>

      <p className="muted" style={{ marginTop: 16 }}>
        Back to <Link href="/">sign in</Link>.
      </p>
    </>
  );
}

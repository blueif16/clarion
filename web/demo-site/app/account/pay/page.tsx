"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

export default function PayPage() {
  const router = useRouter();

  // Auth wall — bounce unauthenticated visitors.
  useEffect(() => {
    if (!sessionStorage.getItem("nw_auth")) {
      router.replace("/");
    }
  }, [router]);

  // ── Autopay upsell modal state ──────────────────────────────────────────
  // The modal appears when the page mounts (simulating a real mid-flow
  // interstitial).  It is a REAL overlay: role="dialog", aria-modal="true",
  // with a visible and accessible close button.  The agent must find and
  // dismiss it before interacting with the payment form underneath.
  const [upsellOpen, setUpsellOpen] = useState(true);
  const closeRef = useRef<HTMLButtonElement>(null);

  // Move focus into the modal when it opens (real focus-trap behaviour).
  useEffect(() => {
    if (upsellOpen) {
      closeRef.current?.focus();
    }
  }, [upsellOpen]);

  // ── Layout-shifting confirmation state ─────────────────────────────────
  // After submit we async-inject a confirmation banner that pushes content
  // down (real layout shift).  The banner is NOT in the initial DOM — it is
  // inserted 1 s after submit, causing surrounding elements to jump.
  const [submitted, setSubmitted] = useState(false);
  const [confirmationVisible, setConfirmationVisible] = useState(false);
  const [confirmationNum, setConfirmationNum] = useState("");

  function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setSubmitted(true);
    // Simulate async server round-trip that then injects the confirmation.
    setTimeout(() => {
      // Generate a fake confirmation number.
      const num = "NW-" + Math.floor(100000 + Math.random() * 900000);
      setConfirmationNum(num);
      setConfirmationVisible(true);
    }, 1200);
  }

  return (
    <>
      {/* ── AUTOPAY UPSELL MODAL (flaw #2) ─────────────────────────────
          Real overlay: role="dialog", aria-modal="true".
          Has a real close button with an accessible name so the agent can
          dismiss it.  While open it sits above the form in the stacking
          context (z-index), creating a real barrier. */}
      {upsellOpen && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="upsell-title"
          aria-describedby="upsell-desc"
          className="upsell-backdrop"
        >
          <div className="upsell-box">
            <h2 id="upsell-title">Save time — enable AutoPay</h2>
            <p id="upsell-desc">
              Never miss a payment. Enroll in AutoPay and we&rsquo;ll
              automatically charge your card each month.
            </p>
            <div className="upsell-actions">
              <button
                className="btn"
                onClick={() => setUpsellOpen(false)}
                type="button"
              >
                Enable AutoPay
              </button>
              <button
                ref={closeRef}
                className="btn btn-ghost"
                onClick={() => setUpsellOpen(false)}
                type="button"
                aria-label="No thanks, close this offer"
              >
                No thanks
              </button>
            </div>
          </div>
        </div>
      )}

      <h1>Pay your bill</h1>
      <p className="muted">Account NW-4417-0093 &middot; Amount due $84.32</p>

      {/* ── LAYOUT-SHIFTING CONFIRMATION BANNER (flaw #3) ──────────────
          The banner is async-injected AFTER submit (not present in the
          initial DOM), causing the content below to jump downward.
          real layout shift — not a hidden/opacity toggle. */}
      {confirmationVisible && (
        <div className="confirm-banner" role="status" aria-live="polite">
          <strong>Payment submitted!</strong> Confirmation:{" "}
          <span id="conf-num">{confirmationNum}</span>. Allow 1–2 business days
          to process.
        </div>
      )}

      <section className="card" aria-label="Payment">
        <form onSubmit={handleSubmit}>
          <div className="field">
            <label htmlFor="amount">Payment amount</label>
            <input
              id="amount"
              name="amount"
              type="text"
              defaultValue="84.32"
            />
          </div>

          <div className="field">
            <label htmlFor="card">Card number</label>
            <input
              id="card"
              name="card"
              type="text"
              inputMode="numeric"
              placeholder="•••• •••• •••• ••••"
            />
          </div>

          <div className="field">
            <label htmlFor="expiry">Expiry</label>
            <input
              id="expiry"
              name="expiry"
              type="text"
              placeholder="MM / YY"
            />
          </div>

          {submitted && !confirmationVisible && (
            <p className="field-processing" aria-live="polite">
              Processing payment&hellip;
            </p>
          )}

          <button
            className="btn"
            type="submit"
            disabled={submitted}
            aria-disabled={submitted}
          >
            {submitted ? "Submitted" : "Submit payment"}
          </button>
        </form>
      </section>

      <p className="muted" style={{ marginTop: 16 }}>
        Back to <Link href="/account">account &amp; billing</Link>.
      </p>
    </>
  );
}

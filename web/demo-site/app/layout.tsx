import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Northwind Utilities",
  description:
    "Sandboxed fake utility-account site for the Clarion demo. Modeled on real sites; no real money or credentials.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <header className="topbar">
          <Link href="/" className="brand">
            Northwind Utilities
          </Link>
          <nav>
            <Link href="/account">My Account</Link>
          </nav>
        </header>
        <main className="shell">{children}</main>
        <p className="disclosure">
          Demo environment. Modeled on real sites; sandboxed. No real money or
          credentials.
        </p>
      </body>
    </html>
  );
}

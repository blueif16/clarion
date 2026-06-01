import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Clarion — Legibility Panel",
  description: "Six-effect on-screen panel for the Clarion voice agent (execution §6)",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body style={{ margin: 0, padding: 0, background: "#050505" }}>
        {children}
      </body>
    </html>
  );
}

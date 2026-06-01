/**
 * Root page — App Router Server Component.
 * Reads ?live=1 from searchParams to switch between mock and live modes.
 * All interactive work is delegated to <ClarionPanel> (a Client Component).
 *
 * Context7: Next.js v16.2.2 /vercel/next.js — App Router page with searchParams.
 */

import { ClarionPanel } from "@/components/ClarionPanel";

interface PageProps {
  searchParams: Promise<{ live?: string }>;
}

export default async function PanelPage({ searchParams }: PageProps) {
  const params = await searchParams;
  const liveMode = params.live === "1";

  const lkUrl = process.env.NEXT_PUBLIC_LK_URL ?? "";
  const lkToken = process.env.NEXT_PUBLIC_LK_TOKEN ?? "";

  return (
    <ClarionPanel
      liveMode={liveMode}
      lkUrl={lkUrl}
      lkToken={lkToken}
    />
  );
}

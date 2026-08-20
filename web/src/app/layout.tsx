import type { Metadata } from "next";
import Link from "next/link";
import { Newsreader, Public_Sans, Roboto_Mono } from "next/font/google";
import { Suspense } from "react";

import { ReleaseIdentity } from "@/components/release-identity";

import "./globals.css";

const publicSans = Public_Sans({
  variable: "--font-public-sans",
  subsets: ["latin"],
  display: "swap",
});

const newsreader = Newsreader({
  variable: "--font-newsreader",
  subsets: ["latin"],
  display: "swap",
});

// Decision 0023: the identifier role — provision and Source ids, digests,
// release tags, page numbers, and the provenance rail.
const robotoMono = Roboto_Mono({
  variable: "--font-roboto-mono",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: {
    default: "OpenActs",
    template: "%s | OpenActs",
  },
  description:
    "Read, navigate, verify, and share source-backed Nigerian legislation.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${publicSans.variable} ${newsreader.variable} ${robotoMono.variable}`}>
      <body
        suppressHydrationWarning
        className="min-h-screen bg-paper font-sans text-ink antialiased"
      >
        <a
          href="#main-content"
          className="fixed left-4 top-0 z-50 -translate-y-full rounded-b-md bg-action px-4 py-3 font-semibold text-white focus:translate-y-0 focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2"
        >
          Skip to content
        </a>
        <div className="flex min-h-screen flex-col">
          <header className="border-b border-line bg-surface/90">
            <div className="mx-auto flex w-full max-w-7xl items-center justify-between px-5 py-5 sm:px-8">
              <Link
                href="/"
                className="rounded-sm font-reading text-2xl font-semibold tracking-tight text-ink focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-4"
              >
                OpenActs
              </Link>
              <p className="hidden text-sm text-muted sm:block">
                Nigerian legislation, directly readable
              </p>
            </div>
          </header>
          <div className="flex-1">{children}</div>
          <footer className="border-t border-line bg-surface">
            <div className="mx-auto flex w-full max-w-7xl flex-col gap-2 px-5 py-6 text-xs text-muted sm:px-8 md:flex-row md:items-center md:justify-between">
              <p>Open, source-backed Nigerian legislation.</p>
              <Suspense fallback={<p>Loading release identity…</p>}>
                <ReleaseIdentity />
              </Suspense>
            </div>
          </footer>
        </div>
      </body>
    </html>
  );
}

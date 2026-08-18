import Link from "next/link";

export default function Home() {
  return (
    <main id="main-content">
      <section className="mx-auto grid w-full max-w-7xl gap-12 px-5 py-20 sm:px-8 sm:py-28 lg:grid-cols-[minmax(0,48rem)_1fr] lg:items-end lg:py-36">
        <div>
          <p className="mb-6 text-sm font-semibold uppercase tracking-[0.16em] text-action">
            Nigerian law, structured and source-backed
          </p>
          <h1 className="max-w-3xl text-balance font-reading text-5xl font-medium leading-[0.98] tracking-[-0.035em] text-ink sm:text-7xl">
            Read the law at the provision.
          </h1>
          <p className="mt-8 max-w-2xl text-lg leading-8 text-muted sm:text-xl sm:leading-9">
            OpenActs turns published Acts into exact, navigable legal text with
            permanent links and a visible trail back to each Source.
          </p>
        </div>
        <aside className="border-l-2 border-action pl-6 lg:mb-2">
          <p className="text-sm font-semibold text-ink">Reader foundation</p>
          <p className="mt-2 max-w-sm text-sm leading-6 text-muted">
            Act browsing and private exact-reference search are the next
            implementation slice.
          </p>
          <Link
            href="/acts"
            className="mt-4 inline-block border-b border-b-action pb-1 text-sm font-semibold text-action"
          >
            Open Act index →
          </Link>
        </aside>
      </section>
    </main>
  );
}

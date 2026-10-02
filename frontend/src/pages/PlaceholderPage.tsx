import type { Page } from "../pages";

/** A page whose task hasn't been built yet. */
export function PlaceholderPage({ page }: { page: Page }) {
  return (
    <section>
      <title>{`${page.title} | Work Journal`}</title>
      <h1 className="text-2xl font-semibold">{page.title}</h1>
      <p className="mt-2 text-stone-600">{page.summary}</p>
      <p className="mt-6 rounded-md border border-dashed border-stone-300 p-4 text-sm text-stone-500">
        This page arrives with {page.task}.
      </p>
    </section>
  );
}

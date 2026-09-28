import { Link } from "react-router";

export function NotFound() {
  return (
    <section>
      <title>Page not found · Work Journal</title>
      <h1 className="text-2xl font-semibold">Page not found</h1>
      <p className="mt-2 text-stone-600">
        There&apos;s no page at this address.{" "}
        <Link
          to="/inbox"
          className="text-emerald-800 underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700"
        >
          Go to the inbox
        </Link>
        .
      </p>
    </section>
  );
}

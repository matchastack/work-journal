import { Navigate } from "react-router";

import { useMe } from "../api/auth";
import { Layout } from "./Layout";

/** Shows the page in the layout to someone signed in, and the sign-in page to anyone else. */
export function RequireSignIn() {
  const me = useMe();
  if (me.isPending) {
    return (
      <p role="status" className="p-8 text-stone-600">
        Loading...
      </p>
    );
  }
  if (me.isError) {
    return (
      <div role="alert" className="mx-auto max-w-md p-8">
        <h1 className="text-lg font-semibold">The app can&apos;t reach its server</h1>
        <p className="mt-2 text-stone-600">Check your connection, then try again.</p>
        <button
          type="button"
          onClick={() => void me.refetch()}
          className="mt-4 rounded border border-stone-300 px-3 py-1.5 hover:bg-stone-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700"
        >
          Try again
        </button>
      </div>
    );
  }
  if (me.data === null) {
    return <Navigate to="/sign-in" replace />;
  }
  return <Layout me={me.data} />;
}

import { Navigate } from "react-router";

import { useMe } from "../api/auth";

export function SignIn() {
  const me = useMe();
  if (me.data) {
    return <Navigate to="/" replace />;
  }
  return (
    <main className="grid min-h-screen place-items-center bg-stone-50 px-4 text-stone-900">
      <title>Sign in · Work Journal</title>
      <div className="w-full max-w-sm rounded-lg border border-stone-200 bg-white p-8 shadow-sm">
        <h1 className="text-xl font-semibold text-emerald-800">Work Journal</h1>
        <p className="mt-2 text-sm text-stone-600">
          Sign in to review your journal, profile and resumes.
        </p>
        {/* A full page load: the server sends the browser on to GitHub. */}
        <a
          href="/auth/login"
          className="mt-6 flex w-full justify-center rounded-md bg-stone-900 px-4 py-2.5 text-sm font-medium text-white hover:bg-stone-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700"
        >
          Sign in with GitHub
        </a>
      </div>
    </main>
  );
}

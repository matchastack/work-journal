import { NavLink, Outlet } from "react-router";

import { useSignOut, type Me } from "../api/auth";
import { PAGES } from "../pages";

const FOCUS = "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700";

/** The frame around every page once someone has signed in. */
export function Layout({ me }: { me: Me }) {
  const signOut = useSignOut();
  return (
    <div className="min-h-screen bg-stone-50 text-stone-900">
      <a
        href="#main"
        className={`sr-only rounded bg-white px-3 py-2 focus:not-sr-only focus:absolute focus:top-3 focus:left-3 ${FOCUS}`}
      >
        Skip to content
      </a>
      <header className="border-b border-stone-200 bg-white">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3">
          <span className="font-semibold text-emerald-800">Work Journal</span>
          <nav aria-label="Main" className="order-last w-full md:order-none md:w-auto">
            <ul className="flex flex-wrap gap-1">
              {PAGES.map((page) => (
                <li key={page.path}>
                  <NavLink
                    to={`/${page.path}`}
                    className={`block rounded px-3 py-1.5 text-sm text-stone-700 hover:bg-stone-100 aria-[current=page]:bg-emerald-50 aria-[current=page]:font-medium aria-[current=page]:text-emerald-800 ${FOCUS}`}
                  >
                    {page.title}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm">
            <span className="text-stone-600">
              {me.githubLogin === null ? "Signed in" : `@${me.githubLogin}`}
            </span>
            <button
              type="button"
              onClick={() => {
                signOut.mutate();
              }}
              disabled={signOut.isPending}
              className={`rounded border border-stone-300 px-3 py-1.5 hover:bg-stone-100 disabled:opacity-50 ${FOCUS}`}
            >
              Sign out
            </button>
          </div>
        </div>
      </header>
      {signOut.isError && (
        <p role="alert" className="mx-auto max-w-6xl px-4 pt-4 text-sm text-red-700">
          Signing out didn&apos;t work. Try again.
        </p>
      )}
      <main id="main" tabIndex={-1} className="mx-auto max-w-6xl px-4 py-8 focus:outline-none">
        <Outlet />
      </main>
    </div>
  );
}

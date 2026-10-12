import type { ReactNode } from "react";
import { Navigate, type RouteObject } from "react-router";

import { RequireSignIn } from "./components/RequireSignIn";
import { PAGES, type Page } from "./pages";
import { Journal } from "./pages/Journal";
import { NotFound } from "./pages/NotFound";
import { PlaceholderPage } from "./pages/PlaceholderPage";
import { SignIn } from "./pages/SignIn";

/** A page built so far, or what its task will add. */
function pageElement(page: Page): ReactNode {
  switch (page.path) {
    case "journal":
      return <Journal />;
    default:
      return <PlaceholderPage page={page} />;
  }
}

export const routes: RouteObject[] = [
  { path: "/sign-in", Component: SignIn },
  {
    path: "/",
    Component: RequireSignIn,
    children: [
      { index: true, element: <Navigate to="/inbox" replace /> },
      ...PAGES.map((page) => ({ path: page.path, element: pageElement(page) })),
      { path: "*", Component: NotFound },
    ],
  },
];

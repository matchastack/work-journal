import type { ReactNode } from "react";
import { Navigate, type RouteObject } from "react-router";

import { RequireSignIn } from "./components/RequireSignIn";
import { PAGES, type Page } from "./pages";
import { Inbox } from "./pages/Inbox";
import { Journal } from "./pages/Journal";
import { NotFound } from "./pages/NotFound";
import { PlaceholderPage } from "./pages/PlaceholderPage";
import { Portfolio } from "./pages/Portfolio";
import { Profile } from "./pages/Profile";
import { Resumes } from "./pages/Resumes";
import { Settings } from "./pages/Settings";
import { SignIn } from "./pages/SignIn";
import { Tailor } from "./pages/Tailor";

/** A page built so far, or what its task will add. */
function pageElement(page: Page): ReactNode {
  switch (page.path) {
    case "inbox":
      return <Inbox />;
    case "journal":
      return <Journal />;
    case "profile":
      return <Profile />;
    case "resumes":
      return <Resumes />;
    case "tailor":
      return <Tailor />;
    case "portfolio":
      return <Portfolio />;
    case "settings":
      return <Settings />;
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

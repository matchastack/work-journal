import { Navigate, type RouteObject } from "react-router";

import { RequireSignIn } from "./components/RequireSignIn";
import { PAGES } from "./pages";
import { NotFound } from "./pages/NotFound";
import { PlaceholderPage } from "./pages/PlaceholderPage";
import { SignIn } from "./pages/SignIn";

export const routes: RouteObject[] = [
  { path: "/sign-in", Component: SignIn },
  {
    path: "/",
    Component: RequireSignIn,
    children: [
      { index: true, element: <Navigate to="/inbox" replace /> },
      ...PAGES.map((page) => ({ path: page.path, element: <PlaceholderPage page={page} /> })),
      { path: "*", Component: NotFound },
    ],
  },
];

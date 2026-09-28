# Portfolio template

`wj portfolio build --out <dir>` fills `index.html` with the profile's public content (`app/portfolio/build.py`). It writes the page with `static/portfolio.css`, `static/portfolio.js` and any resumes passed with `--resume`.

- **Template:** Jinja with autoescaping. Links go through the `url` filter, which allows only web and email links. The layout follows the owner's current site: a blue-to-purple accent, cards, and project tabs. There is a tab for each role type that a project is kept for; projects without keep-for tags show under "All projects".
- **CSS:** Tailwind's standalone CLI builds it, so no Node is needed. After changing `index.html` or `portfolio.js`, run `scripts/build-portfolio-css.sh` from `backend/` and commit `static/portfolio.css`. CI checks that the committed file matches a fresh build.
- **JavaScript:** the page works without it. The script adds the mobile menu and the project filters.
- **Checks:** `tests/test_portfolio_browser.py` runs axe-core at desktop width and at 360 px, and exercises the menu and the filters in Chromium.

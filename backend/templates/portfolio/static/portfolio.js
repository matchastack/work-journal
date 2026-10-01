// The page works without JavaScript. This adds the mobile menu and the project filters.
document.addEventListener("DOMContentLoaded", () => {
  const menuButton = document.querySelector("[data-menu-button]");
  const menu = document.getElementById("mobile-menu");
  if (menuButton && menu) {
    const setOpen = (open) => {
      menuButton.setAttribute("aria-expanded", String(open));
      menu.hidden = !open;
    };
    menuButton.hidden = false;
    menuButton.addEventListener("click", () => {
      setOpen(menuButton.getAttribute("aria-expanded") !== "true");
    });
    menu.querySelectorAll("a").forEach((link) => {
      link.addEventListener("click", () => setOpen(false));
    });
  }

  const filters = document.getElementById("project-filters");
  if (filters) {
    const buttons = filters.querySelectorAll("[data-project-filter]");
    const projects = document.querySelectorAll("[data-project]");
    filters.hidden = false;
    buttons.forEach((button) => {
      button.addEventListener("click", () => {
        const category = button.dataset.projectFilter;
        buttons.forEach((other) => other.setAttribute("aria-pressed", String(other === button)));
        projects.forEach((project) => {
          const categories = project.dataset.categories.split(" ");
          project.hidden = category !== "all" && !categories.includes(category);
        });
      });
    });
  }
});

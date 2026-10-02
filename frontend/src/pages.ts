/** A page of the web app, and the backlog task that builds it. */
export interface Page {
  path: string;
  title: string;
  summary: string;
  task: string;
}

/** Every page, in the order the navigation lists them. */
export const PAGES: readonly Page[] = [
  {
    path: "inbox",
    title: "Inbox",
    summary: "Review the changes proposed from your journal, and accept or reject them.",
    task: "T-040",
  },
  {
    path: "journal",
    title: "Journal",
    summary: "Everything you've sent the bot, as one document you can edit and export.",
    task: "T-041",
  },
  {
    path: "profile",
    title: "Profile",
    summary: "Your roles, projects and skills, with every earlier version.",
    task: "T-042",
  },
  {
    path: "resumes",
    title: "Resumes",
    summary: "Preview and download your resume variants.",
    task: "T-044",
  },
  {
    path: "tailor",
    title: "Tailor",
    summary: "Tailor a resume to a job posting, and see where you've applied.",
    task: "T-045",
  },
  {
    path: "portfolio",
    title: "Portfolio",
    summary: "Choose what your public portfolio page shows, and publish it.",
    task: "T-046",
  },
  {
    path: "linkedin",
    title: "LinkedIn",
    summary: "Copy the updated sections into your LinkedIn profile.",
    task: "T-047",
  },
  {
    path: "settings",
    title: "Settings",
    summary: "Telegram, reminders, style notes and the rest.",
    task: "T-048",
  },
];

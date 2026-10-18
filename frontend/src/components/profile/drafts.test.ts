import { describe, expect, it } from "vitest";

import type { Basics, Bullet, Project, Role } from "../../api/profile";
import {
  basicsDraft,
  basicsEditOf,
  bulletDraft,
  bulletEditOf,
  commaList,
  datesProblem,
  orNull,
  projectDraft,
  projectEditOf,
  roleDraft,
  roleEditOf,
  savedMessage,
  tagsProblem,
} from "./drafts";

const BULLET: Bullet = {
  id: "nw_export",
  text: "Cut the nightly export job from 50 to 12 minutes.",
  strength: "high",
  strengthOverrides: [],
  verification: "partial",
  verificationNote: "Numbers from the job logs.",
  swaps: [],
  needs: [],
  notes: [],
  status: "active",
  statusReason: null,
  tags: ["backend", "data"],
  priority: 2,
  visibility: "public",
  sources: [],
};

const ROLE: Role = {
  id: "northwind",
  name: "Northwind Traders",
  position: "Engineer",
  titleVariants: [],
  location: "Springfield",
  startDate: "2025-03",
  endDate: null,
  highlights: [BULLET],
  loadBearing: true,
  duringEducation: false,
  keepFor: [],
  cutFor: [],
  sensitivity: "normal",
  honestyBoundaries: [],
  priority: 1,
  notes: [],
};

describe("small helpers", () => {
  it("clear blank text and split comma lists", () => {
    expect(orNull("  ")).toBeNull();
    expect(orNull(" Springfield ")).toBe("Springfield");
    expect(commaList(" backend, ,data ,")).toEqual(["backend", "data"]);
  });

  it("check months and their order", () => {
    expect(datesProblem("2025-03", "", { startRequired: true })).toBeNull();
    expect(datesProblem("", "", { startRequired: true })).toBe("It needs a start date.");
    expect(datesProblem("", "", { startRequired: false })).toBeNull();
    expect(datesProblem("2025-13", "", { startRequired: true })).toBe(
      "The start date needs to be a month, such as 2026-10.",
    );
    expect(datesProblem("2025-03", "March", { startRequired: true })).toBe(
      "The end date needs to be a month, such as 2026-10.",
    );
    expect(datesProblem("2025-03", "2025-01", { startRequired: true })).toBe(
      "The end date is before the start date.",
    );
    expect(datesProblem("2025-03", "2025-03", { startRequired: true })).toBeNull();
  });

  it("check tags", () => {
    expect(tagsProblem(["backend", "ml_ops", "front-end", "a1"])).toBeNull();
    expect(tagsProblem(["Backend"])).toBe(
      'The tag "Backend" needs to be lowercase letters, digits, - or _, such as backend.',
    );
    expect(tagsProblem(["-x"])).not.toBeNull();
    expect(tagsProblem(["x".repeat(33)])).not.toBeNull();
    expect(tagsProblem(Array.from({ length: 21 }, (_, index) => `t${String(index)}`))).toBe(
      "There can be at most 20 tags.",
    );
  });

  it("say whether a save made a new version", () => {
    expect(savedMessage(3, 2)).toBe("Saved as version 3.");
    expect(savedMessage(2, 2)).toBe("Nothing changed, so no new version was saved.");
  });
});

describe("a bullet's edit", () => {
  it("sends every field, with blanks as null", () => {
    const draft = { ...bulletDraft(BULLET), verificationNote: "  " };
    expect(bulletEditOf(draft, 4)).toEqual({
      edit: {
        baseVersion: 4,
        text: BULLET.text,
        status: "active",
        statusReason: null,
        strength: "high",
        verification: "partial",
        verificationNote: null,
        tags: ["backend", "data"],
      },
    });
  });

  it("needs text, and a reason to bench it", () => {
    const draft = bulletDraft(BULLET);
    expect(bulletEditOf({ ...draft, text: " " }, 1)).toEqual({
      problem: "The bullet needs text.",
    });
    expect(bulletEditOf({ ...draft, status: "benched" }, 1)).toEqual({
      problem: "Say why the bullet is benched.",
    });
    const benched = bulletEditOf({ ...draft, status: "benched", statusReason: " Old work " }, 1);
    expect(benched).toMatchObject({ edit: { status: "benched", statusReason: "Old work" } });
  });

  it("keeps a reason only with a benched or planned status", () => {
    const draft = { ...bulletDraft(BULLET), statusReason: "Not yet" };
    expect(bulletEditOf(draft, 1)).toMatchObject({ edit: { statusReason: null } });
    expect(bulletEditOf({ ...draft, status: "planned" }, 1)).toMatchObject({
      edit: { status: "planned", statusReason: "Not yet" },
    });
    expect(bulletEditOf({ ...draft, status: "planned", statusReason: "" }, 1)).toMatchObject({
      edit: { statusReason: null },
    });
  });

  it("checks its tags", () => {
    expect(bulletEditOf({ ...bulletDraft(BULLET), tags: "backend, Data" }, 1)).toEqual({
      problem: 'The tag "Data" needs to be lowercase letters, digits, - or _, such as backend.',
    });
  });
});

describe("the basics' edit", () => {
  const basics: Basics = {
    name: "Casey Morgan",
    label: "Software Engineer",
    email: "casey@example.com",
    emailVisibility: "public",
    phoneVisibility: "resume_only",
    locationVisibility: "public",
    location: { city: "Springfield", countryCode: "US" },
    profiles: [{ network: "GitHub", username: "casey-example", url: null }],
  };

  it("clears blank fields, and a location with nothing in it", () => {
    const draft = { ...basicsDraft(basics), label: " ", city: "", countryCode: "" };
    expect(basicsEditOf(draft, 2)).toEqual({
      edit: {
        baseVersion: 2,
        name: "Casey Morgan",
        label: null,
        email: "casey@example.com",
        phone: null,
        url: null,
        summary: null,
        location: null,
        profiles: [{ network: "GitHub", username: "casey-example", url: null }],
      },
    });
    expect(basicsEditOf({ ...draft, region: "Northshire" }, 2)).toMatchObject({
      edit: { location: { city: null, region: "Northshire", countryCode: null } },
    });
  });

  it("needs a name, and a network for each profile", () => {
    const draft = basicsDraft(basics);
    expect(basicsEditOf({ ...draft, name: "" }, 1)).toEqual({
      problem: "Your basics need your name.",
    });
    const blank = { key: 9, network: " ", username: "", url: "https://example.com" };
    expect(basicsEditOf({ ...draft, profiles: [...draft.profiles, blank] }, 1)).toEqual({
      problem: "Profile 2 needs a network, such as GitHub.",
    });
  });
});

describe("a role's edit", () => {
  it("leaves the end date blank while it's current", () => {
    expect(roleEditOf(roleDraft(ROLE), 1)).toEqual({
      edit: {
        baseVersion: 1,
        name: "Northwind Traders",
        position: "Engineer",
        location: "Springfield",
        url: null,
        startDate: "2025-03",
        endDate: null,
        summary: null,
      },
    });
  });

  it("needs an organisation, a title and dates in order", () => {
    const draft = roleDraft(ROLE);
    expect(roleEditOf({ ...draft, name: "" }, 1)).toEqual({
      problem: "The role needs an organisation.",
    });
    expect(roleEditOf({ ...draft, position: " " }, 1)).toEqual({
      problem: "The role needs a title.",
    });
    expect(roleEditOf({ ...draft, endDate: "2024-12" }, 1)).toEqual({
      problem: "The end date is before the start date.",
    });
  });
});

describe("a project's edit", () => {
  const project: Project = {
    id: "bird_calls",
    name: "Bird Call Classifier",
    keywords: ["Python", "PyTorch"],
    highlights: [],
    status: "planned",
    statusReason: null,
    keepFor: [],
    cutFor: [],
    tags: ["ml"],
    priority: 3,
    notes: [],
  };

  it("sends its tech stack and status", () => {
    const draft = { ...projectDraft(project), keywords: "Python, PyTorch, ONNX" };
    expect(projectEditOf(draft, 5)).toEqual({
      edit: {
        baseVersion: 5,
        name: "Bird Call Classifier",
        description: null,
        keywords: ["Python", "PyTorch", "ONNX"],
        url: null,
        startDate: null,
        endDate: null,
        status: "planned",
        statusReason: null,
        tags: ["ml"],
      },
    });
  });

  it("needs a reason to bench it, and dates in order", () => {
    const draft = projectDraft(project);
    expect(projectEditOf({ ...draft, status: "benched" }, 1)).toEqual({
      problem: "Say why the project is benched.",
    });
    expect(projectEditOf({ ...draft, startDate: "2026-05", endDate: "2026-01" }, 1)).toEqual({
      problem: "The end date is before the start date.",
    });
  });
});

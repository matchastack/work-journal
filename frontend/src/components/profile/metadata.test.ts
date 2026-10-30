import { describe, expect, it } from "vitest";

import type { OpenQuestion, Profile, Role } from "../../api/profile";
import {
  courseworkEditOf,
  lineList,
  presetDrafts,
  presetsEditOf,
  questionDraft,
  questionEditOf,
  roleRulesDraft,
  roleRulesEditOf,
  roleTypesEditOf,
  skillsDraft,
  skillsEditOf,
  summariesEditOf,
  swapsEditOf,
} from "./metadata";

const ROLE: Role = {
  id: "northwind",
  name: "Northwind Traders",
  position: "Engineer",
  titleVariants: ["Software Engineer", "Backend Engineer"],
  location: null,
  url: null,
  startDate: "2025-03",
  endDate: null,
  summary: null,
  highlights: [],
  loadBearing: true,
  duringEducation: false,
  keepFor: ["backend"],
  cutFor: [],
  sensitivity: "sensitive",
  honestyBoundaries: ["Did not own the deployment pipeline."],
  priority: 1,
  notes: [],
};

function profile(fields: Partial<Profile> = {}): Profile {
  return {
    basics: {
      name: "Casey Morgan",
      label: null,
      email: null,
      emailVisibility: "public",
      phone: null,
      phoneVisibility: "resume_only",
      url: null,
      location: null,
      locationVisibility: "public",
      profiles: [],
    },
    roleTypes: [{ id: "backend", name: "Backend / full stack" }],
    work: [ROLE],
    education: [],
    projects: [],
    skills: [
      {
        name: "Python",
        category: "Languages",
        tier: "strong",
        verifyBeforeShipping: false,
        aliases: [],
        notes: [],
      },
    ],
    skillGaps: ["Rust"],
    skillPresets: [
      {
        roleType: "backend",
        lines: [
          { label: "Languages", skills: ["Python", "SQL"] },
          { label: "Backend & APIs", skills: ["FastAPI"] },
        ],
      },
    ],
    summaries: [],
    openQuestions: [],
    ...fields,
  };
}

describe("a role's tailoring rules", () => {
  it("send a line each for sentences and commas for role types", () => {
    const draft = {
      ...roleRulesDraft(ROLE),
      titleVariants: "Software Engineer\n\n  Platform Engineer  ",
      cutFor: "data",
    };
    expect(roleRulesEditOf(draft, 4)).toEqual({
      edit: {
        baseVersion: 4,
        titleVariants: ["Software Engineer", "Platform Engineer"],
        loadBearing: true,
        duringEducation: false,
        keepFor: ["backend"],
        cutFor: ["data"],
        sensitivity: "sensitive",
        honestyBoundaries: ["Did not own the deployment pipeline."],
      },
    });
  });

  it("keep or cut a role type, not both", () => {
    const draft = { ...roleRulesDraft(ROLE), cutFor: "backend" };
    expect(roleRulesEditOf(draft, 1)).toEqual({
      problem: "The role type backend can't be both kept and cut.",
    });
    expect(roleRulesEditOf({ ...draft, cutFor: "Data Eng" }, 1)).toHaveProperty("problem");
  });
});

describe("a bullet's swaps", () => {
  it("need their phrasing, and a blank part replaced is the whole bullet", () => {
    const swap = {
      key: 0,
      text: " ",
      replaces: "",
      contexts: "",
      savedContexts: [],
      origin: "approved" as const,
    };
    expect(swapsEditOf({ swaps: [swap] }, 1)).toEqual({ problem: "Swap 1 needs its phrasing." });
    const filled = { ...swap, text: "high-volume services", contexts: "platform roles\nbackend" };
    expect(swapsEditOf({ swaps: [filled] }, 1)).toEqual({
      edit: {
        baseVersion: 1,
        swaps: [
          {
            text: "high-volume services",
            replaces: null,
            contexts: ["platform roles", "backend"],
            origin: "approved",
          },
        ],
      },
    });
  });
});

describe("coursework", () => {
  it("keeps a role type's courses in their order and drops removed ones", () => {
    const draft = {
      courses: "Algorithms\nDatabases",
      subsets: [{ key: 0, roleType: "backend", courses: ["Databases", "Compilers", "Algorithms"] }],
      rules: "Never print the GPA.",
    };
    expect(courseworkEditOf(draft, 2)).toEqual({
      edit: {
        baseVersion: 2,
        courses: ["Algorithms", "Databases"],
        courseworkSubsets: [{ roleType: "backend", courses: ["Databases", "Algorithms"] }],
        rules: ["Never print the GPA."],
      },
    });
  });

  it("needs a role type and a course for each coursework for a role type", () => {
    const subset = { key: 0, roleType: "", courses: ["Algorithms"] };
    const draft = { courses: "Algorithms", subsets: [subset], rules: "" };
    expect(courseworkEditOf(draft, 1)).toHaveProperty("problem");
    const none = { ...draft, subsets: [{ ...subset, roleType: "backend", courses: ["Gone"] }] };
    expect(courseworkEditOf(none, 1)).toEqual({
      problem: "Choose at least one course for backend, or remove it.",
    });
    expect(courseworkEditOf({ ...draft, courses: "A\nA", subsets: [] }, 1)).toEqual({
      problem: "A is on the coursework list twice.",
    });
  });
});

describe("role types", () => {
  it("need an ID like a tag, a name, and no repeats", () => {
    const type = { key: 0, id: "ml", name: "Machine learning", saved: false };
    expect(roleTypesEditOf({ roleTypes: [type] }, 1)).toEqual({
      edit: { baseVersion: 1, roleTypes: [{ id: "ml", name: "Machine learning" }] },
    });
    expect(roleTypesEditOf({ roleTypes: [{ ...type, id: "Machine Learning" }] }, 1)).toHaveProperty(
      "problem",
    );
    expect(roleTypesEditOf({ roleTypes: [type, { ...type, key: 1 }] }, 1)).toEqual({
      problem: "The role type ml is listed twice.",
    });
  });
});

describe("skills", () => {
  it("keep each skill's earlier name, so its notes stay with it", () => {
    const draft = skillsDraft(profile());
    const [python] = draft.skills;
    if (python === undefined) {
      throw new Error("no skill");
    }
    const renamed = { ...python, name: "Python 3", tier: "" as const, aliases: "py, python3" };
    expect(skillsEditOf({ skills: [renamed], gaps: "Rust\nHaskell" }, 3)).toEqual({
      edit: {
        baseVersion: 3,
        skills: [
          {
            name: "Python 3",
            previousName: "Python",
            category: "Languages",
            tier: null,
            verifyBeforeShipping: false,
            aliases: ["py", "python3"],
          },
        ],
        skillGaps: ["Rust", "Haskell"],
      },
    });
  });

  it("never lists a gap as a skill, whatever its case", () => {
    expect(skillsEditOf({ ...skillsDraft(profile()), gaps: "python" }, 1)).toEqual({
      problem: "python can't be both a skill and a gap.",
    });
  });
});

describe("skills presets and summaries", () => {
  it("read a preset's lines as a label and its skills", () => {
    const drafts = presetDrafts(profile());
    expect(drafts[0]?.lines).toBe("Languages: Python, SQL\nBackend & APIs: FastAPI");
    expect(presetsEditOf({ presets: drafts }, 1)).toEqual({
      edit: { baseVersion: 1, skillPresets: profile().skillPresets },
    });
    const wrong = [{ key: 0, roleType: "backend", lines: "Python, SQL", savedLines: null }];
    expect(presetsEditOf({ presets: wrong }, 1)).toEqual({
      problem:
        '"Python, SQL" in the backend preset needs a label and skills, such as Languages: Python, SQL.',
    });
  });

  it("have one summary for each role type", () => {
    const summary = { key: 0, roleType: "backend", text: "Builds services." };
    expect(summariesEditOf({ summaries: [summary, { ...summary, key: 1 }] }, 1)).toEqual({
      problem: "There's more than one summary for backend.",
    });
    expect(summariesEditOf({ summaries: [{ ...summary, text: " " }] }, 1)).toEqual({
      problem: "The backend summary needs its text.",
    });
  });
});

describe("open questions", () => {
  it("start from the bullets they block", () => {
    const question: OpenQuestion = {
      id: "q_volume",
      question: "How many events a day?",
      status: "open",
      answerFactIds: [],
    };
    const withBullet = profile({
      work: [
        {
          ...ROLE,
          highlights: [
            {
              id: "nw_orders",
              text: "Built order services.",
              strength: "high",
              strengthOverrides: [],
              verification: "yes",
              verificationNote: null,
              swaps: [],
              needs: ["q_volume"],
              notes: [],
              status: "active",
              statusReason: null,
              tags: [],
              priority: 2,
              visibility: "public",
              sources: [],
            },
          ],
        },
      ],
    });
    const draft = questionDraft(question, withBullet);
    expect(draft).toEqual({
      question: "How many events a day?",
      status: "open",
      bulletIds: ["nw_orders"],
    });
    expect(questionEditOf({ ...draft, status: "answered" }, 2)).toEqual({
      edit: {
        baseVersion: 2,
        question: "How many events a day?",
        status: "answered",
        bulletIds: ["nw_orders"],
      },
    });
    expect(questionEditOf(questionDraft(null, withBullet), 2)).toEqual({
      problem: "The open question needs its question.",
    });
  });
});

describe("lists the forms didn't change", () => {
  it("are sent as they were saved, commas and all", () => {
    const contexts = ["Software Engineer, Backend at Example Corp"];
    const swap = { key: 0, text: "x", replaces: "", origin: "approved" as const };
    const untouched = { ...swap, contexts: contexts.join("\n"), savedContexts: contexts };
    expect(swapsEditOf({ swaps: [untouched] }, 1)).toMatchObject({
      edit: { swaps: [{ contexts }] },
    });
    const [python] = skillsDraft(profile()).skills;
    if (python === undefined) {
      throw new Error("no skill");
    }
    const aliases = ["Python 3, CPython"];
    const skill = { ...python, aliases: aliases.join(", "), savedAliases: aliases };
    expect(skillsEditOf({ skills: [skill], gaps: "" }, 1)).toMatchObject({
      edit: { skills: [{ aliases }] },
    });
    const lines = [{ label: "Data", skills: ["SQL (Postgres, MySQL)", "dbt, Core"] }];
    const presets = presetDrafts(profile({ skillPresets: [{ roleType: "backend", lines }] }));
    expect(presetsEditOf({ presets }, 1)).toEqual({
      edit: { baseVersion: 1, skillPresets: [{ roleType: "backend", lines }] },
    });
  });
});

describe("line lists", () => {
  it("drop blank lines and trim the rest", () => {
    expect(lineList(" a \n\n b\n")).toEqual(["a", "b"]);
  });
});

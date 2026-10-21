export const meta = {
  name: "resume-rewrite",
  description: "Inventory a resume, collect real numbers, rewrite each role, then humanize for one job",
  whenToUse: "Rewrite resume bullets without inventing metrics. args.resume is the file path. Optional args.job is the target posting. Optional args.answers holds numbers the user already supplied.",
  phases: [
    { title: "Inventory", detail: "Read the resume and list one question per missing number" },
    { title: "Rewrite", detail: "One pass per role, using only the user's answers" },
    { title: "Humanize", detail: "Plain language, then reject anything invented" },
  ],
};

const inventorySchema = {
  type: "object",
  required: ["roles", "questions"],
  properties: {
    roles: {
      type: "array",
      maxItems: 4,
      items: {
        type: "object",
        required: ["title", "company", "bullets"],
        properties: {
          title: { type: "string" },
          company: { type: "string" },
          bullets: { type: "array", maxItems: 8, items: { type: "string" } },
        },
      },
    },
    questions: { type: "array", maxItems: 12, items: { type: "string" } },
  },
};

const roleSchema = {
  type: "object",
  required: ["title", "company", "bullets"],
  properties: {
    title: { type: "string" },
    company: { type: "string" },
    bullets: { type: "array", maxItems: 8, items: { type: "string" } },
  },
};

const draftSchema = {
  type: "object",
  required: ["resume", "gaps"],
  properties: {
    resume: { type: "string" },
    gaps: { type: "array", maxItems: 12, items: { type: "string" } },
  },
};

const checkSchema = {
  type: "object",
  required: ["clean", "invented"],
  properties: {
    clean: { type: "boolean" },
    invented: { type: "array", maxItems: 12, items: { type: "string" } },
  },
};

let input = args;
if (typeof input === "string") {
  try {
    input = JSON.parse(input);
  } catch {
    input = {};
  }
}
input = input || {};
const resumePath = input.resume;
if (typeof resumePath !== "string" || resumePath.trim() === "") {
  log("resume-rewrite needs args.resume, a path to the resume file.");
  return { started: false, reason: "no-resume" };
}
const jobText = typeof input.job === "string" ? input.job : "";
const answers = typeof input.answers === "string" ? input.answers : "";

phase("Inventory");
const inventory = await agent(
  "Read the resume with the Read tool before you answer. Path: " + resumePath +
    ". Do not rewrite yet. Do not invent projects, tools, employers, dates, or numbers. " +
    "If the file is LaTeX, ignore the preamble and read the experience items. " +
    "Return at most 4 roles with title, company, and the current bullets. " +
    "For each bullet that sounds measurable but has no number, add one question about scale, before and after, time, or how often. " +
    "An empty questions list is valid.",
  { label: "inventory", phase: "Inventory", schema: inventorySchema },
);

const roles = Array.isArray(inventory && inventory.roles) ? inventory.roles.slice(0, 4) : [];
const questions = Array.isArray(inventory && inventory.questions) ? inventory.questions : [];
if (roles.length === 0) {
  log("No roles found in " + resumePath);
  return { started: true, summary: "No roles found.", questions };
}
if ((inventory.roles || []).length > 4) {
  log("Kept 4 roles. Extra roles were left out of this run.");
}

phase("Rewrite");
const rewritten = await parallel(roles.map((role, index) => () => agent(
  "Rewrite only this one role. Read " + resumePath + " with the Read tool. " +
    "Also read data/resume-answers.md if that file exists. " +
    "Role JSON:\n" + JSON.stringify(role) +
    "\nNumbers the user already supplied:\n" + answers +
    "\nLead with what changed, then what they did, then the tool. Keep every fact, date, and number. " +
    "If a result has no number in the resume or the answers, write [metric] and do not invent one. " +
    "No spearheaded, leveraged, robust, or synergy. Two lines at most per bullet.",
  { label: "rewrite:" + (index + 1), phase: "Rewrite", schema: roleSchema },
)));

const kept = rewritten.filter((item) => item && Array.isArray(item.bullets));
if (kept.length === 0) {
  return { started: true, summary: "Rewrite produced no bullets.", questions };
}

phase("Humanize");
const draft = await agent(
  "Read " + resumePath + " with the Read tool. Combine these rewritten roles into one experience section.\n" +
    JSON.stringify(kept) +
    "\nTarget job, empty if none:\n" + jobText +
    "\nReorder and rephrase so real work matches the job's words only where that is honest. " +
    "Do not change employers, titles, or dates. Do not add a skill that is not in the resume. " +
    "Where the job asks for something the resume does not show, add it to gaps. " +
    "Then make the prose plain: vary sentence length, remove em dashes, remove not-X-but-Y contrasts, " +
    "remove chat leftovers, and do not make the text longer. Keep [metric] placeholders.",
  { label: "humanize", phase: "Humanize", schema: draftSchema },
);

const resumeText = draft && typeof draft.resume === "string" ? draft.resume : "";
const gaps = draft && Array.isArray(draft.gaps) ? draft.gaps : [];
const verdict = await agent(
  "Compare the original resume file " + resumePath + " with this rewrite. Read the file with the Read tool.\n" +
    "Rewritten roles:\n" + JSON.stringify(kept) +
    "\nDraft:\n" + resumeText +
    "\nUser-supplied numbers:\n" + answers +
    "\nSet clean to true only if every employer, title, date, tool, and number in the rewrite appears in the file or the user-supplied numbers, or is the placeholder [metric]. " +
    "If anything was added, set clean to false and list it in invented.",
  { label: "check-facts", phase: "Humanize", schema: checkSchema },
);

const clean = Boolean(verdict && verdict.clean === true);
const invented = verdict && Array.isArray(verdict.invented) ? verdict.invented : [];
log(clean ? "Fact check passed." : "Fact check did not pass. Do not paste the rewrite in yet.");
return { started: true, clean, resume: resumeText, gaps, invented, questions };

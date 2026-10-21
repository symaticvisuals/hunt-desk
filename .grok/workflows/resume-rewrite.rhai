let meta = #{
    name: "resume-rewrite",
    description: "Inventory a resume, collect real numbers, rewrite each role, then humanize for one job",
    when_to_use: "Rewrite resume bullets without inventing metrics. Pass args.resume as a file path. Optional args.job is the target posting. Optional args.answers skips the pause when the numbers are already known.",
    phases: [
        #{ title: "Inventory", detail: "Read the resume and list one question per missing number" },
        #{ title: "Rewrite", detail: "One pass per role, using only the user's answers" },
        #{ title: "Humanize", detail: "Plain language, then reject anything invented" },
    ],
};

let inventory_schema = #{
    "type": "object",
    "required": ["roles", "questions"],
    "properties": #{
        "roles": #{
            "type": "array",
            "maxItems": 4,
            "items": #{
                "type": "object",
                "required": ["title", "company", "bullets"],
                "properties": #{
                    "title": #{ "type": "string" },
                    "company": #{ "type": "string" },
                    "bullets": #{ "type": "array", "maxItems": 8, "items": #{ "type": "string" } },
                },
            },
        },
        "questions": #{ "type": "array", "maxItems": 12, "items": #{ "type": "string" } },
    },
};

let role_schema = #{
    "type": "object",
    "required": ["title", "company", "bullets"],
    "properties": #{
        "title": #{ "type": "string" },
        "company": #{ "type": "string" },
        "bullets": #{ "type": "array", "maxItems": 8, "items": #{ "type": "string" } },
    },
};

let draft_schema = #{
    "type": "object",
    "required": ["resume", "gaps"],
    "properties": #{
        "resume": #{ "type": "string" },
        "gaps": #{ "type": "array", "maxItems": 12, "items": #{ "type": "string" } },
    },
};

let check_schema = #{
    "type": "object",
    "required": ["clean", "invented"],
    "properties": #{
        "clean": #{ "type": "boolean" },
        "invented": #{ "type": "array", "maxItems": 12, "items": #{ "type": "string" } },
    },
};

let resume_path = if args == () { () } else { args.resume };
if resume_path == () {
    pause("verification", "Pass args.resume, the path to the resume file (.md or .tex). Optional args.job is the target posting. Optional args.answers holds the numbers if you already have them.");
}
let job_text = if args == () { () } else { args.job };
let answers = if args == () { () } else { args.answers };
if job_text == () { job_text = ""; }
if answers == () { answers = ""; }

phase("Inventory");
let inventory_prompt = "Read the resume file with read_file before you answer. Path: ";
inventory_prompt += resume_path;
inventory_prompt += "\nDo not rewrite yet. Do not invent projects, tools, employers, dates, or numbers. ";
inventory_prompt += "If the file is LaTeX, ignore the preamble and read the experience items. ";
inventory_prompt += "Return at most 4 roles. For each role return title, company, and the current bullets. ";
inventory_prompt += "For each bullet that sounds measurable but has no number, add one question to questions. ";
inventory_prompt += "A question asks about scale, before and after, time, or how often. ";
inventory_prompt += "If a bullet needs no number, do not invent a question for it. An empty questions list is valid.";
let inventory = agent(inventory_prompt, #{
    label: "inventory",
    capability_mode: "read-only",
    output_schema: inventory_schema,
});

let roles = [];
let questions = [];
if inventory != () && inventory.success && inventory.output != () {
    if inventory.output.roles != () {
        let seen = 0;
        for role in inventory.output.roles {
            if seen < 4 {
                roles.push(role);
            }
            seen += 1;
        }
        if seen > 4 {
            log("Kept 4 roles. Extra roles were left out of this run.");
        }
    }
    if inventory.output.questions != () {
        questions = inventory.output.questions;
    }
}

if roles.len() == 0 {
    complete(#{
        summary: "No roles found in the resume file.",
        questions: questions,
    });
}

if answers == "" {
    let note = "Write one answer per question into data/resume-answers.md, then resume this run. If you do not know a number, write unknown. Do not guess a percentage.";
    log(note);
    await_user("user", note);
}

phase("Rewrite");
let rewrite_jobs = [];
let index = 0;
for role in roles {
    index += 1;
    let prompt = "Rewrite only this one role. Read ";
    prompt += resume_path;
    prompt += " with read_file. Also read data/resume-answers.md with read_file if that file exists. ";
    prompt += "Use this role JSON as the scope, and ignore other roles:\n";
    prompt += json_encode(role);
    prompt += "\nNumbers the user already supplied:\n";
    prompt += answers;
    prompt += "\nLead with what changed, then what they did, then the tool. Keep every fact, date, and number. ";
    prompt += "If a result has no number in the resume or the answers, write [metric] and do not invent one. ";
    prompt += "No spearheaded, leveraged, robust, or synergy. Two lines at most per bullet. ";
    prompt += "Return title, company, and the rewritten bullets only.";
    rewrite_jobs.push(#{
        prompt: prompt,
        label: "rewrite:" + index.to_string(),
        capability_mode: "read-only",
        output_schema: role_schema,
    });
}
let rewritten = parallel(rewrite_jobs);

let kept = [];
for item in rewritten {
    if item != () && item.success && item.output != () && item.output.bullets != () {
        kept.push(item.output);
    }
}
if kept.len() == 0 {
    complete(#{
        summary: "Rewrite produced no bullets.",
        questions: questions,
    });
}

phase("Humanize");
let human_prompt = "Read ";
human_prompt += resume_path;
human_prompt += " with read_file. Combine these rewritten roles into one resume experience section.\n";
human_prompt += json_encode(kept);
human_prompt += "\nTarget job, empty if none:\n";
human_prompt += job_text;
human_prompt += "\nReorder and rephrase so real work matches the job's words only where that is honest. ";
human_prompt += "Do not change employers, titles, or dates. Do not add a skill that is not in the resume. ";
human_prompt += "Where the job asks for something the resume does not show, add it to gaps. ";
human_prompt += "Then make the prose plain: vary sentence length, remove em dashes, remove not-X-but-Y contrasts, ";
human_prompt += "remove chat leftovers, and do not make the text longer. Keep [metric] placeholders. ";
human_prompt += "Return the experience section as resume, and the honest gaps.";
let draft = agent(human_prompt, #{
    label: "humanize",
    capability_mode: "read-only",
    output_schema: draft_schema,
});

let resume_text = "";
let gaps = [];
if draft != () && draft.success && draft.output != () {
    if draft.output.resume != () { resume_text = draft.output.resume; }
    if draft.output.gaps != () { gaps = draft.output.gaps; }
}

let check_prompt = "Compare the original resume file ";
check_prompt += resume_path;
check_prompt += " with this rewrite. Read the file with read_file. Rewrite JSON follows.\n";
check_prompt += json_encode(kept);
check_prompt += "\nDraft:\n";
check_prompt += resume_text;
check_prompt += "\nUser-supplied numbers:\n";
check_prompt += answers;
check_prompt += "\nSet clean to true only if every employer, title, date, tool, and number in the rewrite appears in the file or the user-supplied numbers, or is the placeholder [metric]. ";
check_prompt += "If anything was added, set clean to false and list it in invented. A missing check is not a pass.";
let verdict = agent(check_prompt, #{
    label: "check-facts",
    capability_mode: "read-only",
    output_schema: check_schema,
});

let clean = false;
let invented = [];
if verdict != () && verdict.success && verdict.output != () && verdict.output.clean == true {
    clean = true;
}
if verdict != () && verdict.success && verdict.output != () && verdict.output.invented != () {
    invented = verdict.output.invented;
}

let report = "# Resume rewrite\n\n";
if clean {
    report += "Fact check passed. Review the bullets, fill any [metric], then paste them into the resume.\n\n";
} else {
    report += "Fact check did not pass. Do not paste this in until the invented lines are removed.\n\n";
}
report += resume_text;
report += "\n\n## Gaps\n";
if gaps.len() == 0 {
    report += "None listed.\n";
} else {
    for gap in gaps {
        report += "- ";
        report += gap;
        report += "\n";
    }
}
if invented.len() > 0 {
    report += "\n## Invented\n";
    for line in invented {
        report += "- ";
        report += line;
        report += "\n";
    }
}
let path = write_scratch_file("resume-rewrite.md", report);
complete(#{
    summary: if clean { "Rewrite passed the fact check." } else { "Rewrite failed the fact check." },
    clean: clean,
    path: path,
    questions: questions,
    gaps: gaps,
    invented: invented,
});

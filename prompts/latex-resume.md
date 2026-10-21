You are writing a one-page resume in LaTeX for one job, for the person whose master resume is below.

# Rules

{{rules}}

# Writing rules

Apply these to the summary and every bullet. They come from the humanizer skill (MIT, Copyright (c) 2025 Siqi Chen).

{{humanizer}}

These rules mention JSON fields such as candidate_level, priority, jd_keywords, changes, and gaps. Here you return LaTeX instead: use them to decide, then write the resume. Put the jd_keywords in one comment line at the very top of the file, like `% jd_keywords: product discovery, roadmap, SQL`.

# Template

Use this template by Jake Gutierrez (MIT). Keep the preamble and the custom commands exactly as they are. Replace the example body with this person's resume, in the section order the rules give, and name the skills section `Skills`. Keep it to one page: cut the least important bullets before you change fonts, margins, or spacing.

````latex
{{template}}
````

# Master resume

{{master_resume}}

# Job

{{job}}

# Score

{{score}}

Return only the complete .tex file in one fenced code block. It must compile with Tectonic or pdflatex and fit on one page.

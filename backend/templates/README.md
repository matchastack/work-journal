# Resume templates

Each folder here is a template: `resume.tex`, plus any files it needs, such as a class or style file. A variant names its template; the default is `default`.

## The default template

`default` is the owner's resume layout. It's Jake Gutierrez's template (MIT License, credited in the file) with a one-line subheading for roles. The preamble and macros are the owner's, unchanged; only the document body is generated. It has no personal content.

Rendering the profile imported from the owner's file gives the same pages as compiling that file, pixel for pixel (checked at 100 dpi). The one difference is by design: date ranges use an ASCII hyphen ("Aug 2021 - Jun 2025") instead of an en dash. If you change the template, compare it against the owner's file again.

## Writing a template

Templates are Jinja with delimiters that read naturally in LaTeX:

| Syntax | Use |
|---|---|
| `\VAR{selection.contact.name}` | Print a value. Every value is escaped, so `&`, `%` or `$` in the text print as written. |
| `\BLOCK{for role in selection.work}` … `\BLOCK{endfor}` | Statements: loops and conditions |
| `\#{ a note }` | A comment that doesn't reach the PDF |

A template receives:

- `selection`: the variant's content (`app/selection.py`): the contact details, summary, sections in order, education, work, projects and skills;
- `contact`: the contact line as (text, link) pairs: phone, email, location, website, then profiles;
- `dates(start, end)`, which gives "Mar 2024 - Present", the `month` filter, and the `url` filter for `\href{\VAR{link|url}}{...}`.

Printing a missing value is an error, so guard optional fields with `\BLOCK{if ...}`. Keep `\VAR`, `\BLOCK` and `\#{` out of LaTeX comments too: Jinja reads the whole file.

Whitespace matters to LaTeX: a blank line ends a paragraph, and whether a list starts after one changes the spacing above it. A line holding only a `\BLOCK{...}` disappears from the output, so the generated LaTeX keeps the template's line structure.

## Compiling

`wj render` compiles with `latexmk -pdf` (pdfLaTeX) in a temporary folder, with shell escape off, file access limited to that folder, and a 30-second timeout. Install TeX Live first:

```sh
sudo apt-get install --no-install-recommends latexmk texlive-latex-base texlive-latex-extra texlive-fonts-recommended cm-super
```

On macOS, install MacTeX (`brew install --cask mactex-no-gui`), which includes all of these. CI installs the same packages:

| Package | Provides |
|---|---|
| `texlive-latex-base` | pdfLaTeX, `hyperref`, `fancyhdr`, `tabularx`, `babel`, `latexsym` |
| `texlive-latex-extra` | `enumitem`, `titlesec`, `fullpage` |
| `texlive-fonts-recommended` | `marvosym` |
| `cm-super` | Outline (Type 1) fonts for the list bullet |
| `latexmk` | Reruns pdfLaTeX until the document is complete |

## Font encoding

The default template uses pdfLaTeX's default OT1 encoding with Computer Modern, as the owner's file does. It has some gaps:

- The list bullet (`\textbullet`) comes from the TS1 encoding. Without `cm-super`, TeX draws it as a bitmap font, and the renderer warns when a PDF contains one.
- An apostrophe prints curly, as in the owner's PDFs, so PDF text extraction gives a curly apostrophe (U+2019).
- An underscore is drawn as a line, and accented letters are built from separate accents. So PDF text extraction drops underscores and splits accents ("José" comes out as "Jos´ e").

The T1 encoding avoids the last two, but it changes how the PDF looks, so the template keeps OT1.

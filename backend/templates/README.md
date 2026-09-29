# Resume templates

Each folder here is a template: `resume.tex`, plus any files it needs, such as a class or style file. A variant names its template; the default is `default`.

## Writing a template

Templates are Jinja with delimiters that read naturally in LaTeX:

| Syntax | Use |
|---|---|
| `\VAR{selection.contact.name}` | Print a value. Every value is escaped, so `&`, `%` or `$` in the text print as written. |
| `\BLOCK{for role in selection.work}` … `\BLOCK{endfor}` | Statements: loops and conditions |
| `\#{ a note }` | A comment that doesn't reach the PDF |

A template receives:

- `selection`: the variant's content (`app/selection.py`): the contact details, summary, sections in order, education, work, projects and skills;
- `contact`: the contact line as (text, link) pairs;
- `dates(start, end)`, which gives "Mar 2024 -- Present", the `month` filter, and the `url` filter for `\href{\VAR{link|url}}{...}`.

Printing a missing value is an error, so guard optional fields with `\BLOCK{if ...}`. Keep `\VAR`, `\BLOCK` and `\#{` out of LaTeX comments too: Jinja reads the whole file.

## Compiling

`wj render` compiles with `latexmk -pdf` (pdfLaTeX) in a temporary folder, with shell escape off, file access limited to that folder, and a 30-second timeout. Install TeX Live first:

```sh
sudo apt-get install --no-install-recommends latexmk texlive-latex-base texlive-latex-extra texlive-fonts-recommended
```

On macOS, install MacTeX (`brew install --cask mactex-no-gui`). CI installs the same packages:

| Package | Provides |
|---|---|
| `texlive-latex-base` | pdfLaTeX, `geometry`, `hyperref`, `fancyhdr`, `tabularx`, `babel` |
| `texlive-latex-extra` | `enumitem`, `titlesec`, `fullpage` |
| `texlive-fonts-recommended` | `marvosym` |
| `latexmk` | Reruns pdfLaTeX until the document is complete |

## Font encoding

Templates use pdfLaTeX's default OT1 encoding with Computer Modern. It has some gaps:

- An underscore is drawn as a line, and accented letters are built from separate accents. So PDF text extraction drops underscores and splits accents ("José" comes out as "Jos´ e").
- Symbols from the TS1 encoding, such as `\textbullet`, come out as bitmap fonts unless the `cm-super` fonts are installed. The renderer warns when a PDF contains a bitmap font.

The T1 encoding with `cm-super` avoids both. The owner's template (T-011) will decide which to use.

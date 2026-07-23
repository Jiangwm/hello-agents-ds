# Repository Guidelines

## Project Overview

This is **Hello-Agents DSExt**: the Datawhale *Hello-Agents*《从零开始构建智能体》course (16 chapters, 5 parts) plus an "工业数据分析与挖掘" (Industrial Data Analysis & Mining) extension layered on top of it. It is a documentation- and example-code repository (mostly Markdown + Python notebooks/scripts), not a single deployable application. The base course teaches building AI-native agents from scratch; the DSExt layer re-maps each chapter to auditable, evidence-driven industrial data-analysis scenarios. See `README.md` (base) and `docs/readme-dsext.md` (extension) for the full picture.

## Project Structure & Module Organization

Two parallel layers run through the whole repo — base course and DSExt extension:

- **Docs** live under `docs/chapter<N>/`, each holding three paired files: the Chinese chapter (`第N章 ....md`), the English version (`ChapterN-<Topic>.md`), and the industrial extension (`ChapterN-DSExt-XXX.md`). Keep `docs/_sidebar.md` and `docs/_sidebar_en.md` aligned when adding or renaming docs. Docsify site config is `docs/index.html`.
- **Base code** examples belong in `code/chapter<N>/`, with dependencies documented locally.
- **DSExt code** belongs in `code-dsext/chapter<N>/`. These dirs currently hold only `.gitkeep` placeholders; add industrial examples here as chapters are implemented, and record dependencies, data sources, and run steps in a local README.
- **Supplementary articles** live in `Extra-Chapter/` (`ExtraNN-*.md`) and `Additional-Chapter/`.
- **Community applications** go in `Co-creation-projects/<GitHub-user>-<ProjectName>/` and require `README.md`, `requirements.txt`, and `main.ipynb`; `EXAMPLE-ProjectTemplate/` is the reference layout. Industrial migration notes for these projects are in `Co-creation-projects/readme-dsext.md`.
- **Figures** go under `docs/images/<chapter>-figures/`.

## Build, Test, and Development Commands

There is no repository-wide build or dependency lockfile; run commands from the chapter or project you change.

```bash
python -m http.server 8000 --directory docs                          # preview the Docsify site locally
python -m venv .venv                                                  # create an isolated environment
python -m pip install -r Co-creation-projects/<project>/requirements.txt
python code/chapter9/05_terminal_tool_examples.py                    # run a chapter example
python -m pytest Co-creation-projects/<project>/tests -v             # run a project's tests
```

Check the nearest `README.md` for project-specific setup.

## Coding Style & Naming Conventions

Use four-space indentation and PEP 8 for Python: `snake_case` for modules and functions, `PascalCase` for classes, `UPPER_SNAKE_CASE` for constants. Preserve the bilingual heading pattern and the chapter/DSExt filename conventions (`第N章 ....md`, `ChapterN-<Topic>.md`, `ChapterN-DSExt-XXX.md`). No root formatter is enforced; follow adjacent files and avoid unrelated refactors.

## Testing Guidelines

Tests are project-local. Add focused `test_*.py` files beside an existing suite or under the project's `tests/` directory (see `code/chapter7/test_*.py` for the pattern). Cover changed behavior and a failure path. For docs, verify links, images, sidebars, and rendered Markdown. Mock network and LLM responses; routine tests must not require paid API calls.

## Commit & Pull Request Guidelines

History favors `feat:`, `fix:`, and `docs:`, optionally scoped, e.g. `fix(docs): 修正第七章链接`. Limit each commit to one chapter, fix, or project. Pull requests should name the affected path, motivation, and validation; link issues and include screenshots for UI or formatting changes.

## Security & Configuration

Do not commit API keys, tokens, personal/customer datasets, real production addresses, un-desensitized process data, or populated `.env` files. Provide placeholders in `.env.example` and document required variables in the nearest README.

**DSExt safety principles (apply to all industrial examples):** agents are for teaching, offline analysis, and decision support only — default to **read-only** tools and desensitized sample data. Never let a model directly control PLC/MES/DCS, write production parameters, or issue high-risk actions. Key numeric conclusions must come from deterministic tools and be traceable to a data window, rule, document, or tool result; the agent selects tools and organizes evidence rather than free-generating measurements. Keep human-in-the-loop checkpoints for cross-domain data access, process changes, and final root-cause conclusions. Distinguish facts, hypotheses, unknowns, and correlation-vs-causation in outputs.

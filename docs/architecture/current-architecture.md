# Step 1: reusable documentation core

Baseline: KalyanM45/Doclify commit 93dfd6632929e0b1dc4b10b1ff0f2115aa890d01.

## Original execution flow

The Click entry point in `pipelines/supervisor.py` dispatches to components.
`init` scans selected Python/text/Markdown/notebook files using ignore patterns,
then writes a YAML manifest. `run` loads the manifest and JSON cache, extracts
40,000-character chunks, summarizes each chunk through Groq, caches per-file
summaries, and synthesizes a README. The README writer saves an artifact and
backs up the existing README. `update` refreshes selected summaries and asks
whether to regenerate the README. Model configuration and discovery are CLI tools.

There was no frontend, HTTP API, database, job queue, AST graph, or retrieval
engine in this baseline. Summary calls are sequential. Cache entries do not
contain content hashes; an ordinary run reprocesses every selected file.
The `update .` help text incorrectly claimed no new inference calls.

## Changes in this step

`doclify.core.generate_documentation(root, files, ...)` returns a
`DocumentationResult` containing summaries and Markdown. It does not change
directories, write documents, ask questions, or depend on a web framework.
An injectable generation callable enables offline tests. Progress callbacks
receive `(stage, completed, total, relative_path)`.

Both CLI generation commands use `summarize_files`; the existing README writer
uses `synthesize_readme`. The CLI retains persistence, backups, and confirmation.
Library imports no longer configure logging or create log directories. The CLI
configures its own logging explicitly. Prompt resources are included in packages,
and Pydantic is declared directly instead of relying on a transitive dependency.

The core validates resolved input paths before inference, rejects files outside
the repository, and rejects extraction errors, oversized files, and empty model
output. Provider failures propagate rather than creating partial new summaries.
Outer Markdown wrappers are removed without truncating nested code blocks.

## Preserved and deferred

Preserved: upstream attribution/license, CLI names, configuration, Groq adapter,
prompts, extraction, summary-to-README workflow, local cache format and backups.
Directory updates now select configured files beneath the requested directory.

Deferred: content-hash caching, atomic persistence, retry policies, provider
adapters, token budgets, scanner improvements, complete API-level authorization,
background workers, frontend, deployment, and RAG. Input-path validation is not
a sandbox for executing repository code. This step never executes repository code.

Next: a job API that calls the core with an explicit checkout path, stores its
results, and exposes progress to a minimal documentation preview screen.

## Local verification

Eight pytest cases passed using the existing Python 3.10 installation and fake
model responses. Compilation also passed. No live Groq request was made.
Wheel verification was attempted but the installed setuptools/wheel tooling
was insufficient; network dependency upgrades stalled. Package installation
must be verified in a fresh environment with current build tools.

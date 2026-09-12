"""Explicit inputs and in-memory outputs for CLI, API and worker callers."""
from dataclasses import dataclass
from pathlib import Path
from doclify.schema.schema import LLMConfig
from doclify.utils.extract import extract_file_content

@dataclass(frozen=True)
class DocumentationResult:
    summaries: dict[str, str]
    markdown: str

def _default_generate(**kwargs):
    from doclify.utils.llm import generate_doc
    return generate_doc(**kwargs)

def summarize_files(repository_root, files, *, llm_config=None, metadata=None,
                    generate=None, progress=None):
    """Caller owns persistence. Validate all paths before inference; fail on partial output."""
    root = Path(repository_root).resolve(strict=True)
    generate = generate or _default_generate
    paths = []
    for filename in files:
        path = (root / filename).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"File must be inside repository: {filename}")
        paths.append((path, path.relative_to(root).as_posix()))
    if not paths:
        raise ValueError("No files selected")
    summaries = {}
    for index, (path, relative) in enumerate(paths):
        chunks = extract_file_content(str(path))
        if any(c.startswith(("Error", "File not found", "File too large")) for c in chunks):
            raise ValueError(f"Cannot extract {relative}")
        parts = []
        for chunk in chunks:
            chunk = chunk.replace(str(path), relative, 1)
            summary = generate(code_content=chunk, prompt_type="batch_summary",
                               llm_config=llm_config or LLMConfig(), metadata=metadata)
            if not summary or not summary.strip():
                raise ValueError(f"Empty summary for {relative}")
            parts.append(summary.strip())
        summaries[relative] = "\n\n".join(parts)
        if progress:
            progress("summarizing", index + 1, len(paths), relative)
    return summaries

def synthesize_readme(summaries, *, llm_config=None, metadata=None, generate=None):
    if not summaries:
        raise ValueError("No summaries available")
    generate = generate or _default_generate
    context = "\n\n".join(f"## File: {path}\n\n{text}" for path, text in summaries.items())
    response = generate(code_content=context, prompt_type="final_summary",
                        llm_config=llm_config or LLMConfig(), metadata=metadata)
    if not response or not response.strip():
        raise ValueError("Empty README returned by provider")
    markdown = response.strip()
    lines = markdown.splitlines()
    if len(lines) >= 2 and lines[0].strip() in {"```", "```md", "```markdown"} and lines[-1].strip() == "```":
        markdown = "\n".join(lines[1:-1]).strip()
    if not markdown:
        raise ValueError("Empty README returned by provider")
    return markdown

def generate_documentation(repository_root, files, *, project_name=None,
                           llm_config=None, generate=None, progress=None):
    root = Path(repository_root).resolve(strict=True)
    metadata = {"project_name": project_name or root.name}
    summaries = summarize_files(root, files, llm_config=llm_config, metadata=metadata,
                                generate=generate, progress=progress)
    markdown = synthesize_readme(summaries, llm_config=llm_config,
                                metadata=metadata, generate=generate)
    if progress:
        progress("completed", len(summaries), len(summaries), None)
    return DocumentationResult(summaries, markdown)

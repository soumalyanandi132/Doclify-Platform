import time
import yaml
import os
from pathlib import Path
from rich.console import Console
from rich.prompt import Confirm
from doclify.utils.file_utils import load_cache, save_cache, clean_cache
from doclify.schema.schema import LLMConfig
from doclify.utils.readme import generate_readme_file
from doclify.utils.logger import get_logger

# Initialize production-level logger and clean console
logger = get_logger(__name__)
console = Console()

def update_docs(path, model=None, provider=None):
    """
    Update documentation for a specific file or all files (use '.').
    Logs all steps to .doclify/logs/ with a clean uv-style UI.
    """
    start_time = time.time()
    logger.info(f"Update sequence triggered for path: {path}. Overrides: model={model}, provider={provider}")
    config_path = Path("doclify.yaml")
    files_to_process = []
    llm_config = None

    # Load config for LLM settings (needed in both paths)
    if config_path.exists():
        try:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            llm_data = config.get("llm", {})
            llm_config = LLMConfig(**llm_data) if llm_data else LLMConfig()
        except Exception as e:
            logger.error(f"Failed to read doclify.yaml: {str(e)}", exc_info=True)
            console.print(f"[bold red]✖ Error:[/bold red] Error reading [blue]doclify.yaml[/blue]: {e}")
            return
    else:
        config = {}
        llm_config = LLMConfig()

    # Apply CLI overrides
    if model:
        llm_config.model = model
    if provider:
        llm_config.provider = provider

    # Determine files to process
    if path == ".":
        logger.info("Universal update ('.') requested.")
        if not config_path.exists():
            logger.error("doclify.yaml missing during universal update.")
            console.print("[bold red]✖ Error:[/bold red] [blue]doclify.yaml[/blue] not found. Run [bold green]doclify init[/bold green] first.")
            return
        
        files_to_process = config.get("structure", [])
        logger.info(f"Loaded {len(files_to_process)} files from configuration for update.")
    else:
        logger.info(f"Single file update requested for: {path}")
        files_to_process = [path]

    if not files_to_process:
        logger.warning("Update list is empty. Nothing to process.")
        console.print("[bold yellow]⚠ Warning:[/bold yellow] No files found to update.")
        return

    try:
        cache = load_cache()
        if path == ".":
            logger.info("Universal update: cleaning cache of stale entries.")
            cache = clean_cache(cache, files_to_process)
            save_cache(cache)
    except Exception as e:
        logger.warning(f"Failed to load or clean cache: {e}")
        cache = {"files": {}}

    # Generate session ID
    session_id = time.strftime("%Y-%m-%d_%H-%M:%S")
    llm_metadata = {"session_id": session_id}

    from doclify.core import summarize_files
    target = Path(path)
    if path != "." and target.is_dir():
        files_to_process = [f for f in config.get("structure", [])
                            if (Path.cwd() / f).resolve().is_relative_to(target.resolve())]
    summaries = summarize_files(Path.cwd(), files_to_process, llm_config=llm_config,
                                metadata=llm_metadata)
    cache.setdefault("files", {}).update(summaries)

    save_cache(cache)
    
    # Success message
    if path == ".":
        console.print(f"[bold green]Updated[/bold green] artifacts for [white]all files[/white]")
    else:
        console.print(f"[bold green]Updated[/bold green] artifacts for [white]{path}[/white]")

    # 3. Final README Decision
    logger.info("Prompting user for README regeneration.")
    if Confirm.ask("Regenerate README with latest changes?"):
        logger.info("User confirmed README regeneration.")
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        generate_readme_file(cache, config, llm_config=llm_config, metadata=llm_metadata)
        duration = time.time() - start_time
        console.print(f"[bold green]Generated[/bold green] README.md in [white]{duration:.1f} secs[/white]")
    else:
        logger.info("User declined README regeneration.")
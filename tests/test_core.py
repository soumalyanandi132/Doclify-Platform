from pathlib import Path
import subprocess
import sys
import pytest
from doclify.core import generate_documentation

def test_two_repositories_no_writes_or_chdir(tmp_path):
    original = Path.cwd()
    for name in ('first', 'second'):
        root = tmp_path / name
        root.mkdir()
        (root / 'app.py').write_text(f'print("{name}")')
        calls, events = [], []
        def generate(**kwargs):
            calls.append(kwargs)
            return 'summary' if kwargs['prompt_type'] == 'batch_summary' else '```markdown\n# Docs\n```python\nprint(1)\n```\n```'
        result = generate_documentation(root, ['app.py'], generate=generate,
                                        progress=lambda *e: events.append(e))
        assert result.summaries == {'app.py': 'summary'}
        assert result.markdown == '# Docs\n```python\nprint(1)\n```'
        assert calls[0]['metadata']['project_name'] == name
        assert str(root) not in calls[0]['code_content']
        assert events[-1][0] == 'completed'
        assert list(root.iterdir()) == [root / 'app.py']
        assert Path.cwd() == original

def test_escape_rejected_before_inference(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    (tmp_path / 'secret.txt').write_text('secret')
    with pytest.raises(ValueError, match='inside repository'):
        generate_documentation(root, ['../secret.txt'], generate=lambda **k: pytest.fail('called provider'))

@pytest.mark.parametrize('response', ['', '   ', None])
def test_empty_summary_is_failure(tmp_path, response):
    (tmp_path / 'app.py').write_text('print(1)')
    with pytest.raises(ValueError, match='Empty summary'):
        generate_documentation(tmp_path, ['app.py'], generate=lambda **k: response)

def test_large_file_never_sent(tmp_path):
    (tmp_path / 'app.py').write_text('x' * 1_000_001)
    with pytest.raises(ValueError, match='Cannot extract'):
        generate_documentation(tmp_path, ['app.py'], generate=lambda **k: pytest.fail('called provider'))

def test_import_does_not_create_logs(tmp_path):
    subprocess.run([sys.executable, '-c', 'import doclify.core'], cwd=tmp_path, check=True)
    assert not list(tmp_path.iterdir())

def test_cli_run_preserves_backup(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from doclify.pipelines.supervisor import cli
    import doclify.utils.llm
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'doclify.yaml').write_text('project: Demo\nstructure: [app.py]\n')
    (tmp_path / 'app.py').write_text('print(1)')
    (tmp_path / 'README.md').write_text('old readme')
    monkeypatch.setattr(doclify.utils.llm, 'generate_doc', lambda **k: '# New docs')
    # The legacy writer imports its provider at module scope.
    import doclify.utils.readme
    monkeypatch.setattr(doclify.utils.readme, 'generate_doc', lambda **k: '# New docs')
    result = CliRunner().invoke(cli, ['run'])
    assert result.exit_code == 0, result.output
    assert (tmp_path / 'README.md').read_text() == '# New docs'
    assert next(tmp_path.glob('README-prev-*.md')).read_text() == 'old readme'

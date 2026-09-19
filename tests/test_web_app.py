from doclify.web.server import Workspace


def wait_for_job(workspace, job_id):
    import time
    deadline = time.time() + 3
    while time.time() < deadline:
        job = workspace.store.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if job["status"] != "queued" and job["status"] != "running":
            return job
        time.sleep(0.02)
    return workspace.store.one("SELECT * FROM jobs WHERE id=?", (job_id,))


def test_web_shell_and_static_assets_load(tmp_path):
    static = __import__("doclify.web.server", fromlist=["Handler"]).Handler.static
    page = (static / "index.html").read_text(encoding="utf-8")
    assert 'href="static/styles.css"' in page
    assert 'src="static/app.js"' in page
    assert (static / "styles.css").read_text(encoding="utf-8").startswith(":root")
    assert "async function init()" in (static / "app.js").read_text(encoding="utf-8")


def test_sample_repository_indexes_and_reports(tmp_path):
    workspace = Workspace(data_dir=tmp_path)
    created = workspace.add_sample()
    job = wait_for_job(workspace, created["job_id"])
    assert job["status"] == "completed"

    repositories = workspace.list_repositories()
    assert repositories[0]["name"] == "sample/orbit-api"

    detail = workspace.repository_detail(created["repository_id"])
    assert detail["status"] == "ready"
    assert detail["file_count"] >= 1

    report = workspace.create_document(created["repository_id"], "report")
    assert wait_for_job(workspace, report["job_id"])["status"] == "completed"
    updated = workspace.repository_detail(created["repository_id"])
    assert updated["documents"]

import re

with open('tests/test_api.py', 'r') as f:
    content = f.read()

# Add the fixture after _make_empty_uow
fixture = '''
@pytest.fixture(autouse=True)
def override_db_dependencies():
    from api.main import app
    from api.dependencies import get_unit_of_work, get_job_repository

    async def _mock_repo():
        mock_repo = AsyncMock()
        mock_repo.get = AsyncMock(return_value=None)
        mock_repo.get_top_skills = AsyncMock(return_value=[{"skill": "Python", "count": 500}, {"skill": "SQL", "count": 420}])
        yield mock_repo

    app.dependency_overrides[get_unit_of_work] = _make_empty_uow
    app.dependency_overrides[get_job_repository] = _mock_repo
    yield
    app.dependency_overrides.clear()

'''

content = content.replace('def _make_empty_uow():\n    """UoW mock that returns an empty job list (no DB required)."""\n    mock_uow = AsyncMock()\n    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)\n    mock_uow.__aexit__ = AsyncMock(return_value=False)\n    mock_uow.jobs.search = AsyncMock(return_value=([], 0))\n    return mock_uow\n', 'def _make_empty_uow():\n    """UoW mock that returns an empty job list (no DB required)."""\n    mock_uow = AsyncMock()\n    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)\n    mock_uow.__aexit__ = AsyncMock(return_value=False)\n    mock_uow.jobs.search = AsyncMock(return_value=([], 0))\n    return mock_uow\n' + fixture)

# Fix test_structured_search_returns_200_with_mocked_db
content = re.sub(r'        from api.main import app.*?try:\n', '', content, flags=re.DOTALL)
content = re.sub(r'        finally:\n            app.dependency_overrides.clear()\n', '', content)
content = content.replace('        with patch("api.routers.search.get_unit_of_work", return_value=_make_empty_uow()):\n', '')
content = re.sub(r'        with patch\("api.routers.trends.get_job_repository".*?mock_repo_dep.return_value = mock_repo\n', '', content, flags=re.DOTALL)
content = re.sub(r'        with patch\("api.routers.search.get_job_repository"\) as mock_dep:\n.*?mock_dep.return_value = mock_repo\n', '', content, flags=re.DOTALL)

with open('tests/test_api.py', 'w') as f:
    f.write(content)

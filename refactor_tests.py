import re
import glob

def refactor_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
    
    # 1. Change 'def test_*(self, api_client' to 'async def test_*(self, api_client'
    # and 'def test_*(api_client' to 'async def test_*(api_client'
    content = re.sub(
        r'(?m)^(\s*)def\s+(test_[a-zA-Z0-9_]+)\s*\((.*?api_client.*?)\)\s*:',
        r'\1async def \2(\3):',
        content
    )
    
    # 2. Add 'await ' before 'api_client.get' / 'api_client.post' etc if not present
    # We look for 'api_client.\w+\(' and prepend 'await ' if it is not already awaited
    # Example: response = api_client.post(...) -> response = await api_client.post(...)
    content = re.sub(
        r'(?<!await\s)api_client\.(get|post|put|delete|patch)\(',
        r'await api_client.\1(',
        content
    )
    
    with open(filepath, 'w') as f:
        f.write(content)

if __name__ == "__main__":
    refactor_file("tests/test_api.py")
    refactor_file("tests/test_resume_matcher.py")
    print("Refactoring complete.")

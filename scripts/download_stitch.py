import os
import json
import urllib.request

base_dir = r"d:\projects\talentRadar\stitch"
os.makedirs(base_dir, exist_ok=True)

# Load project metadata
output_path = r"C:\Users\ajitp\.gemini\antigravity\brain\cd7ac7f5-4b57-4578-b2f0-812a12090254\.system_generated\steps\19\output.txt"
with open(output_path, "r", encoding="utf-8") as f:
    data = json.load(f)

project = data["projects"][0]

screens = [
    {
        "index": 1,
        "id": "asset-stub-assets_131d42abf87b4de583355f1d81cdd7a2",
        "title": "Design System",
        "slug": "01_design_system",
        "is_design_system": True,
    },
    {
        "index": 2,
        "id": "672b229e96ef4dbc848d8b2a9f4a52ae",
        "title": "Landing & Authentication",
        "slug": "02_landing_authentication",
        "screenshot_url": "https://lh3.googleusercontent.com/aida/AP1WRLsrvq2-QeQ5MPZodBs2ZXsgfLGdb13UdGRXCNWkWl3mkTK4zQY3q5fXFoak9r3ewiPC7fouhcwTTo0iUqSUu4ONJaNyIQAiIWIy2jTyZRv_x2vhmfmyt1y1aZ30FHa2XBKqDuPciSxNgYy10L2gMeHfhanvKm9419GTD-aZcfVZZ7Pwev7MIV2ZvKFBt-1zGzJGETAj-uRlZzABMVnzdY8MTw-aC90oSq7JVmb9Bl0uW9RM3ikFYTRsQw",
        "html_url": "https://contribution.usercontent.google.com/download?c=CgthaWRhX2NvZGVmeBJ7Eh1hcHBfY29tcGFuaW9uX2dlbmVyYXRlZF9maWxlcxpaCiVodG1sXzAwMDY1OTYzN2I3OWM2ZDIwNzc5YWY1ZjA0MjI1ZGE4EgsSBxCTnYrZ-BwYAZIBIwoKcHJvamVjdF9pZBIVQhM0MzI3NDc4Mzc2NjM2MjM5ODcz&filename=&opi=89354086",
        "width": "2560",
        "height": "2048",
        "deviceType": "DESKTOP",
    },
    {
        "index": 3,
        "id": "4eaba16e566646a7a4a739d78737ec67",
        "title": "Candidate Profile Onboarding Wizard",
        "slug": "03_candidate_profile_onboarding_wizard",
        "screenshot_url": "https://lh3.googleusercontent.com/aida/AEtjO1WLYLowV-U5VJzG40PcqSSH9_daTbK2pEUIwaHuvhQruPbCWCtWwxjvz61tDRVmBN1Nr-YbIXSSrWCi3cTlelMANcfom5-bjjggxwSiYEaFareGCSt_ll9zsXbslepiS8xmlQQ7gx_Vgnj4LMyDXzzPJQPmvsExxwKpaRt0dkZ1bDeZArBtrb_JqBEN-GN2w0ctuVEaDeg-OQC0ofY7u-Yx4v912A3QDL_XwreC5Sjz5jsHqAeDHDakeYo",
        "html_url": "https://contribution.usercontent.google.com/download?c=CgthaWRhX2NvZGVmeBJ7Eh1hcHBfY29tcGFuaW9uX2dlbmVyYXRlZF9maWxlcxpaCiVodG1sXzAwMDY1OTYzN2M4MjI1M2IwOTEwN2M1Y2I2MTVlMDFjEgsSBxCTnYrZ-BwYAZIBIwoKcHJvamVjdF9pZBIVQhM0MzI3NDc4Mzc2NjM2MjM5ODcz&filename=&opi=89354086",
        "width": "2560",
        "height": "2048",
        "deviceType": "DESKTOP",
    },
    {
        "index": 4,
        "id": "680599a07cca4d05b94005f9ebf746ed",
        "title": "Command Center Dashboard",
        "slug": "04_command_center_dashboard",
        "screenshot_url": "https://lh3.googleusercontent.com/aida/AP1WRLvQpBHlbjXTjfkGDdvOooAcTb1Yj7vt0T7Ynv6EgT6vM3rdfcvEbp-umJ3tcM7hh60Tf2xGl7V-mu2YTKtfg28jB0-8SZfy36zhcWyH5mURh-OxxE_o11jyuFeTRwy26RHTMEeSpKCFNOd2TAwrv6o152KGeZjxSjSigrreoQUBnJBf32CzTVRz9LQ7DN1IU3KdCNGvsLbtEtg6iMkd1NuK1xzDQQAnpA1wL3aqBsHJjBUoQOHucNQYrcU",
        "html_url": "https://contribution.usercontent.google.com/download?c=CgthaWRhX2NvZGVmeBJ7Eh1hcHBfY29tcGFuaW9uX2dlbmVyYXRlZF9maWxlcxpaCiVodG1sXzAwMDY1OTYzN2QxNGNmMDYwNzNhZTQ1ZTU5MWY4NWRhEgsSBxCTnYrZ-BwYAZIBIwoKcHJvamVjdF9pZBIVQhM0MzI3NDc4Mzc2NjM2MjM5ODcz&filename=&opi=89354086",
        "width": "2560",
        "height": "2748",
        "deviceType": "DESKTOP",
    },
    {
        "index": 5,
        "id": "34a6844e6c644c5e8d0a28f54d801ca7",
        "title": "Semantic Job Search & Explorer",
        "slug": "05_semantic_job_search_explorer",
        "screenshot_url": "https://lh3.googleusercontent.com/aida/AEtjO1VDwDKZUAbMuoOz_rZHZwgmMESKl90cvpYinv7SGSWuszp_jpX3e9pDSQZk_3xLldFQs6pnBtGWn3nncb8TDvb_J7a38YTvZiJsuvp5IlDs-8strBlaqs7gPo6XWBHhf5xwgvpkB4eyDBudOa3CTh4bOJ04s89jxnDnvYfNxdudQO0uaDDBDYxEMCjM4FZNHRDA6m5BCLXiC8e8BENpD4rMIZCYeA_KAIDwikWc_zX1SRCcLdeNblukeUw",
        "html_url": "https://contribution.usercontent.google.com/download?c=CgthaWRhX2NvZGVmeBJ7Eh1hcHBfY29tcGFuaW9uX2dlbmVyYXRlZF9maWxlcxpaCiVodG1sXzAwMDY1OTYzODEyYWUwYTAwMzM4NWI1MTBhMTgyN2JhEgsSBxCTnYrZ-BwYAZIBIwoKcHJvamVjdF9pZBIVQhM0MzI3NDc4Mzc2NjM2MjM5ODcz&filename=&opi=89354086",
        "width": "2560",
        "height": "2048",
        "deviceType": "DESKTOP",
    },
    {
        "index": 6,
        "id": "80b1dc7b04fd4c48b357b0d6efaed964",
        "title": "Job Detail & AI Match Breakdown",
        "slug": "06_job_detail_ai_match_breakdown",
        "screenshot_url": "https://lh3.googleusercontent.com/aida/AEtjO1VbdBodR0w6ajYLnPPAulqjZ2fd9-LCxmczecWa6LEJWqTNw4ATEk5FIQbVmnk_LGL3AOMFffYYjT9Gk61vFGdNVEEGCM5W9xkz6tnxw1UlgFvzKtD87fqIoR3CSuAZTU4kZ6pQgwcndOyyfZ7dVawRekBcVVdk5LKZtX-HL8iGT-EF1GuklI6xFkSKNPXa7hT9XX3HCgg1kXZcTcgWd52ZnChCh1MAKaoNSgg313nv-bmMuQ5sHM9LZwc",
        "html_url": "https://contribution.usercontent.google.com/download?c=CgthaWRhX2NvZGVmeBJ7Eh1hcHBfY29tcGFuaW9uX2dlbmVyYXRlZF9maWxlcxpaCiVodG1sXzAwMDY1OTYzODBjMmRkZjEwMjhmMDkxYzUwMDJkMDUzEgsSBxCTnYrZ-BwYAZIBIwoKcHJvamVjdF9pZBIVQhM0MzI3NDc4Mzc2NjM2MjM5ODcz&filename=&opi=89354086",
        "width": "2560",
        "height": "2936",
        "deviceType": "DESKTOP",
    },
    {
        "index": 7,
        "id": "672757e255d9465586b532e1f2aacad1",
        "title": "Resume Studio & Optimizer",
        "slug": "07_resume_studio_optimizer",
        "screenshot_url": "https://lh3.googleusercontent.com/aida/AP1WRLuLUS1NVuxFoY6m-gTUONednDDbi40HzTEu8r2zvGTMO-3Mw_d8nEVWhgZRUxKBX-4QC7EHxmcgK2DVzZzDUMTITHg2uc1qYPlx5m-m6uwO8ZOK1XlYtke2fcuBMI6hG8hx3BdF2iicXvJXLsJ2eFR0i7njGemtSfGKEQkN43xfrvdWiqVBLmGMdRZEdu_Uj0dd7RAA-61GUtHghcUvgcl312R7lES5IxlBkKSYxmurXOtPqlfPCfs8fw",
        "html_url": "https://contribution.usercontent.google.com/download?c=CgthaWRhX2NvZGVmeBJ7Eh1hcHBfY29tcGFuaW9uX2dlbmVyYXRlZF9maWxlcxpaCiVodG1sXzAwMDY1OTYzODBmNzZhNDMwMzMyZWIwZjczMDlmYzkwEgsSBxCTnYrZ-BwYAZIBIwoKcHJvamVjdF9pZBIVQhM0MzI3NDc4Mzc2NjM2MjM5ODcz&filename=&opi=89354086",
        "width": "2560",
        "height": "2048",
        "deviceType": "DESKTOP",
    },
    {
        "index": 8,
        "id": "04834277dae64dca8b3d964231bd7645",
        "title": "Application Tracking Kanban Pipeline",
        "slug": "08_application_tracking_kanban_pipeline",
        "screenshot_url": "https://lh3.googleusercontent.com/aida/AP1WRLsEoqQOsVoqAaC7A-XQTB1oE3PY83WOxE0YCMNL1DjSVtBebq4rNolo9zwQF24cQKoHHfXrsHuUvSZANO1vNKs3kQSklk3pz8CcjVm6IJKdVJ4ucWj5-cma3-9lBXs154pfgxKIdbeZ5N2Hztlhn5ptsirFEPdIv5icjTB1od49yHTrIKIa1zG-8MQL37kY05W63UtS4zbw3EkLvRvmlQ0yWavylbaSPPzeht3HKAhkdzI_H-hkAlQcfg",
        "html_url": "https://contribution.usercontent.google.com/download?c=CgthaWRhX2NvZGVmeBJ7Eh1hcHBfY29tcGFuaW9uX2dlbmVyYXRlZF9maWxlcxpaCiVodG1sXzAwMDY1OTYzODUxMTcxMDQwMWE2MjlhODFjMGRlZGUxEgsSBxCTnYrZ-BwYAZIBIwoKcHJvamVjdF9pZBIVQhM0MzI3NDc4Mzc2NjM2MjM5ODcz&filename=&opi=89354086",
        "width": "2560",
        "height": "2048",
        "deviceType": "DESKTOP",
    },
    {
        "index": 9,
        "id": "245faed561bf4557a9f1fe9711d8464a",
        "title": "Mock Interview Simulator",
        "slug": "09_mock_interview_simulator",
        "screenshot_url": "https://lh3.googleusercontent.com/aida/AEtjO1XBU3jl2j4FIKYMN7AOB_9p67mqRrT7xyeo-D6OHLZgHMehXKe-8OZrXU9uJ_y57I3urDmj8_BmamsuphqHvccW43hFQj8yB9NmJgEqoUD-FTFYg56a6Ifrfy659mMJ4ZnPDQUZI0paRVTCiW-X_yWUxhyI44SOnxqh1uWc--kxbVlCsqgM8_Td8aKtMf4sr569-YtT0ckU4ViuxaLHyLAH-CmwAGcfwp3Y5DmGxaJy-1zC304DnosZ-zI",
        "html_url": "https://contribution.usercontent.google.com/download?c=CgthaWRhX2NvZGVmeBJ7Eh1hcHBfY29tcGFuaW9uX2dlbmVyYXRlZF9maWxlcxpaCiVodG1sXzAwMDY1OTYzODQxMjZmMDkwMWVlN2YyMzRhMzdmNjIwEgsSBxCTnYrZ-BwYAZIBIwoKcHJvamVjdF9pZBIVQhM0MzI3NDc4Mzc2NjM2MjM5ODcz&filename=&opi=89354086",
        "width": "2560",
        "height": "2048",
        "deviceType": "DESKTOP",
    },
]

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

for item in screens:
    folder = os.path.join(base_dir, item["slug"])
    os.makedirs(folder, exist_ok=True)

    if item.get("is_design_system"):
        design_md = project["designTheme"]["designMd"]
        with open(os.path.join(folder, "DESIGN.md"), "w", encoding="utf-8") as f:
            f.write(design_md)
        with open(
            os.path.join(folder, "design_theme.json"), "w", encoding="utf-8"
        ) as f:
            json.dump(project["designTheme"], f, indent=2)
        with open(os.path.join(folder, "metadata.json"), "w", encoding="utf-8") as f:
            json.dump(item, f, indent=2)
        print(f"[Done] Screen {item['index']}: {item['title']}")
        continue

    # Download screenshot
    screenshot_path = os.path.join(folder, "screenshot.png")
    req = urllib.request.Request(item["screenshot_url"], headers=headers)
    with urllib.request.urlopen(req) as resp, open(
        screenshot_path, "wb"
    ) as out_file:
        out_file.write(resp.read())
    img_size = os.path.getsize(screenshot_path)

    # Download HTML
    html_path = os.path.join(folder, "index.html")
    req = urllib.request.Request(item["html_url"], headers=headers)
    with urllib.request.urlopen(req) as resp, open(html_path, "wb") as out_file:
        out_file.write(resp.read())
    html_size = os.path.getsize(html_path)

    # Save metadata
    with open(os.path.join(folder, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(item, f, indent=2)

    print(
        f"[Done] Screen {item['index']}: {item['title']} (Image: {img_size:,} bytes, HTML: {html_size:,} bytes)"
    )

# Save master manifest
manifest = {
    "project_name": project["name"],
    "project_id": "4327478376636239873",
    "title": project.get("title"),
    "create_time": project.get("createTime"),
    "update_time": project.get("updateTime"),
    "screens": screens,
}
with open(os.path.join(base_dir, "manifest.json"), "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2)

# Generate README.md
readme_content = f"""# TalentRadar Stitch UI Screens & Assets

Project: **{project.get('title')}**
Project ID: `4327478376636239873`

## Directory Structure

- `01_design_system/`: Design system tokens, style guides, and `DESIGN.md`
- `02_landing_authentication/`: Landing page & authentication flow
- `03_candidate_profile_onboarding_wizard/`: 4-step candidate profile onboarding wizard
- `04_command_center_dashboard/`: Command center dashboard with match metrics, radar charts, active matches
- `05_semantic_job_search_explorer/`: Semantic job search explorer with filters, map view, match badges
- `06_job_detail_ai_match_breakdown/`: Detailed job view with AI match score, skills gap analysis, match radar
- `07_resume_studio_optimizer/`: AI Resume studio and real-time optimizer with live preview
- `08_application_tracking_kanban_pipeline/`: Multi-stage application pipeline Kanban board
- `09_mock_interview_simulator/`: Interactive AI interview simulator with audio/video feedback and real-time critique

Each screen directory contains:
- `screenshot.png`: High-resolution visual render (2560x2048+)
- `index.html`: Complete standalone Tailwind/HTML code
- `metadata.json`: Screen specifications and dimensions
"""

with open(os.path.join(base_dir, "README.md"), "w", encoding="utf-8") as f:
    f.write(readme_content)

print("\nAll downloads and metadata generated successfully!")

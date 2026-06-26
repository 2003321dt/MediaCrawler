# Xiaod Railway MediaCrawler Exporter

This repo is used only as an optional external enrichment source for `小D看剧日报`.

Railway Cron:

- Dockerfile path: `Dockerfile.railway`
- Command: default image command, or explicitly `python run_once_exporter.py`
- Schedule: `30 0 * * *` when Railway cron is UTC, equivalent to 08:30 Asia/Shanghai.

Recommended env:

- `MC_PLATFORMS=bili,tieba,zhihu`
- `MC_KEYWORDS=电视剧,综艺,短剧,热播,定档,开播`
- `MC_MAX_NOTES=20`
- `MC_MAX_CONCURRENCY=1`
- `MC_OUTPUT=outputs/mediacrawler/latest-hotspots.json`
- `MC_SAVE_DATA_PATH=data/railway-run`
- `MEDIACRAWLER_USE_BUNDLED_CHROMIUM=1`
- `GITHUB_TOKEN=<fine-grained-token-with-contents-write>`
- `OUTPUT_REPO=2003321dt/MediaCrawler`
- `OUTPUT_BRANCH=main`
- `OUTPUT_PATH=outputs/mediacrawler/latest-hotspots.json`

First version scope:

- Enabled platforms: `bili`, `tieba`, `zhihu`.
- Disabled by default: `xhs`, `dy`, `ks`, `wb`, comments, media download, wordcloud, CDP mode, persistent login state.
- The exporter always writes `latest-hotspots.json`; failures are visible in `errors` and `runs`.

# CoinPilotXAI Inc. Indexing Readiness Report

Date: 2026-05-10

## Crawlability

- Public pages return canonical URLs and indexable metadata.
- `/robots.txt` allows public educational pages and blocks private/admin/API/auth/payment routes.
- `/sitemap.xml` is generated dynamically from the same public route map used by the static sitemap file.
- `/ai-index.json` gives AI search systems a structured summary of public pages, keyword clusters, and safety rules.
- `/llms.txt` gives LLM crawlers concise product, safety, and page guidance.

## Structured Data

- Organization schema is available sitewide through SEO pages.
- WebSite schema includes search action support.
- SoftwareApplication schema describes CoinPilotX as the public product operated by CoinPilotXAI Inc.
- FAQ schema is rendered on SEO pages.
- Breadcrumb schema is rendered on SEO pages.
- Article schema is rendered for article-style pages and prediction/sports scenario pages.

## Important Indexable Route Groups

- `/markets/<symbol>`
- `/markets/<symbol>/live`
- `/markets/<symbol>/prediction`
- `/country-intelligence/<country>`
- `/sports-edge/<sport>`
- `/intel/<article>`
- `/news`, `/insights`, `/intel`

## Intentional Non-Indexing

- `/search` remains `noindex, follow` because search result pages can create thin/duplicate index pages.
- Admin, API, auth, account, debug, and webhook routes are excluded from robots and sitemap.

## Manual Submission Checklist

- Submit `https://coinpilotx.app/sitemap.xml` in Google Search Console.
- Submit `https://coinpilotx.app/sitemap.xml` in Bing Webmaster Tools.
- Set `GOOGLE_SITE_VERIFICATION` and `BING_SITE_VERIFICATION` environment variables when verification tokens are available.
- Use IndexNow after deployment if Bing indexing needs a refresh.

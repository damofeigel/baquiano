BOT_NAME = "portfolio_seed_loader"

SPIDER_MODULES = ["portfolio_spider.spiders"]
NEWSPIDER_MODULE = "portfolio_spider.spiders"

ROBOTSTXT_OBEY = True
CONCURRENT_REQUESTS = 8
DOWNLOAD_DELAY = 0.2

FEEDS = {
    "portfolio_seeds.jsonl": {
        "format": "jsonlines",
        "overwrite": True,
    }
}
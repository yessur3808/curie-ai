# utils/search.py

from duckduckgo_search import DDGS
from icrawler.builtin import GoogleImageCrawler
import tempfile
import os

def web_search(query, max_results=3):
    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, region='wt-wt', safesearch='Moderate', timelimit='y'):
            results.append({
                "title": r.get("title"),
                "href": r.get("href"),
                "body": r.get("body")
            })
            if len(results) >= max_results:
                break
    return results

def image_search(query, max_results=3):
    results = []
    with DDGS() as ddgs:
        for r in ddgs.images(query, safesearch='Moderate', size=None):
            results.append(r.get("image"))
            if len(results) >= max_results:
                break
    return results

def crawl_google_images(query, max_num=3, out_dir=None):
    # Use icrawler's GoogleImageCrawler to download images
    out_dir = out_dir or tempfile.mkdtemp(prefix='imgcrawl_')
    crawler = GoogleImageCrawler(storage={'root_dir': out_dir})
    crawler.crawl(keyword=query, max_num=max_num)
    # Return file paths of downloaded images
    files = [os.path.join(out_dir, f) for f in os.listdir(out_dir)]
    return files, out_dir

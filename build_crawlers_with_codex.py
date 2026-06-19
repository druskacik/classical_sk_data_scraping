from __future__ import annotations

import argparse
import re
from pathlib import Path
from shutil import which
from urllib.parse import urlparse

import pystache
from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox


URLS = [
    "https://www.hamu.cz/",
    "https://www.ceskafilharmonie.cz/",
    "https://www.berg.cz/",
    "https://www.varhannifestival.cz/",
    "https://www.neoklasikorchestr.cz/",
    "https://www.auditeorganum.cz/",
    "https://www.liedercompany.cz/",
    "https://www.hybatelerezonance.cz/",
    "https://www.letnislavnosti.cz/",
    "https://www.narodni-divadlo.cz/",
    "https://www.cnso.cz/",
    "https://www.musicaflorea.cz/",
    "https://www.salvator.farnost.cz/",
    "https://www.stnicholas.cz/",
    "https://festival.cz/",
    "https://firkusny.cz/",
    "https://www.dvorak-symphony-orchestra.com/",
    "https://www.pkf.cz/",
    "https://praguesounds.cz/",
    "https://socr.rozhlas.cz/",
    "https://www.fok.cz/",
    "https://www.collegiummarianum.cz/",
    "https://collegium1704.com/",
    "https://www.prgcons.cz/",
    "https://www.bachcollegium.cz/",
    "https://www.dvorakovapraha.cz/",
    "https://www.camerata2018.cz/",
    "https://www.pko.cz/",
    "https://www.suksymphony.cz/",
    "https://www.ensembleinegal.cz/",
    "https://praha.charita.cz/",
    "https://www.pragueclassicalconcerts.com/",
    "https://www.pragueticketoffice.com/",
    "https://www.bco.cz/",
    "https://filharmonie-brno.cz/",
    "https://www.ndbrno.cz/",
    "https://jamu.cz/",
    "https://www.msobrno.cz/",
    "https://www.konzervatorbrno.eu/",
    "https://www.ebcz.eu/",
    "https://www.cfsbrno.cz/",
    "https://www.mhflj.cz/",
    "https://shf.cz/",
    "https://www.ndm.cz/",
    "https://www.jko.cz/",
    "https://www.jfo.cz/",
    "https://www.djkt.eu/",
    "https://www.smetanovskedny.cz/",
    "https://www.plzenskafilharmonie.cz/",
    "https://www.saldovo-divadlo.cz/",
    "https://www.moravskedivadlo.cz/",
    "https://www.mfo.cz/",
    "https://www.jcfilharmonie.cz/",
    "https://www.jihoceskedivadlo.cz/",
    "https://www.jhf.cz/",
    "https://www.fhk.cz/",
    "https://www.operabalet.cz/",
    "https://www.kfpar.cz/",
    "https://www.divadlojablonec.cz/",
    "https://www.filharmonie-zlin.cz/",
    "https://www.kso.cz/",
]

MODEL = "gpt-5.5"
PROMPT_PATH = Path("prompts/build_crawler.mustache")
CRAWLERS_DIR = Path("crawlers")
CZECH_DOMAINS = {".cz"}
SLOVAK_DOMAINS = {".sk"}
CZECH_HOSTS = {
    "collegium1704.com",
    "djkt.eu",
    "dvorak-symphony-orchestra.com",
    "ebcz.eu",
    "konzervatorbrno.eu",
    "pragueclassicalconcerts.com",
    "pragueticketoffice.com",
}


def crawler_folder_name(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = parsed.netloc or parsed.path.split("/", 1)[0]
    host = host.split("@")[-1].split(":", 1)[0].lower()

    if host.startswith("www."):
        host = host[4:]

    folder = re.sub(r"[^a-z0-9]+", "_", host).strip("_")
    if not folder:
        raise ValueError(f"Could not derive crawler folder name from URL: {url!r}")
    return folder


def country_code_for_url(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.netloc or parsed.path.split("/", 1)[0]).lower()
    if host.startswith("www."):
        host = host[4:]
    if host in CZECH_HOSTS:
        return "CZ"
    if any(host.endswith(domain) for domain in CZECH_DOMAINS):
        return "CZ"
    if any(host.endswith(domain) for domain in SLOVAK_DOMAINS):
        return "SK"
    raise ValueError(f"Could not infer country code from URL: {url!r}. Pass only country-specific domains or add a mapping.")


def render_prompt(url: str) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    return pystache.render(template, {"url": url, "country_code": country_code_for_url(url)})


def build_crawler(codex: Codex, url: str) -> str | None:
    folder_name = crawler_folder_name(url)
    crawler_dir = CRAWLERS_DIR / country_code_for_url(url).lower() / folder_name

    if crawler_dir.exists():
        print(f"SKIP {url} -> {crawler_dir} already exists")
        return None

    prompt = render_prompt(url)
    print(f"BUILD {url} -> {crawler_dir}")

    thread = codex.thread_start(
        approval_mode=ApprovalMode.auto_review,
        cwd=str(Path.cwd()),
        model=MODEL,
        sandbox=Sandbox.full_access,
    )
    result = thread.run(
        prompt,
        approval_mode=ApprovalMode.auto_review,
        cwd=str(Path.cwd()),
        model=MODEL,
        sandbox=Sandbox.full_access,
    )

    if result.error:
        raise RuntimeError(f"Codex failed for {url}: {result.error}")

    return result.final_response


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Use Codex SDK to build crawlers from prompts/build_crawler.mustache."
    )
    parser.add_argument(
        "--url",
        action="append",
        dest="urls",
        help="URL to build. Can be provided multiple times. Defaults to the URLS list in this file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print derived crawler folders and skip/build decisions.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    urls = args.urls or URLS

    if args.dry_run:
        for url in urls:
            crawler_dir = CRAWLERS_DIR / country_code_for_url(url).lower() / crawler_folder_name(url)
            action = "SKIP" if crawler_dir.exists() else "BUILD"
            print(f"{action} {url} -> {crawler_dir}")
        return

    codex_bin = which("codex")
    if not codex_bin:
        raise RuntimeError("Could not find `codex` binary. Is it installed and on PATH?")

    with Codex(CodexConfig(codex_bin=codex_bin, cwd=str(Path.cwd()))) as codex:
        for url in urls:
            final_response = build_crawler(codex, url)
            if final_response:
                print(f"\nCodex final response for {url}:\n{final_response}\n")


if __name__ == "__main__":
    main()

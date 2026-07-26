from __future__ import annotations

import argparse
import json
import os
import re
import traceback
from pathlib import Path
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
    # "https://www.salvator.farnost.cz/",
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

MODEL = "gpt-5.6-sol"
PROMPT_PATH = Path("prompts/build_crawler.mustache")
CRAWLERS_DIR = Path("crawlers")
BLOCKED_MARKER = "BLOCKED.md"
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


def render_prompt(url: str, workspace: Path | None = None) -> str:
    template = ((workspace or Path.cwd()) / PROMPT_PATH).read_text(encoding="utf-8")
    return pystache.render(template, {"url": url, "country_code": country_code_for_url(url)})


def crawler_status(crawler_dir: Path) -> str:
    if not crawler_dir.exists():
        return "BUILD"
    if (crawler_dir / BLOCKED_MARKER).exists():
        return "BLOCKED"
    return "SKIP"


def build_crawler(
    codex: Codex,
    url: str,
    workspace: Path | None = None,
    retry_blocked: bool = False,
    sandbox: Sandbox = Sandbox.full_access,
) -> dict:
    workspace = (workspace or Path.cwd()).resolve()
    folder_name = crawler_folder_name(url)
    crawler_dir = workspace / CRAWLERS_DIR / country_code_for_url(url).lower() / folder_name
    status = crawler_status(crawler_dir)

    if status == "BLOCKED" and retry_blocked:
        print(f"RETRY {url} -> {crawler_dir}")
    elif status != "BUILD":
        print(f"{status} {url} -> {crawler_dir} already exists")
        return {
            "url": url,
            "status": "blocked" if status == "BLOCKED" else "skipped_existing",
            "crawler_directory": str(crawler_dir.relative_to(workspace)),
            "final_response": None,
            "error": None,
        }

    prompt = render_prompt(url, workspace)
    print(f"BUILD {url} -> {crawler_dir}")

    thread = codex.thread_start(
        approval_mode=ApprovalMode.auto_review,
        cwd=str(workspace),
        model=MODEL,
        sandbox=sandbox,
    )
    result = thread.run(
        prompt,
        approval_mode=ApprovalMode.auto_review,
        cwd=str(workspace),
        model=MODEL,
        sandbox=sandbox,
    )

    if result.error:
        raise RuntimeError(f"Codex failed for {url}: {result.error}")

    return {
        "url": url,
        "status": "generated",
        "crawler_directory": str(crawler_dir.relative_to(workspace)),
        "final_response": result.final_response,
        "error": None,
    }


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
    parser.add_argument(
        "--max-urls",
        type=int,
        default=None,
        help="Process at most this many URLs after applying --url/default selection.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="Repository checkout Codex may modify. Defaults to the current directory.",
    )
    parser.add_argument(
        "--results",
        type=Path,
        help="Write a JSON batch report to this path.",
    )
    parser.add_argument(
        "--continue-on-error",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Continue processing later URLs after a Codex failure (default: true).",
    )
    parser.add_argument(
        "--retry-blocked",
        action="store_true",
        help="Run Codex even when the expected directory already contains BLOCKED.md.",
    )
    parser.add_argument(
        "--sandbox",
        choices=("workspace-write", "full-access"),
        default="full-access",
        help="Codex filesystem sandbox. Local runs retain full-access by default.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    urls = args.urls or URLS
    if args.max_urls is not None:
        if args.max_urls < 1:
            raise SystemExit("--max-urls must be at least 1")
        urls = urls[:args.max_urls]
    workspace = args.workspace.resolve()

    if args.dry_run:
        for url in urls:
            crawler_dir = workspace / CRAWLERS_DIR / country_code_for_url(url).lower() / crawler_folder_name(url)
            action = crawler_status(crawler_dir)
            print(f"{action} {url} -> {crawler_dir.relative_to(workspace)}")
        return

    results = []
    sandbox = Sandbox.workspace_write if args.sandbox == "workspace-write" else Sandbox.full_access
    with Codex(
        CodexConfig(codex_bin=os.getenv("CODEX_BIN"), cwd=str(workspace))
    ) as codex:
        for url in urls:
            try:
                result = build_crawler(
                    codex,
                    url,
                    workspace,
                    args.retry_blocked,
                    sandbox,
                )
            except Exception as exc:
                result = {
                    "url": url,
                    "status": "generation_failed",
                    "crawler_directory": None,
                    "final_response": None,
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                }
                results.append(result)
                print(f"FAILED {url}: {result['error']}")
                if not args.continue_on_error:
                    break
            else:
                results.append(result)
                if result["final_response"]:
                    print(f"\nCodex final response for {url}:\n{result['final_response']}\n")

    if args.results:
        args.results.parent.mkdir(parents=True, exist_ok=True)
        args.results.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")

    if any(result["status"] == "generation_failed" for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

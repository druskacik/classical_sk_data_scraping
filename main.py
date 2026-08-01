import schedule
import time
import importlib
import os
from pathlib import Path

from deployment.scraper_updater import ScraperUpdater, UpdaterConfig


RUN_JOBS_ON_STARTUP_ENV = "RUN_JOBS_ON_STARTUP"


def should_run_jobs_on_startup():
    """Return whether scheduled jobs should also run immediately on process startup."""
    return os.getenv(RUN_JOBS_ON_STARTUP_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def discover_crawler_modules():
    """Discover and import all crawler main functions"""
    crawler_modules = []
    crawlers_dir = Path(__file__).parent / 'crawlers'
    
    for main_file in sorted(crawlers_dir.glob('*/*/main.py')):
        parts = main_file.relative_to(crawlers_dir).with_suffix('').parts
        module_path = 'crawlers.' + '.'.join(parts)
        try:
            module = importlib.import_module(module_path)
            if hasattr(module, 'main'):
                crawler_modules.append(module.main)
        except ImportError as e:
            print(f"Failed to import {module_path}: {e}")
    
    return crawler_modules

def run_job(main_function):
    try:
        main_function()
    except Exception as e:
        print(f"Error running {main_function.__name__}: {e}")

if __name__ == "__main__":
    # Discover all crawler modules
    crawler_functions = discover_crawler_modules()
    
    # Add the analyzer function
    from analyzers.analyze_potential_events import main as analyze_potential_events_main
    from analyzers.analyze_concert_programs import scheduled_main as analyze_concert_programs_main
    crawler_functions.append(analyze_potential_events_main)
    crawler_functions.append(analyze_concert_programs_main)
    updater = ScraperUpdater(UpdaterConfig.from_environment())
    
    if should_run_jobs_on_startup():
        print(f"{RUN_JOBS_ON_STARTUP_ENV} is enabled; running all jobs immediately...")
        updater.begin_daily_pipeline()
        try:
            for func in crawler_functions:
                run_job(func)
        finally:
            updater.finish_daily_pipeline()
    else:
        print(f"Skipping immediate job run; set {RUN_JOBS_ON_STARTUP_ENV}=true to enable it.")
    
    print("Scheduling crawlers...")
    schedule.every().day.at("00:00").do(updater.begin_daily_pipeline)
    # Schedule all crawlers to run at 2-minute intervals starting at 00:01.
    for i, func in enumerate(crawler_functions):
        n = 1 + 2*i
        start_minute = n % 60
        start_hour = n // 60
        schedule_time = f"{start_hour:02d}:{start_minute:02d}"  # Format: 01:00, 01:02, etc.
        schedule.every().day.at(schedule_time).do(lambda f=func: run_job(f))

    final_job_number = 1 + 2 * len(crawler_functions)
    final_time = f"{final_job_number // 60:02d}:{final_job_number % 60:02d}"
    schedule.every().day.at(final_time).do(updater.finish_daily_pipeline)
    schedule.every(5).minutes.do(updater.check_for_update)
    
    while True:
        schedule.run_pending()
        time.sleep(1)

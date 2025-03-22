import schedule
import time
import importlib
import os
from pathlib import Path

def discover_crawler_modules():
    """Discover and import all crawler main functions"""
    crawler_modules = []
    crawlers_dir = Path(__file__).parent / 'crawlers'
    
    # Walk through all subdirectories in crawlers/
    for crawler_dir in crawlers_dir.iterdir():
        if crawler_dir.is_dir() and not crawler_dir.name.startswith('__'):
            # Import the main module from each crawler
            module_path = f"crawlers.{crawler_dir.name}.main"
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
    crawler_functions.append(analyze_potential_events_main)
    
    print('Running crawlers...')
    # Run all crawlers initially
    for func in crawler_functions:
        run_job(func)
    
    print("Scheduling crawlers...")
    # Schedule all crawlers to run at 1-minute intervals starting at 01:00
    for i, func in enumerate(crawler_functions):
        schedule_time = f"01:{i:02d}"  # Format: 01:00, 01:01, etc.
        schedule.every().day.at(schedule_time).do(lambda f=func: run_job(f))
    
    while True:
        schedule.run_pending()
        time.sleep(1)

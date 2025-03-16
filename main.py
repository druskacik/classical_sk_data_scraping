
import schedule
import time

from crawlers.filharmonia_sk.main import main as filharmonia_sk_main
from crawlers.konvergencie_sk.main import main as konvergencie_sk_main
from crawlers.sfk_sk.main import main as sfk_sk_main
from crawlers.snd_sk.main import main as snd_sk_main

def run_crawler(main_function):
    try:
        main_function()
    except Exception as e:
        print(f"Error running {main_function.__name__}: {e}")

if __name__ == "__main__":
    
    print('Running crawlers...')
    
    run_crawler(filharmonia_sk_main)
    run_crawler(konvergencie_sk_main)
    run_crawler(sfk_sk_main)
    run_crawler(snd_sk_main)
    
    print("Sheduling crawlers...")
    
    schedule.every().minute.do(run_crawler, filharmonia_sk_main)
    schedule.every().minute.do(run_crawler, konvergencie_sk_main)
    schedule.every().minute.do(run_crawler, sfk_sk_main)
    schedule.every().minute.do(run_crawler, snd_sk_main)
    
    # schedule.every().day.at("01:00").do(filharmonia_sk_main)
    # schedule.every().day.at("01:01").do(konvergencie_sk_main)
    # schedule.every().day.at("01:02").do(sfk_sk_main)
    # schedule.every().day.at("01:03").do(snd_sk_main)
    
    while True:
        schedule.run_pending()
        time.sleep(1)

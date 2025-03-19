
import schedule
import time

from crawlers.filharmonia_sk.main import main as filharmonia_sk_main
from crawlers.konvergencie_sk.main import main as konvergencie_sk_main
from crawlers.sfk_sk.main import main as sfk_sk_main
from crawlers.snd_sk.main import main as snd_sk_main
from crawlers.skozilina_sk.main import main as skozilina_sk_main
from crawlers.stateopera_sk.main import main as stateopera_sk_main
from crawlers.kpvh_sk.main import main as kpvh_sk_main
from crawlers.sdke_sk.main import main as sdke_sk_main
from crawlers.ticketportal_sk.main import main as ticketportal_sk_main
from crawlers.simachart_weebly_com.main import main as simachart_weebly_com_main
from crawlers.toottoot_fm.main import main as toottoot_fm_main
from crawlers.goout_net.main import main as goout_net_main

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
    run_crawler(skozilina_sk_main)
    run_crawler(stateopera_sk_main)
    run_crawler(kpvh_sk_main)
    run_crawler(sdke_sk_main)
    run_crawler(ticketportal_sk_main)
    run_crawler(simachart_weebly_com_main)
    run_crawler(toottoot_fm_main)
    run_crawler(goout_net_main)
    print("Sheduling crawlers...")
    
    schedule.every().day.at("01:00").do(filharmonia_sk_main)
    schedule.every().day.at("01:01").do(konvergencie_sk_main)
    schedule.every().day.at("01:02").do(sfk_sk_main)
    schedule.every().day.at("01:03").do(snd_sk_main)
    schedule.every().day.at("01:04").do(skozilina_sk_main)
    schedule.every().day.at("01:05").do(stateopera_sk_main)
    schedule.every().day.at("01:06").do(kpvh_sk_main)
    schedule.every().day.at("01:07").do(sdke_sk_main)
    schedule.every().day.at("01:08").do(ticketportal_sk_main)
    schedule.every().day.at("01:09").do(simachart_weebly_com_main)
    schedule.every().day.at("01:10").do(toottoot_fm_main)
    schedule.every().day.at("01:11").do(goout_net_main)
    
    while True:
        schedule.run_pending()
        time.sleep(1)

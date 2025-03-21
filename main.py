
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
from crawlers.tootoot_fm.main import main as tootoot_fm_main
from crawlers.goout_net.main import main as goout_net_main
from crawlers.pkopresov_sk.main import main as pkopresov_sk_main

from analyzers.analyze_potential_events import main as analyze_potential_events_main

def run_job(main_function):
    try:
        main_function()
    except Exception as e:
        print(f"Error running {main_function.__name__}: {e}")

if __name__ == "__main__":
    
    print('Running crawlers...')
    
    run_job(filharmonia_sk_main)
    run_job(konvergencie_sk_main)
    run_job(sfk_sk_main)
    run_job(snd_sk_main)
    run_job(skozilina_sk_main)
    run_job(stateopera_sk_main)
    run_job(kpvh_sk_main)
    run_job(sdke_sk_main)
    run_job(ticketportal_sk_main)
    run_job(simachart_weebly_com_main)
    run_job(tootoot_fm_main)
    run_job(goout_net_main)
    run_job(pkopresov_sk_main)
    run_job(analyze_potential_events_main)
    
    print("Sheduling crawlers...")
    
    schedule.every().day.at("01:00").do(lambda: run_job(filharmonia_sk_main))
    schedule.every().day.at("01:01").do(lambda: run_job(konvergencie_sk_main))
    schedule.every().day.at("01:02").do(lambda: run_job(sfk_sk_main))
    schedule.every().day.at("01:03").do(lambda: run_job(snd_sk_main))
    schedule.every().day.at("01:04").do(lambda: run_job(skozilina_sk_main))
    schedule.every().day.at("01:05").do(lambda: run_job(stateopera_sk_main))
    schedule.every().day.at("01:06").do(lambda: run_job(kpvh_sk_main))
    schedule.every().day.at("01:07").do(lambda: run_job(sdke_sk_main))
    schedule.every().day.at("01:08").do(lambda: run_job(ticketportal_sk_main))
    schedule.every().day.at("01:09").do(lambda: run_job(simachart_weebly_com_main))
    schedule.every().day.at("01:10").do(lambda: run_job(tootoot_fm_main))
    schedule.every().day.at("01:11").do(lambda: run_job(goout_net_main))
    schedule.every().day.at("01:12").do(lambda: run_job(pkopresov_sk_main))
    schedule.every().day.at("01:13").do(lambda: run_job(analyze_potential_events_main))
    
    while True:
        schedule.run_pending()
        time.sleep(1)
